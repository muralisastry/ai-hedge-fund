"""Internal data client backed by the shared quantai-market-data warehouse.

Implements the same ``DataClient`` protocol as ``FDClient`` (see
``v2/data/protocol.py``) on top of the suite's Postgres warehouse:

- Prices: ``quantai_market_data.get_daily_range`` on the point-in-time
  ``split_asof`` basis (the suite's mandatory backtest basis). Dividends are
  NOT baked into the bars; a total-return engine must credit them as cash.
- Earnings: the ``earnings_events`` table (yfinance analyst estimates —
  announcement timestamp, EPS estimate/actual/surprise), joined to quarterly
  ``company_financials`` report periods. ``source_type`` is ``"8-K"`` because
  the announce timestamp is the announcement itself, not a later 10-Q/10-K.
- Fundamentals/news/insider/facts: the ``fundamentals`` submodule. Metrics
  are point-in-time filtered on ``filed_at`` (rows without a filing date are
  excluded); only directly-derivable ratios are populated, the rest are None.

Known limits (documented, not bugs): historical market cap is unavailable
(``get_market_cap`` returns None for past dates); ``period="ttm"`` metrics
have no warehouse rows today (annual/quarterly only).

Error contract mirrors FDClient: infrastructure failures raise
``InternalClientError``; empty list / None mean data genuinely absent.
"""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta

import quantai_market_data as _qmd
from quantai_market_data import fundamentals as _fund

from v2.data.models import (
    CompanyFacts,
    CompanyNews,
    Earnings,
    EarningsData,
    EarningsRecord,
    FinancialMetrics,
    InsiderTrade,
    Price,
)

logger = logging.getLogger(__name__)

_EPS_EPSILON = 0.005  # |actual − estimate| ≤ ε counts as MEET
_PERIOD_MATCH_MAX_DAYS = 100  # announce date must be within this of the quarter end


class InternalClientError(Exception):
    """Raised on any warehouse/infrastructure failure (never silent-empty)."""

    def __init__(self, message: str, *, operation: str | None = None):
        super().__init__(message)
        self.operation = operation


class InternalClient:
    """DataClient over quantai-market-data. Context-manager compatible."""

    def __init__(self, *, eps_epsilon: float = _EPS_EPSILON) -> None:
        if not os.environ.get("QUANTAI_MARKETDATA_DB_URL"):
            raise InternalClientError(
                "QUANTAI_MARKETDATA_DB_URL is not set — the internal provider " "needs the shared warehouse DSN (see .env.example)",
                operation="init",
            )
        self._eps_epsilon = eps_epsilon

    # -- context manager (connections are per-call in the shared layer) ----

    def close(self) -> None:  # symmetry with FDClient
        pass

    def __enter__(self) -> "InternalClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Prices
    # ------------------------------------------------------------------

    def get_prices(self, ticker: str, start_date: str, end_date: str, **kwargs) -> list[Price]:
        start = date.fromisoformat(start_date[:10])
        end = date.fromisoformat(end_date[:10])
        try:
            df = _qmd.get_daily_range(ticker.upper(), start, end, adjust="split_asof", asof=end)
        except Exception as exc:
            raise InternalClientError(f"get_prices({ticker}) failed: {exc}", operation="get_prices") from exc
        if df is None or df.empty:
            return []
        out: list[Price] = []
        for row in df.itertuples(index=False):
            vol = getattr(row, "volume", None)
            out.append(
                Price(
                    open=float(row.open),
                    close=float(row.close),
                    high=float(row.high),
                    low=float(row.low),
                    volume=int(vol) if vol is not None and vol == vol else 0,
                    time=f"{str(row.date)[:10]}T00:00:00Z",
                )
            )
        return out

    # ------------------------------------------------------------------
    # Earnings (announcements + analyst estimates)
    # ------------------------------------------------------------------

    def get_earnings_history(self, ticker: str, limit: int = 12) -> list[EarningsRecord]:
        try:
            # Extra headroom: scheduled-but-unreported events are dropped below.
            events = _fund.read_earnings_events(ticker.upper(), limit=limit + 4)
            quarters = _fund.read_financials(ticker.upper(), period="quarterly", limit=limit + 8)
        except Exception as exc:
            raise InternalClientError(f"get_earnings_history({ticker}) failed: {exc}", operation="get_earnings_history") from exc
        quarterly_periods = [q["report_period"] for q in quarters]
        return _build_earnings_records(ticker.upper(), events, quarterly_periods, limit=limit, epsilon=self._eps_epsilon)

    def get_earnings(self, ticker: str) -> Earnings | None:
        records = self.get_earnings_history(ticker, limit=1)
        if not records:
            return None
        r = records[0]
        return Earnings(
            ticker=r.ticker,
            report_period=r.report_period,
            fiscal_period=r.fiscal_period,
            currency=r.currency,
            quarterly=r.quarterly,
        )

    # ------------------------------------------------------------------
    # Financial metrics (point-in-time on filed_at)
    # ------------------------------------------------------------------

    def get_financial_metrics(self, ticker: str, end_date: str, period: str = "ttm", limit: int = 10) -> list[FinancialMetrics]:
        try:
            rows = _fund.read_financials(ticker.upper(), period=period, limit=limit * 2 + 4)
        except Exception as exc:
            raise InternalClientError(f"get_financial_metrics({ticker}) failed: {exc}", operation="get_financial_metrics") from exc
        cutoff = end_date[:10]
        # Point-in-time: keep only rows that were public by end_date. Rows
        # without a filing date are excluded (matches FD's filing_date_lte).
        visible = [r for r in rows if r.get("filed_at") and r["filed_at"] <= cutoff]
        return [_metrics_from_row(ticker.upper(), r) for r in visible[:limit]]

    # ------------------------------------------------------------------
    # News / insider / facts / market cap
    # ------------------------------------------------------------------

    def get_news(self, ticker: str, end_date: str, start_date: str | None = None, limit: int = 1000) -> list[CompanyNews]:
        try:
            rows = _fund.read_news(ticker.upper(), start_date, end_date, limit=limit)
        except Exception as exc:
            raise InternalClientError(f"get_news({ticker}) failed: {exc}", operation="get_news") from exc
        out = []
        for r in rows:
            if not r.get("title") or not r.get("source"):
                continue
            out.append(CompanyNews(**{k: v for k, v in r.items() if v is not None}))
        return out

    def get_insider_trades(self, ticker: str, end_date: str, start_date: str | None = None, limit: int = 1000) -> list[InsiderTrade]:
        try:
            rows = _fund.read_insider_trades(ticker.upper(), start_date, end_date, limit=limit)
        except Exception as exc:
            raise InternalClientError(f"get_insider_trades({ticker}) failed: {exc}", operation="get_insider_trades") from exc
        out = []
        for r in rows:
            if not r.get("name") or not r.get("filing_date"):
                continue
            out.append(InsiderTrade(**{k: v for k, v in r.items() if v is not None}))
        return out

    def get_company_facts(self, ticker: str) -> CompanyFacts | None:
        try:
            row = _fund.read_company_facts(ticker.upper())
        except Exception as exc:
            raise InternalClientError(f"get_company_facts({ticker}) failed: {exc}", operation="get_company_facts") from exc
        if not row:
            return None
        return CompanyFacts(
            ticker=row["symbol"],
            name=row.get("name"),
            market_cap=row.get("market_cap"),
            cik=str(row["cik"]) if row.get("cik") else None,
            sector=row.get("sector"),
            industry=row.get("industry"),
            exchange=row.get("exchange"),
            sic_code=str(row["sic_code"]) if row.get("sic_code") else None,
        )

    def get_market_cap(self, ticker: str, end_date: str) -> float | None:
        # Historical as-of market cap is not stored — only the current value.
        if end_date[:10] < date.today().isoformat():
            return None
        try:
            return _fund.read_market_cap(ticker.upper())
        except Exception as exc:
            raise InternalClientError(f"get_market_cap({ticker}) failed: {exc}", operation="get_market_cap") from exc


# ----------------------------------------------------------------------
# Pure helpers (unit-testable without a DB)
# ----------------------------------------------------------------------


def _classify_surprise(actual: float | None, estimate: float | None, epsilon: float) -> str | None:
    if actual is None or estimate is None:
        return None
    delta = actual - estimate
    if delta > epsilon:
        return "BEAT"
    if delta < -epsilon:
        return "MISS"
    return "MEET"


def _prev_quarter_end(d: date) -> date:
    """Latest calendar quarter end strictly before *d* (fallback period match)."""
    for month, day in ((12, 31), (9, 30), (6, 30), (3, 31)):
        candidate = date(d.year, month, day)
        if candidate < d:
            return candidate
    return date(d.year - 1, 12, 31)


def _match_report_period(announce: date, quarterly_periods: list[str]) -> str:
    """Latest known quarterly report_period ≤ announce within the match window,
    else the inferred previous calendar quarter end."""
    best: str | None = None
    for p in quarterly_periods:
        pd_ = date.fromisoformat(p[:10])
        if pd_ < announce and (announce - pd_) <= timedelta(days=_PERIOD_MATCH_MAX_DAYS):
            if best is None or p > best:
                best = p
    return best or _prev_quarter_end(announce).isoformat()


def _build_earnings_records(
    ticker: str,
    events: list[dict],
    quarterly_periods: list[str],
    *,
    limit: int,
    epsilon: float,
) -> list[EarningsRecord]:
    """Map earnings_events rows (newest first) to EarningsRecord.

    Scheduled-but-unreported events (eps_actual is None) are dropped — every
    consumer of history wants reported announcements.
    """
    records: list[EarningsRecord] = []
    for e in events:
        actual = e.get("eps_actual")
        if actual is None:
            continue
        announce_at = str(e["announce_at"])
        announce_date = date.fromisoformat(announce_at[:10])
        estimate = e.get("eps_estimate")
        records.append(
            EarningsRecord(
                ticker=ticker,
                report_period=_match_report_period(announce_date, quarterly_periods),
                source_type="8-K",
                filing_date=announce_at[:10],
                filing_datetime=announce_at,
                fiscal_period="quarterly",
                quarterly=EarningsData(
                    earnings_per_share=actual,
                    estimated_earnings_per_share=estimate,
                    eps_surprise=_classify_surprise(actual, estimate, epsilon),
                ),
            )
        )
        if len(records) >= limit:
            break
    return records


def _ratio(numer: float | None, denom: float | None) -> float | None:
    if numer is None or not denom:
        return None
    return numer / denom


def _metrics_from_row(ticker: str, r: dict) -> FinancialMetrics:
    revenues = r.get("revenue")  # canonical warehouse name (singular)
    net_income = r.get("net_income")
    equity = r.get("shareholders_equity")
    return FinancialMetrics(
        ticker=ticker,
        report_period=r["report_period"],
        period=r.get("period") or "annual",
        currency=r.get("currency"),
        filing_date=r.get("filed_at"),
        earnings_per_share=r.get("earnings_per_share"),
        gross_margin=_ratio(r.get("gross_profit"), revenues),
        operating_margin=_ratio(r.get("operating_income"), revenues),
        net_margin=_ratio(net_income, revenues),
        return_on_equity=_ratio(net_income, equity),
        return_on_assets=_ratio(net_income, r.get("total_assets")),
        current_ratio=_ratio(r.get("current_assets"), r.get("current_liabilities")),
        debt_to_equity=_ratio(r.get("total_liabilities"), equity),
    )
