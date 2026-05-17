"""Polygon provider.

- ``get_company_news``  — Polygon Ticker News (carries Benzinga-style sentiment)
- ``fetch_financials`` — normalized raw statement values from Polygon's
  ``/vX/reference/financials`` (XBRL extracted from SEC 10-K/10-Q)
- ``search_line_items`` — maps Polygon raw values to the FD line-item names the
  agents request, deriving the items Polygon doesn't expose directly

Requires ``POLYGON_API_KEY``. Already part of the suite subscription, so this
is $0 marginal cost. Returns empty on any failure so the router can fall back.
"""

from __future__ import annotations

import logging
import os

from src.data.models import CompanyNews, LineItem
from src.tools.providers import _derive
from src.tools.providers._http import get_json

logger = logging.getLogger(__name__)

_BASE = "https://api.polygon.io"

# Polygon canonical statement key -> our raw line-item name.
# (Derived items — ebit, ebitda, free_cash_flow, working_capital, total_debt,
#  margins, ratios — are computed in _derive, not mapped here.)
_INCOME = {
    "revenues": "revenue",
    "gross_profit": "gross_profit",
    "operating_expenses": "operating_expense",
    "operating_income_loss": "operating_income",
    "net_income_loss": "net_income",
    "research_and_development": "research_and_development",
    "interest_expense_operating": "interest_expense",
    "basic_earnings_per_share": "earnings_per_share",
    "diluted_average_shares": "outstanding_shares",
}
_BALANCE = {
    "assets": "total_assets",
    "current_assets": "current_assets",
    "liabilities": "total_liabilities",
    "current_liabilities": "current_liabilities",
    "equity_attributable_to_parent": "shareholders_equity",
    "cash": "cash_and_equivalents",
    "intangible_assets": "goodwill_and_intangible_assets",
    "long_term_debt": "_long_term_debt",
}
_CASHFLOW = {
    "net_cash_flow_from_operating_activities": "_operating_cash_flow",
    "net_cash_flow_from_investing_activities": "_investing_cash_flow",
}


class PolygonProvider:
    name = "polygon"

    def _key(self, api_key: str | None) -> str | None:
        return api_key or os.environ.get("POLYGON_API_KEY")

    # ------------------------------------------------------------------
    # News (Phase 2)
    # ------------------------------------------------------------------

    def get_company_news(self, ticker: str, end_date: str, start_date: str | None = None, limit: int = 1000, api_key: str | None = None) -> list[CompanyNews]:
        key = self._key(api_key)
        if not key:
            return []
        params = {
            "ticker": ticker,
            "published_utc.lte": f"{end_date}T23:59:59Z",
            "order": "desc",
            "sort": "published_utc",
            "limit": min(limit, 1000),
            "apiKey": key,
        }
        if start_date:
            params["published_utc.gte"] = f"{start_date}T00:00:00Z"

        url = f"{_BASE}/v2/reference/news"
        out: list[CompanyNews] = []
        while url and len(out) < limit:
            data = get_json(url, params=params)
            params = None  # next_url already carries query params
            if not data:
                break
            for r in data.get("results", []):
                sentiment = None
                for ins in r.get("insights", []) or []:
                    if (ins.get("ticker") or "").upper() == ticker.upper():
                        sentiment = ins.get("sentiment")
                        break
                pub = r.get("published_utc", "")
                out.append(
                    CompanyNews(
                        ticker=ticker,
                        title=r.get("title") or "",
                        author=r.get("author") or None,
                        source=(r.get("publisher") or {}).get("name") or "polygon",
                        date=pub[:10] if pub else end_date,
                        url=r.get("article_url") or r.get("amp_url") or "",
                        sentiment=sentiment,
                    )
                )
                if len(out) >= limit:
                    break
            nxt = data.get("next_url")
            if not nxt:
                break
            url = f"{nxt}&apiKey={key}" if "apiKey=" not in nxt else nxt
        return out[:limit]

    # ------------------------------------------------------------------
    # Financials (Phase 3)
    # ------------------------------------------------------------------

    def fetch_financials(self, ticker: str, end_date: str, period: str = "ttm", limit: int = 10, api_key: str | None = None) -> list[dict]:
        """Return a list of normalized raw statement dicts, newest first.

        Each dict: ``{report_period, period, currency, <raw line items>}``.
        Raw keys are FD names from the mapping tables above (plus ``_*``
        helpers consumed by ``_derive``).
        """
        key = self._key(api_key)
        if not key:
            return []
        timeframe = {"ttm": "ttm", "annual": "annual", "quarterly": "quarterly"}.get(period, "ttm")
        params = {
            "ticker": ticker,
            "timeframe": timeframe,
            "period_of_report_date.lte": end_date,
            "order": "desc",
            "sort": "period_of_report_date",
            "limit": min(limit, 100),
            "apiKey": key,
        }
        data = get_json(f"{_BASE}/vX/reference/financials", params=params)
        if not data:
            return []

        rows: list[dict] = []
        for r in data.get("results", []):
            fin = r.get("financials", {}) or {}
            rec: dict = {
                "report_period": r.get("end_date") or r.get("period_of_report_date") or end_date,
                "period": period,
                "currency": "USD",
            }
            for poly_map, section in ((_INCOME, "income_statement"), (_BALANCE, "balance_sheet"), (_CASHFLOW, "cash_flow_statement")):
                sec = fin.get(section, {}) or {}
                for pkey, our in poly_map.items():
                    cell = sec.get(pkey)
                    if isinstance(cell, dict) and cell.get("value") is not None:
                        rec[our] = cell["value"]
            rows.append(_derive.enrich_raw(rec))
        return rows

    def search_line_items(self, ticker: str, line_items: list[str], end_date: str, period: str = "ttm", limit: int = 10, api_key: str | None = None) -> list[LineItem]:
        rows = self.fetch_financials(ticker, end_date, period=period, limit=limit, api_key=api_key)
        results: list[LineItem] = []
        for rec in rows[:limit]:
            payload = {"ticker": ticker, "report_period": rec["report_period"], "period": period, "currency": rec.get("currency", "USD")}
            for name in line_items:
                if name == "annual":
                    payload["annual"] = period == "annual"
                elif name in rec:
                    payload[name] = rec[name]
            results.append(LineItem(**payload))
        return results
