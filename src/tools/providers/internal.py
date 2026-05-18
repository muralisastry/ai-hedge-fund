"""Internal provider — the quantai-trading shared data layer.

Prices come from the suite-wide ``quantai_market_data`` package (Polygon-backed
Postgres ``quantai_marketdata``, the same source every other suite app uses).
Market cap comes from Polygon reference data via that package's ``PolygonClient``.

Requires the shared package to be installed in this venv (suite convention):

    pip install -e ~/quantai-trading/quantai-market-data

and ``QUANTAI_MARKETDATA_DB_URL`` set in the environment. If either is missing
the provider returns empty results so the router falls back to Financial
Datasets rather than hard-failing.

Note on adjustment: ``quantai_market_data`` stores Polygon bars fetched with
``adjusted=True`` — i.e. split/dividend-adjusted, the suite standard and the
correct input for technical indicators and backtests. This differs from
Financial Datasets' unadjusted ``/prices`` around split dates; parity is
validated against yfinance-adjusted, not FD.

Phase 1 implements ``get_prices`` and ``get_market_cap`` only. Financial
metrics, line items, insider trades and news remain routed to ``fd`` (per
``config``) until later phases add them here.
"""

from __future__ import annotations

import datetime
import logging

from src.data.models import CompanyNews, FinancialMetrics, InsiderTrade, LineItem, Price
from src.tools.providers import _derive

logger = logging.getLogger(__name__)


def _fundamentals():
    """Lazy import of the shared fundamentals submodule; None if unavailable."""
    try:
        from quantai_market_data import fundamentals  # noqa: WPS433

        return fundamentals
    except Exception as e:
        logger.debug("quantai_market_data.fundamentals unavailable: %s", e)
        return None


def _qmd():
    """Import the shared package lazily; None if unavailable."""
    try:
        import quantai_market_data as qmd  # noqa: WPS433

        return qmd
    except Exception as e:  # ImportError, or DB config error at import time
        logger.warning("quantai_market_data unavailable (%s); internal provider will yield no data", e)
        return None


class InternalProvider:
    """Provider backed by the shared quantai-market-data layer + Polygon."""

    name = "internal"

    def __init__(self) -> None:
        self._polygon = None  # lazy PolygonClient

    # ------------------------------------------------------------------
    # Prices
    # ------------------------------------------------------------------

    def get_prices(self, ticker: str, start_date: str, end_date: str, api_key: str | None = None) -> list[Price]:
        qmd = _qmd()
        if qmd is None:
            return []
        try:
            start = datetime.date.fromisoformat(start_date)
            end = datetime.date.fromisoformat(end_date)
            df = qmd.get_daily_range(ticker, start, end)
        except Exception as e:
            logger.warning("internal get_prices failed for %s: %s", ticker, e)
            return []

        if df is None or df.empty:
            return []

        prices: list[Price] = []
        for row in df.itertuples(index=False):
            d = row.date
            # Normalize the date column to an ISO-8601 timestamp matching FD's `time`.
            if isinstance(d, (datetime.datetime,)):
                time_str = d.strftime("%Y-%m-%dT%H:%M:%SZ")
            elif isinstance(d, datetime.date):
                time_str = f"{d.isoformat()}T00:00:00Z"
            else:
                time_str = f"{str(d)[:10]}T00:00:00Z"
            try:
                prices.append(
                    Price(
                        open=float(row.open),
                        close=float(row.close),
                        high=float(row.high),
                        low=float(row.low),
                        volume=int(row.volume) if row.volume is not None else 0,
                        time=time_str,
                    )
                )
            except Exception as e:
                logger.debug("skipping malformed bar for %s @ %s: %s", ticker, time_str, e)
        prices.sort(key=lambda p: p.time)
        return prices

    # ------------------------------------------------------------------
    # Market cap
    # ------------------------------------------------------------------

    def get_market_cap(self, ticker: str, end_date: str, api_key: str | None = None) -> float | None:
        """Current market cap from Polygon reference data.

        Polygon ticker details exposes only *current* market cap, which covers
        the common ``end_date == today`` path (the only case FD treated as
        authoritative via company facts). Historical as-of-date market cap is
        derived later in Phase 4 (shares x price); until then a non-today
        request returns None so the router can fall back.
        """
        qmd = _qmd()
        if qmd is None:
            return None
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        if end_date != today:
            return None
        try:
            if self._polygon is None:
                from quantai_market_data.providers import PolygonClient  # noqa: WPS433

                self._polygon = PolygonClient()
            details = self._polygon.get_ticker_details(ticker)
        except Exception as e:
            logger.warning("internal get_market_cap failed for %s: %s", ticker, e)
            return None
        mc = (details or {}).get("market_cap")
        return float(mc) if mc else None

    # ------------------------------------------------------------------
    # Fundamentals from the shared layer (Phase 5 — one source of truth)
    # ------------------------------------------------------------------

    @staticmethod
    def _read_financials(f, ticker: str, period: str, limit: int):
        """Read raw financials for *period*; fall back to the canonical
        ``annual`` series when the requested period has no rows (the shared
        ingester stores annual; agents typically request ``ttm``). Returns
        ``(records, effective_period)``."""
        recs = f.read_financials(ticker, period=period, limit=limit)
        if not recs and period != "annual":
            recs = f.read_financials(ticker, period="annual", limit=limit)
            return recs, "annual"
        return recs, period

    def search_line_items(self, ticker: str, line_items: list[str], end_date: str, period: str = "ttm", limit: int = 10, api_key: str | None = None) -> list[LineItem]:
        f = _fundamentals()
        if f is None:
            return []
        try:
            recs, eff = self._read_financials(f, ticker, period, limit)
        except Exception as e:
            logger.warning("internal search_line_items failed for %s: %s", ticker, e)
            return []
        out: list[LineItem] = []
        for rec in recs:
            if rec.get("report_period", "9999") > end_date:
                continue
            _derive.enrich_raw(rec)
            payload = {"ticker": ticker, "report_period": rec["report_period"], "period": eff, "currency": rec.get("currency", "USD")}
            for name in line_items:
                if name == "annual":
                    payload["annual"] = eff == "annual"
                elif name in rec and not name.startswith("_"):
                    payload[name] = rec[name]
            out.append(LineItem(**payload))
        return out[:limit]

    def get_financial_metrics(self, ticker: str, end_date: str, period: str = "ttm", limit: int = 10, api_key: str | None = None) -> list[FinancialMetrics]:
        f = _fundamentals()
        if f is None:
            return []
        try:
            raw, eff = self._read_financials(f, ticker, period, limit + 1)
            recs = [r for r in raw if r.get("report_period", "9999") <= end_date]
        except Exception as e:
            logger.warning("internal get_financial_metrics failed for %s: %s", ticker, e)
            return []
        if not recs:
            return []
        for r in recs:
            _derive.enrich_raw(r)
        price = None
        bars = self.get_prices(ticker, "1900-01-01", end_date)
        if bars:
            price = bars[-1].close
        market_cap = self.get_market_cap(ticker, end_date)
        out: list[FinancialMetrics] = []
        for i, cur in enumerate(recs[:limit]):
            prev = recs[i + 1] if i + 1 < len(recs) else None
            m = _derive.compute_metrics(cur, prev, price, market_cap)
            out.append(FinancialMetrics(ticker=ticker, report_period=cur.get("report_period", end_date), period=eff, currency=cur.get("currency", "USD"), **m))
        return out

    def get_insider_trades(self, ticker: str, end_date: str, start_date: str | None = None, limit: int = 1000, api_key: str | None = None) -> list[InsiderTrade]:
        f = _fundamentals()
        if f is None:
            return []
        try:
            rows = f.read_insider_trades(ticker, start_date, end_date, limit)
        except Exception as e:
            logger.warning("internal get_insider_trades failed for %s: %s", ticker, e)
            return []
        return [InsiderTrade(**r) for r in rows]

    def get_company_news(self, ticker: str, end_date: str, start_date: str | None = None, limit: int = 1000, api_key: str | None = None) -> list[CompanyNews]:
        f = _fundamentals()
        if f is None:
            return []
        try:
            rows = f.read_news(ticker, start_date, end_date, limit)
        except Exception as e:
            logger.warning("internal get_company_news failed for %s: %s", ticker, e)
            return []
        return [CompanyNews(**r) for r in rows]
