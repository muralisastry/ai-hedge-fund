"""Deterministic unit tests for the fundamentals analyst agent (no network).

`src.agents.fundamentals.get_financial_metrics` is monkeypatched with crafted
FinancialMetrics so the rule-based logic — including the None-price-ratio
degradation path that occurs without a prices DB — is locked.
"""

from langchain_core.messages import HumanMessage

from src.data.models import FinancialMetrics


def _metrics(**overrides) -> FinancialMetrics:
    """A FinancialMetrics with every numeric field None unless overridden."""
    base = {f: None for f in FinancialMetrics.model_fields}
    base.update(ticker="AAPL", report_period="2025-09-27", period="ttm", currency="USD")
    base.update(overrides)
    return FinancialMetrics(**base)


def _state(tickers):
    return {
        "messages": [HumanMessage(content="x")],
        "data": {"tickers": tickers, "end_date": "2026-05-15", "analyst_signals": {}},
        "metadata": {"show_reasoning": False},
    }


def test_bullish_with_none_price_ratios(monkeypatch):
    """Strong fundamentals + price ratios None (no prices DB) -> overall bullish.

    Locks the documented behavior: price_ratio_score == 0 makes the
    price-ratios sub-signal *bullish* (not neutral)."""
    from src.agents import fundamentals

    m = _metrics(
        return_on_equity=0.30, net_margin=0.27, operating_margin=0.30,   # profitability bullish
        revenue_growth=0.15, earnings_growth=0.20, book_value_growth=0.12,  # growth bullish
        current_ratio=2.0, debt_to_equity=0.3,
        free_cash_flow_per_share=5.0, earnings_per_share=4.0,            # health bullish
        # price_to_earnings_ratio / price_to_book_ratio / price_to_sales_ratio = None
    )
    monkeypatch.setattr(fundamentals, "get_financial_metrics", lambda **kw: [m])

    out = fundamentals.fundamentals_analyst_agent(_state(["AAPL"]))
    sig = out["data"]["analyst_signals"]["fundamentals_analyst_agent"]["AAPL"]

    assert sig["signal"] == "bullish"
    assert sig["confidence"] == 100.0
    assert set(sig["reasoning"]) == {
        "profitability_signal", "growth_signal",
        "financial_health_signal", "price_ratios_signal",
    }
    assert sig["reasoning"]["price_ratios_signal"]["signal"] == "bullish"
    assert "P/E: N/A" in sig["reasoning"]["price_ratios_signal"]["details"]
    assert out["messages"][0].name == "fundamentals_analyst_agent"


def test_bearish_majority_vote(monkeypatch):
    """Weak profitability/growth/health + overvalued ratios -> bearish."""
    from src.agents import fundamentals

    m = _metrics(
        return_on_equity=0.01, net_margin=0.01, operating_margin=0.01,   # profitability bearish (0)
        revenue_growth=-0.05, earnings_growth=-0.10, book_value_growth=0.0,  # growth bearish (0)
        current_ratio=0.8, debt_to_equity=3.0,
        free_cash_flow_per_share=0.1, earnings_per_share=5.0,            # health bearish (0)
        price_to_earnings_ratio=80.0, price_to_book_ratio=20.0, price_to_sales_ratio=30.0,  # ratios bearish
    )
    monkeypatch.setattr(fundamentals, "get_financial_metrics", lambda **kw: [m])

    out = fundamentals.fundamentals_analyst_agent(_state(["AAPL"]))
    sig = out["data"]["analyst_signals"]["fundamentals_analyst_agent"]["AAPL"]
    assert sig["signal"] == "bearish"
    assert sig["confidence"] == 100.0
    assert sig["reasoning"]["price_ratios_signal"]["signal"] == "bearish"


def test_missing_metrics_skips_ticker(monkeypatch):
    """No metrics for a ticker -> it is absent from the signals (not an error)."""
    from src.agents import fundamentals

    monkeypatch.setattr(fundamentals, "get_financial_metrics", lambda **kw: [])
    out = fundamentals.fundamentals_analyst_agent(_state(["AAPL"]))
    assert out["data"]["analyst_signals"]["fundamentals_analyst_agent"] == {}
