"""Offline contract tests for the internal (warehouse-backed) DataClient.

No DB required: shared-layer functions are monkeypatched. Mirrors
test_client_contract.py's role for FDClient — verifies error semantics,
model mapping, and point-in-time filtering rather than live data.
"""

from __future__ import annotations

import pandas as pd
import pytest

import v2.data.internal as internal
from v2.data.internal import (
    _build_earnings_records,
    _classify_surprise,
    _match_report_period,
    _prev_quarter_end,
    InternalClient,
    InternalClientError,
)
from v2.data.protocol import DataClient


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("QUANTAI_MARKETDATA_DB_URL", "postgresql://test/unit")
    return InternalClient()


# ---------------------------------------------------------------------------
# Construction / protocol conformance
# ---------------------------------------------------------------------------


def test_requires_dsn(monkeypatch):
    monkeypatch.delenv("QUANTAI_MARKETDATA_DB_URL", raising=False)
    with pytest.raises(InternalClientError):
        InternalClient()


def test_satisfies_data_client_protocol(client):
    assert isinstance(client, DataClient)


def test_context_manager(client):
    with client as c:
        assert c is client


# ---------------------------------------------------------------------------
# get_prices — mapping + fail-loud
# ---------------------------------------------------------------------------


def _fake_bars():
    return pd.DataFrame(
        {
            "date": ["2025-08-04", "2025-08-05"],
            "open": [100.0, 101.0],
            "high": [102.0, 103.0],
            "low": [99.0, 100.0],
            "close": [101.0, 102.0],
            "adj_close": [101.0, 102.0],
            "volume": [1_000_000.0, float("nan")],
        }
    )


def test_get_prices_maps_bars(client, monkeypatch):
    seen = {}

    def fake_range(symbol, start, end, **kwargs):
        seen.update(kwargs, symbol=symbol)
        return _fake_bars()

    monkeypatch.setattr(internal._qmd, "get_daily_range", fake_range)
    prices = client.get_prices("aapl", "2025-08-04", "2025-08-05")
    assert seen["symbol"] == "AAPL"
    assert seen["adjust"] == "split_asof"  # mandatory point-in-time basis
    assert [p.time for p in prices] == ["2025-08-04T00:00:00Z", "2025-08-05T00:00:00Z"]
    assert prices[0].volume == 1_000_000
    assert prices[1].volume == 0  # NaN volume sanitised
    assert prices[1].close == 102.0


def test_get_prices_empty_frame_means_absent(client, monkeypatch):
    monkeypatch.setattr(internal._qmd, "get_daily_range", lambda *a, **k: pd.DataFrame())
    assert client.get_prices("AAPL", "2025-01-01", "2025-01-02") == []


def test_get_prices_infra_failure_raises(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("raw price columns not backfilled")

    monkeypatch.setattr(internal._qmd, "get_daily_range", boom)
    with pytest.raises(InternalClientError):
        client.get_prices("AAPL", "2025-01-01", "2025-01-02")


# ---------------------------------------------------------------------------
# get_financial_metrics — point-in-time on filed_at
# ---------------------------------------------------------------------------


def test_metrics_point_in_time_filtering(client, monkeypatch):
    rows = [
        {"report_period": "2025-06-30", "period": "quarterly", "currency": "USD", "filed_at": "2025-08-01", "revenue": 100.0, "net_income": 10.0},
        {"report_period": "2025-03-31", "period": "quarterly", "currency": "USD", "filed_at": "2025-05-02", "revenue": 90.0, "net_income": 9.0},
        {"report_period": "2024-12-31", "period": "quarterly", "currency": "USD", "filed_at": None, "revenue": 80.0},  # undated → excluded
    ]
    monkeypatch.setattr(internal._fund, "read_financials", lambda *a, **k: rows)

    # As of 2025-06-30 the Q2 filing (2025-08-01) was not yet public
    metrics = client.get_financial_metrics("AAPL", "2025-06-30", period="quarterly")
    assert [m.report_period for m in metrics] == ["2025-03-31"]
    assert metrics[0].filing_date == "2025-05-02"
    assert metrics[0].net_margin == pytest.approx(0.1)


def test_metrics_infra_failure_raises(client, monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("db down")

    monkeypatch.setattr(internal._fund, "read_financials", boom)
    with pytest.raises(InternalClientError):
        client.get_financial_metrics("AAPL", "2025-06-30")


# ---------------------------------------------------------------------------
# get_earnings_history — events → EarningsRecord
# ---------------------------------------------------------------------------


def _event(announce, est, act):
    return {"announce_at": announce, "eps_estimate": est, "eps_actual": act, "surprise_pct": None}


def test_earnings_history_end_to_end(client, monkeypatch):
    events = [
        _event("2025-10-30 16:00:00-04:00", 1.77, None),  # scheduled, unreported → dropped
        _event("2025-07-31 16:00:00-04:00", 1.43, 1.57),  # BEAT
        _event("2025-05-01 16:00:00-04:00", 1.65, 1.62),  # MISS
    ]
    quarters = [
        {"report_period": "2025-06-28"},
        {"report_period": "2025-03-29"},
    ]
    monkeypatch.setattr(internal._fund, "read_earnings_events", lambda *a, **k: events)
    monkeypatch.setattr(internal._fund, "read_financials", lambda *a, **k: quarters)

    records = client.get_earnings_history("AAPL", limit=8)
    assert len(records) == 2
    beat, miss = records
    assert beat.filing_date == "2025-07-31"
    assert beat.report_period == "2025-06-28"  # matched to known quarter end
    assert beat.source_type == "8-K"
    assert beat.quarterly.eps_surprise == "BEAT"
    assert miss.quarterly.eps_surprise == "MISS"
    assert miss.report_period == "2025-03-29"


def test_get_earnings_returns_latest(client, monkeypatch):
    monkeypatch.setattr(internal._fund, "read_earnings_events", lambda *a, **k: [_event("2025-07-31 16:00:00-04:00", 1.43, 1.57)])
    monkeypatch.setattr(internal._fund, "read_financials", lambda *a, **k: [])
    e = client.get_earnings("AAPL")
    assert e is not None
    assert e.quarterly.eps_surprise == "BEAT"

    monkeypatch.setattr(internal._fund, "read_earnings_events", lambda *a, **k: [])
    assert client.get_earnings("AAPL") is None


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestClassifySurprise:
    def test_beat_miss_meet(self):
        assert _classify_surprise(2.01, 1.94, 0.005) == "BEAT"
        assert _classify_surprise(1.62, 1.65, 0.005) == "MISS"
        assert _classify_surprise(1.50, 1.50, 0.005) == "MEET"

    def test_epsilon_boundary_is_meet(self):
        assert _classify_surprise(1.505, 1.50, 0.005) == "MEET"
        assert _classify_surprise(1.495, 1.50, 0.005) == "MEET"

    def test_missing_values(self):
        assert _classify_surprise(None, 1.5, 0.005) is None
        assert _classify_surprise(1.5, None, 0.005) is None


class TestPeriodMatching:
    def test_prev_quarter_end(self):
        from datetime import date

        assert _prev_quarter_end(date(2026, 1, 29)) == date(2025, 12, 31)
        assert _prev_quarter_end(date(2026, 4, 30)) == date(2026, 3, 31)
        assert _prev_quarter_end(date(2026, 12, 31)) == date(2026, 9, 30)

    def test_match_prefers_known_quarter(self):
        from datetime import date

        assert _match_report_period(date(2025, 7, 31), ["2025-06-28", "2025-03-29"]) == "2025-06-28"

    def test_match_ignores_stale_quarters_and_falls_back(self):
        from datetime import date

        # Only known quarter is >100 days before the announcement
        assert _match_report_period(date(2025, 7, 31), ["2024-12-28"]) == "2025-06-30"

    def test_match_ignores_future_quarters(self):
        from datetime import date

        assert _match_report_period(date(2025, 7, 31), ["2025-09-27"]) == "2025-06-30"


class TestBuildRecords:
    def test_limit_and_order(self):
        events = [_event(f"2025-0{m}-15 16:00:00-04:00", 1.0, 1.1) for m in (9, 6, 3)]
        records = _build_earnings_records("T", events, [], limit=2, epsilon=0.005)
        assert len(records) == 2
        assert records[0].filing_date > records[1].filing_date  # newest first

    def test_unreported_events_dropped(self):
        records = _build_earnings_records("T", [_event("2025-09-15 16:00:00-04:00", 1.0, None)], [], limit=5, epsilon=0.005)
        assert records == []

    def test_missing_estimate_yields_no_surprise(self):
        records = _build_earnings_records("T", [_event("2025-09-15 16:00:00-04:00", None, 1.1)], [], limit=5, epsilon=0.005)
        assert len(records) == 1
        assert records[0].quarterly.eps_surprise is None
        assert records[0].quarterly.earnings_per_share == 1.1
