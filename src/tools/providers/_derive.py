"""Pure financial derivations shared by Phase 3 (line items) and Phase 4 (metrics).

Source APIs (Polygon vX, SEC companyfacts) give *raw* statement lines. Financial
Datasets additionally returns derived items/ratios. We compute those here from
raw inputs + price, rather than depending on a provider for them.

Every function returns ``None`` when a required input is missing — it never
fabricates a number. Items marked APPROX use a documented simplification (the
exact FD definition needs inputs the free sources don't expose, e.g. tax rate
or short-term debt); these are the fields Phase 4 may flag as non-parity.
"""

from __future__ import annotations

from typing import Optional

Num = Optional[float]


def _div(a: Num, b: Num) -> Num:
    if a is None or b is None:
        return None
    try:
        b = float(b)
        if b == 0:
            return None
        return float(a) / b
    except (TypeError, ValueError):
        return None


def _sub(a: Num, b: Num) -> Num:
    if a is None or b is None:
        return None
    return float(a) - float(b)


def _growth(cur: Num, prev: Num) -> Num:
    if cur is None or prev is None:
        return None
    try:
        denom = abs(float(prev))
        if denom == 0:
            return None
        return (float(cur) - float(prev)) / denom
    except (TypeError, ValueError):
        return None


def enrich_raw(rec: dict) -> dict:
    """Add derived *line items* to a raw statement dict (in place) and return it."""
    g = rec.get
    rec["working_capital"] = _sub(g("current_assets"), g("current_liabilities"))
    rec["total_debt"] = g("_long_term_debt")  # APPROX: long-term only (no ST debt key)
    rec["ebit"] = g("operating_income")  # APPROX: operating income as EBIT proxy
    da = g("depreciation_and_amortization")
    rec["ebitda"] = (rec["ebit"] + da) if (rec.get("ebit") is not None and da is not None) else None
    ocf, capex = g("_operating_cash_flow"), g("capital_expenditure")
    rec["free_cash_flow"] = _sub(ocf, capex) if (ocf is not None and capex is not None) else None
    rec["gross_margin"] = _div(g("gross_profit"), g("revenue"))
    rec["operating_margin"] = _div(g("operating_income"), g("revenue"))
    rec["debt_to_equity"] = _div(rec.get("total_debt"), g("shareholders_equity"))
    rec["book_value_per_share"] = _div(g("shareholders_equity"), g("outstanding_shares"))
    # APPROX ROIC: operating_income / (total_debt + equity); no NOPAT/tax adj.
    invested = None
    if rec.get("total_debt") is not None and g("shareholders_equity") is not None:
        invested = float(rec["total_debt"]) + float(g("shareholders_equity"))
    rec["return_on_invested_capital"] = _div(g("operating_income"), invested)
    return rec


# FinancialMetrics fields we can compute; the rest stay None (flagged in Phase 4).
METRIC_FIELDS = (
    "market_cap",
    "enterprise_value",
    "price_to_earnings_ratio",
    "price_to_book_ratio",
    "price_to_sales_ratio",
    "enterprise_value_to_ebitda_ratio",
    "enterprise_value_to_revenue_ratio",
    "free_cash_flow_yield",
    "peg_ratio",
    "gross_margin",
    "operating_margin",
    "net_margin",
    "return_on_equity",
    "return_on_assets",
    "return_on_invested_capital",
    "asset_turnover",
    "inventory_turnover",
    "receivables_turnover",
    "days_sales_outstanding",
    "operating_cycle",
    "working_capital_turnover",
    "current_ratio",
    "quick_ratio",
    "cash_ratio",
    "operating_cash_flow_ratio",
    "debt_to_equity",
    "debt_to_assets",
    "interest_coverage",
    "revenue_growth",
    "earnings_growth",
    "book_value_growth",
    "earnings_per_share_growth",
    "free_cash_flow_growth",
    "operating_income_growth",
    "ebitda_growth",
    "payout_ratio",
    "earnings_per_share",
    "book_value_per_share",
    "free_cash_flow_per_share",
)


# Period-over-period growth fields (subset of METRIC_FIELDS). Used to overlay
# YoY growth onto a TTM snapshot, which has no prior period of its own.
GROWTH_FIELDS = (
    "revenue_growth",
    "earnings_growth",
    "book_value_growth",
    "earnings_per_share_growth",
    "free_cash_flow_growth",
    "operating_income_growth",
    "ebitda_growth",
)


def compute_metrics(cur: dict, prev: Optional[dict], price: Num, market_cap: Num) -> dict:
    """Build a FinancialMetrics-shaped dict from a raw record (+ prior for growth)."""
    g = cur.get
    rev, ni = g("revenue"), g("net_income")
    eq, ta = g("shareholders_equity"), g("total_assets")
    eps, shares = g("earnings_per_share"), g("outstanding_shares")
    fcf = g("free_cash_flow")
    debt = g("total_debt")

    m = {f: None for f in METRIC_FIELDS}
    m["market_cap"] = float(market_cap) if market_cap is not None else None
    m["earnings_per_share"] = eps
    m["book_value_per_share"] = g("book_value_per_share")
    m["gross_margin"] = g("gross_margin")
    m["operating_margin"] = g("operating_margin")
    m["net_margin"] = _div(ni, rev)
    m["return_on_equity"] = _div(ni, eq)
    m["return_on_assets"] = _div(ni, ta)
    m["return_on_invested_capital"] = g("return_on_invested_capital")
    m["debt_to_equity"] = g("debt_to_equity")
    m["debt_to_assets"] = _div(debt, ta)
    m["current_ratio"] = _div(g("current_assets"), g("current_liabilities"))
    m["cash_ratio"] = _div(g("cash_and_equivalents"), g("current_liabilities"))
    m["asset_turnover"] = _div(rev, ta)
    if shares:
        m["free_cash_flow_per_share"] = _div(fcf, shares)
    if price is not None:
        m["price_to_earnings_ratio"] = _div(price, eps)
        m["price_to_book_ratio"] = _div(price, g("book_value_per_share"))
        if shares and rev:
            m["price_to_sales_ratio"] = _div(market_cap, rev)
    if market_cap is not None:
        ev = float(market_cap) + (float(debt) if debt is not None else 0.0) - (float(g("cash_and_equivalents")) if g("cash_and_equivalents") is not None else 0.0)
        m["enterprise_value"] = ev
        m["enterprise_value_to_revenue_ratio"] = _div(ev, rev)
        m["enterprise_value_to_ebitda_ratio"] = _div(ev, g("ebitda"))
        m["free_cash_flow_yield"] = _div(fcf, market_cap)

    if prev is not None:
        m["revenue_growth"] = _growth(rev, prev.get("revenue"))
        m["earnings_growth"] = _growth(ni, prev.get("net_income"))
        m["earnings_per_share_growth"] = _growth(eps, prev.get("earnings_per_share"))
        m["book_value_growth"] = _growth(eq, prev.get("shareholders_equity"))
        m["free_cash_flow_growth"] = _growth(fcf, prev.get("free_cash_flow"))
        m["operating_income_growth"] = _growth(g("operating_income"), prev.get("operating_income"))
        m["ebitda_growth"] = _growth(g("ebitda"), prev.get("ebitda"))
    return m
