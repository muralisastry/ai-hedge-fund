"""Live smoke tests for the internal DataClient against the shared warehouse.

Runs only when QUANTAI_MARKETDATA_DB_URL is set (mirrors test_client.py's
role for FD). Assumes the quarterly-financials + earnings-events backfill
has run for AAPL.
"""

from __future__ import annotations

import pytest

from v2.conftest import has_warehouse

pytestmark = pytest.mark.skipif(
    not has_warehouse(),
    reason="live internal-provider smoke tests require QUANTAI_MARKETDATA_DB_URL",
)


@pytest.fixture(scope="module")
def client():
    from v2.data import InternalClient

    with InternalClient() as c:
        yield c


def test_prices(client):
    prices = client.get_prices("AAPL", "2025-01-01", "2025-06-30")
    assert len(prices) > 100
    p = prices[0]
    assert p.time.endswith("T00:00:00Z")
    assert p.low <= p.close <= p.high or p.low <= p.open <= p.high


def test_earnings_history(client):
    records = client.get_earnings_history("AAPL", limit=8)
    assert records, "expected earnings events for AAPL (backfill run?)"
    surprises = {r.quarterly.eps_surprise for r in records if r.quarterly}
    assert surprises & {"BEAT", "MISS", "MEET"}
    assert all(r.source_type == "8-K" for r in records)
    assert all(r.filing_date for r in records)


def test_earnings_latest(client):
    e = client.get_earnings("AAPL")
    assert e is not None
    assert e.quarterly is not None


def test_financial_metrics_point_in_time(client):
    metrics = client.get_financial_metrics("AAPL", "2025-06-30", period="annual", limit=4)
    assert metrics
    assert all(m.filing_date and m.filing_date <= "2025-06-30" for m in metrics)
    assert metrics[0].net_margin is not None


def test_news(client):
    news = client.get_news("AAPL", "2026-01-01", limit=10)
    assert isinstance(news, list)  # may be empty if news ingest hasn't run


def test_insider_trades(client):
    trades = client.get_insider_trades("AAPL", "2026-01-01", limit=10)
    assert isinstance(trades, list)


def test_company_facts(client):
    facts = client.get_company_facts("AAPL")
    assert facts is not None
    assert facts.ticker == "AAPL"


def test_market_cap_current_and_historical(client):
    from datetime import date

    assert client.get_market_cap("AAPL", date.today().isoformat())
    assert client.get_market_cap("AAPL", "2024-01-01") is None
