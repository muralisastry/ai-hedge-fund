"""Phase 5: InternalProvider serving fundamentals from the shared layer.

quantai_market_data.fundamentals is replaced with an in-memory fake.
"""

import sys
import types

import pytest


@pytest.fixture
def fake_fundamentals(monkeypatch):
    pkg = types.ModuleType("quantai_market_data")
    fund = types.ModuleType("quantai_market_data.fundamentals")

    fund.read_financials = lambda symbol, period="ttm", limit=10: [
        {"report_period": "2023-12-31", "period": period, "currency": "USD", "revenue": 1200.0, "net_income": 240.0, "total_assets": 2000.0, "shareholders_equity": 800.0, "earnings_per_share": 2.4, "outstanding_shares": 100.0},
        {"report_period": "2022-12-31", "period": period, "currency": "USD", "revenue": 1000.0, "net_income": 200.0, "shareholders_equity": 700.0, "earnings_per_share": 2.0},
    ]
    fund.read_insider_trades = lambda s, st, e, lim: [{"ticker": s, "issuer": "X", "name": "Jane", "title": "CFO", "is_board_director": False, "transaction_date": "2023-11-01", "transaction_shares": -10.0, "transaction_price_per_share": 5.0, "transaction_value": 50.0, "shares_owned_before_transaction": 110.0, "shares_owned_after_transaction": 100.0, "security_title": "Common", "filing_date": "2023-11-03"}]
    fund.read_news = lambda s, st, e, lim: [{"ticker": s, "title": "T", "author": None, "source": "Reuters", "date": "2023-10-10", "url": "http://x", "sentiment": "positive"}]
    pkg.fundamentals = fund
    monkeypatch.setitem(sys.modules, "quantai_market_data", pkg)
    monkeypatch.setitem(sys.modules, "quantai_market_data.fundamentals", fund)


def test_internal_line_items_from_shared(fake_fundamentals):
    from src.tools.providers.internal import InternalProvider

    items = InternalProvider().search_line_items("AAPL", ["revenue", "net_income", "working_capital", "annual"], "2024-01-01", period="annual")
    assert len(items) == 2
    assert items[0].revenue == 1200.0
    assert items[0].annual is True


def test_internal_metrics_from_shared(fake_fundamentals, monkeypatch):
    from src.tools.providers.internal import InternalProvider

    prov = InternalProvider()
    monkeypatch.setattr(prov, "get_prices", lambda *a, **k: [])
    monkeypatch.setattr(prov, "get_market_cap", lambda *a, **k: 4800.0)
    m = prov.get_financial_metrics("AAPL", "2024-01-01", period="annual", limit=2)
    assert len(m) == 2
    assert m[0].return_on_equity == pytest.approx(0.3)
    assert m[0].revenue_growth == pytest.approx(0.2)


def test_internal_insider_and_news_from_shared(fake_fundamentals):
    from src.tools.providers.internal import InternalProvider

    prov = InternalProvider()
    trades = prov.get_insider_trades("AAPL", "2024-01-01", start_date="2023-01-01")
    assert trades[0].name == "Jane" and trades[0].transaction_shares == -10.0
    news = prov.get_company_news("AAPL", "2024-01-01", start_date="2023-01-01")
    assert news[0].sentiment == "positive" and news[0].source == "Reuters"
