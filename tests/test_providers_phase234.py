"""Unit tests for Phase 2-4 providers (no network).

HTTP is monkeypatched. These lock the pure mapping/derivation math, which is
the highest-risk part of the migration; live API parity is validated
separately on infra via scripts/.
"""

import pytest

from src.tools.providers import _derive

# --------------------------------------------------------------------------
# _derive — pure math
# --------------------------------------------------------------------------


def test_enrich_raw_derivations():
    rec = {
        "revenue": 1000.0,
        "gross_profit": 400.0,
        "operating_income": 250.0,
        "net_income": 200.0,
        "current_assets": 600.0,
        "current_liabilities": 300.0,
        "shareholders_equity": 500.0,
        "_long_term_debt": 250.0,
        "_operating_cash_flow": 300.0,
        "capital_expenditure": 50.0,
        "depreciation_and_amortization": 30.0,
        "outstanding_shares": 100.0,
    }
    out = _derive.enrich_raw(dict(rec))
    assert out["working_capital"] == 300.0
    assert out["total_debt"] == 250.0
    assert out["ebit"] == 250.0
    assert out["ebitda"] == 280.0  # ebit + D&A
    assert out["free_cash_flow"] == 250.0  # OCF - capex
    assert out["gross_margin"] == 0.4
    assert out["operating_margin"] == 0.25
    assert out["debt_to_equity"] == 0.5
    assert out["book_value_per_share"] == 5.0
    assert out["return_on_invested_capital"] == pytest.approx(250.0 / 750.0)


def test_enrich_raw_missing_inputs_are_none():
    out = _derive.enrich_raw({"revenue": 1000.0})  # no profit/equity/cash-flow inputs
    assert out["free_cash_flow"] is None
    assert out["ebitda"] is None
    assert out["gross_margin"] is None
    assert out["book_value_per_share"] is None


def test_compute_metrics_growth_and_ratios():
    cur = _derive.enrich_raw(
        {
            "revenue": 1200.0,
            "net_income": 240.0,
            "total_assets": 2000.0,
            "shareholders_equity": 800.0,
            "earnings_per_share": 2.4,
            "outstanding_shares": 100.0,
            "_long_term_debt": 400.0,
            "current_assets": 700.0,
            "current_liabilities": 350.0,
            "cash_and_equivalents": 150.0,
            "operating_income": 300.0,
            "_operating_cash_flow": 320.0,
            "capital_expenditure": 20.0,
        }
    )
    prev = _derive.enrich_raw({"revenue": 1000.0, "net_income": 200.0, "earnings_per_share": 2.0, "shareholders_equity": 700.0, "operating_income": 250.0})
    m = _derive.compute_metrics(cur, prev, price=48.0, market_cap=4800.0)
    assert m["net_margin"] == pytest.approx(0.2)
    assert m["return_on_equity"] == pytest.approx(0.3)
    assert m["return_on_assets"] == pytest.approx(0.12)
    assert m["price_to_earnings_ratio"] == pytest.approx(20.0)
    assert m["revenue_growth"] == pytest.approx(0.2)
    assert m["earnings_growth"] == pytest.approx(0.2)
    assert m["enterprise_value"] == pytest.approx(4800.0 + 400.0 - 150.0)
    # Field that needs inputs free sources lack -> intentionally None
    assert m["inventory_turnover"] is None


# --------------------------------------------------------------------------
# Polygon provider — news + line items (HTTP mocked)
# --------------------------------------------------------------------------


def test_polygon_news_maps_sentiment(monkeypatch):
    from src.tools.providers import polygon as pol

    payload = {
        "results": [
            {
                "title": "Big news",
                "article_url": "http://x/1",
                "published_utc": "2024-03-02T12:00:00Z",
                "author": "Jane",
                "publisher": {"name": "Reuters"},
                "insights": [{"ticker": "AAPL", "sentiment": "positive"}],
            }
        ]
    }
    monkeypatch.setattr(pol, "get_json", lambda *a, **k: payload)
    monkeypatch.setenv("POLYGON_API_KEY", "k")
    news = pol.PolygonProvider().get_company_news("AAPL", "2024-03-31", start_date="2024-03-01")
    assert len(news) == 1
    n = news[0]
    assert n.sentiment == "positive" and n.source == "Reuters" and n.date == "2024-03-02"


def test_polygon_line_items_mapping(monkeypatch):
    from src.tools.providers import polygon as pol

    payload = {
        "results": [
            {
                "end_date": "2023-12-31",
                "financials": {
                    "income_statement": {
                        "revenues": {"value": 1000.0},
                        "gross_profit": {"value": 400.0},
                        "operating_income_loss": {"value": 250.0},
                        "net_income_loss": {"value": 200.0},
                    },
                    "balance_sheet": {
                        "assets": {"value": 5000.0},
                        "equity_attributable_to_parent": {"value": 2000.0},
                    },
                    "cash_flow_statement": {},
                },
            }
        ]
    }
    monkeypatch.setattr(pol, "get_json", lambda *a, **k: payload)
    monkeypatch.setenv("POLYGON_API_KEY", "k")
    items = pol.PolygonProvider().search_line_items("AAPL", ["revenue", "gross_profit", "operating_margin", "total_assets", "annual"], "2024-01-01", period="annual")
    assert len(items) == 1
    li = items[0]
    assert li.revenue == 1000.0
    assert li.gross_profit == 400.0
    assert li.operating_margin == 0.25  # derived
    assert li.total_assets == 5000.0
    assert li.annual is True


# --------------------------------------------------------------------------
# SEC provider — companyfacts mapping (HTTP mocked)
# --------------------------------------------------------------------------


def test_sec_companyfacts_records(monkeypatch):
    from src.tools.providers import sec_edgar as sec

    monkeypatch.setattr(sec, "_cik_cache", {"AAPL": "0000320193"})
    facts = {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {"end": "2023-12-31", "val": 1000.0, "form": "10-K", "filed": "2024-02-01"},
                            {"end": "2023-12-31", "val": 999.0, "form": "10-K", "filed": "2024-01-15"},  # older -> ignored
                        ]
                    }
                },
                "NetIncomeLoss": {
                    "units": {
                        "USD": [
                            {"end": "2023-12-31", "val": 200.0, "form": "10-K", "filed": "2024-02-01"},
                        ]
                    }
                },
            }
        }
    }
    monkeypatch.setattr(sec, "get_json", lambda url, **k: facts if "companyfacts" in url else None)
    recs = sec.SECEdgarProvider()._records("AAPL", "2024-06-01", "annual", 10)
    assert len(recs) == 1
    assert recs[0]["revenue"] == 1000.0  # most-recent filing wins over restated older one
    assert recs[0]["net_income"] == 200.0


def test_sec_form4_parse(monkeypatch):
    from src.tools.providers import sec_edgar as sec

    xml = b"""<?xml version="1.0"?>
    <ownershipDocument>
      <issuer><issuerName>Apple Inc</issuerName></issuer>
      <reportingOwner>
        <reportingOwnerId><rptOwnerName>Cook Tim</rptOwnerName></reportingOwnerId>
        <reportingOwnerRelationship><isDirector>1</isDirector><officerTitle>CEO</officerTitle></reportingOwnerRelationship>
      </reportingOwner>
      <nonDerivativeTransaction>
        <securityTitle><value>Common Stock</value></securityTitle>
        <transactionDate><value>2024-03-01</value></transactionDate>
        <transactionAmounts>
          <transactionShares><value>100</value></transactionShares>
          <transactionPricePerShare><value>180</value></transactionPricePerShare>
          <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
        </transactionAmounts>
        <postTransactionAmounts><sharesOwnedFollowingTransaction><value>900</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
      </nonDerivativeTransaction>
    </ownershipDocument>"""

    class _R:
        status_code = 200
        content = xml

    monkeypatch.setattr(sec.requests, "get", lambda *a, **k: _R())
    trades = sec.SECEdgarProvider()._parse_form4("http://x", "AAPL", "2024-03-04")
    assert len(trades) == 1
    t = trades[0]
    assert t.name == "Cook Tim" and t.title == "CEO" and t.is_board_director is True
    assert t.transaction_shares == -100.0  # disposed -> negative
    assert t.transaction_value == pytest.approx(18000.0)
    assert t.shares_owned_after_transaction == 900.0


def test_sec_insider_strips_xsl_viewer_path(monkeypatch):
    """Regression: primaryDocument is the XSL HTML viewer; the raw
    ownershipDocument XML is the same basename at the accession root."""
    from src.tools.providers import sec_edgar as sec

    monkeypatch.setattr(sec, "_cik_cache", {"AAPL": "0000320193"})
    subs = {
        "filings": {
            "recent": {
                "form": ["4"],
                "filingDate": ["2024-03-04"],
                "accessionNumber": ["0001140361-24-000123"],
                "primaryDocument": ["xslF345X06/form4.xml"],
            }
        }
    }
    monkeypatch.setattr(sec, "get_json", lambda url, **k: subs if "submissions" in url else None)
    seen = {}
    monkeypatch.setattr(
        sec.SECEdgarProvider, "_parse_form4",
        lambda self, url, t, d: seen.setdefault("url", url) or [],
    )
    sec.SECEdgarProvider().get_insider_trades("AAPL", "2026-05-15", start_date="2024-01-01")
    assert seen["url"].endswith("/000114036124000123/form4.xml")
    assert "xslF345X06" not in seen["url"]


# --------------------------------------------------------------------------
# Metrics provider — composition (raw fetch + price mocked)
# --------------------------------------------------------------------------


def test_metrics_provider_composes(monkeypatch):
    from src.tools.providers import metrics as met
    from src.tools.providers.polygon import PolygonProvider

    rows = [
        _derive.enrich_raw({"report_period": "2023-12-31", "currency": "USD", "revenue": 1200.0, "net_income": 240.0, "total_assets": 2000.0, "shareholders_equity": 800.0, "earnings_per_share": 2.4, "outstanding_shares": 100.0}),
        _derive.enrich_raw({"report_period": "2022-12-31", "currency": "USD", "revenue": 1000.0, "net_income": 200.0, "shareholders_equity": 700.0, "earnings_per_share": 2.0}),
    ]
    monkeypatch.setattr(PolygonProvider, "fetch_financials", lambda self, *a, **k: rows)

    class _Internal:
        def get_prices(self, *a, **k):
            class P:  # minimal price stub
                close = 48.0

            return [P()]

        def get_market_cap(self, *a, **k):
            return 4800.0

    monkeypatch.setattr("src.tools.providers.internal.InternalProvider", _Internal)
    out = met.MetricsProvider().get_financial_metrics("AAPL", "2024-01-01", period="annual", limit=2)
    assert len(out) == 2
    assert out[0].ticker == "AAPL"
    assert out[0].return_on_equity == pytest.approx(0.3)
    assert out[0].revenue_growth == pytest.approx(0.2)
