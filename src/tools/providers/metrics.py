"""Computed-metrics provider (Phase 4).

Financial Datasets' ~60 ``FinancialMetrics`` fields are derived, not raw. This
provider rebuilds them locally from raw line items (Polygon vX, SEC fallback) +
price, via ``_derive.compute_metrics``. $0 ongoing, fully transparent.

Fields whose exact FD definition needs inputs the free sources don't expose
(tax rate, short-term debt, inventory/receivables detail) are left ``None``
rather than approximated silently — those are the documented non-parity fields.
"""

from __future__ import annotations

import logging

from src.data.models import FinancialMetrics
from src.tools.providers import _derive

logger = logging.getLogger(__name__)


class MetricsProvider:
    name = "metrics"

    def _raw_records(self, ticker: str, end_date: str, period: str, limit: int, api_key: str | None) -> list[dict]:
        """Raw enriched statement records, newest first: Polygon then SEC."""
        from src.tools.providers.polygon import PolygonProvider
        from src.tools.providers.sec_edgar import SECEdgarProvider

        rows = PolygonProvider().fetch_financials(ticker, end_date, period=period, limit=limit + 1, api_key=api_key)
        if not rows:
            rows = SECEdgarProvider()._records(ticker, end_date, "annual" if period == "ttm" else period, limit + 1)
        return rows

    def get_financial_metrics(self, ticker: str, end_date: str, period: str = "ttm", limit: int = 10, api_key: str | None = None) -> list[FinancialMetrics]:
        rows = self._raw_records(ticker, end_date, period, limit, api_key)
        if not rows:
            return []

        # Latest close <= end_date, and current market cap, via the internal provider.
        price = None
        market_cap = None
        try:
            from src.tools.providers.internal import InternalProvider

            internal = InternalProvider()
            bars = internal.get_prices(ticker, "1900-01-01", end_date)
            if bars:
                price = bars[-1].close
            market_cap = internal.get_market_cap(ticker, end_date)
        except Exception as e:
            logger.debug("price/market_cap unavailable for %s: %s", ticker, e)

        # Polygon's `timeframe=ttm` returns a single snapshot with no prior
        # period, so consecutive-record growth is impossible. Derive YoY growth
        # from the two latest *annual* statements and overlay it (this is also
        # how FD's TTM growth effectively behaves).
        growth_overlay: dict = {}
        if period == "ttm":
            annual = self._raw_records(ticker, end_date, "annual", 2, api_key)
            if len(annual) >= 2:
                g = _derive.compute_metrics(annual[0], annual[1], None, None)
                growth_overlay = {k: g.get(k) for k in _derive.GROWTH_FIELDS}

        out: list[FinancialMetrics] = []
        for i, cur in enumerate(rows[:limit]):
            prev = rows[i + 1] if i + 1 < len(rows) else None
            m = _derive.compute_metrics(cur, prev, price, market_cap)
            for k, v in growth_overlay.items():
                if m.get(k) is None and v is not None:
                    m[k] = v
            out.append(
                FinancialMetrics(
                    ticker=ticker,
                    report_period=cur.get("report_period", end_date),
                    period=period,
                    currency=cur.get("currency", "USD"),
                    **m,
                )
            )
        return out

    def get_market_cap(self, ticker: str, end_date: str, api_key: str | None = None) -> float | None:
        metrics = self.get_financial_metrics(ticker, end_date, limit=1, api_key=api_key)
        return metrics[0].market_cap if metrics else None
