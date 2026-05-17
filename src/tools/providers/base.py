"""Provider protocol — the interface every data source implements.

Structural typing (``Protocol``): any object with these methods is a valid
provider. Methods return the existing ``src.data.models`` types and must
return ``[]`` / ``None`` on failure rather than raising, so the router can
fall back cleanly.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.data.models import (
    CompanyNews,
    FinancialMetrics,
    InsiderTrade,
    LineItem,
    Price,
)

# Data-type keys used by config + router. Keep in sync with router dispatch.
PRICES = "prices"
FINANCIAL_METRICS = "financial_metrics"
LINE_ITEMS = "line_items"
INSIDER_TRADES = "insider_trades"
COMPANY_NEWS = "company_news"
MARKET_CAP = "market_cap"

DATA_TYPES = (
    PRICES,
    FINANCIAL_METRICS,
    LINE_ITEMS,
    INSIDER_TRADES,
    COMPANY_NEWS,
    MARKET_CAP,
)


@runtime_checkable
class DataProvider(Protocol):
    """Interface mirrored 1:1 by the public functions in src/tools/api.py."""

    name: str

    def get_prices(self, ticker: str, start_date: str, end_date: str, api_key: str | None = None) -> list[Price]: ...

    def get_financial_metrics(self, ticker: str, end_date: str, period: str = "ttm", limit: int = 10, api_key: str | None = None) -> list[FinancialMetrics]: ...

    def search_line_items(self, ticker: str, line_items: list[str], end_date: str, period: str = "ttm", limit: int = 10, api_key: str | None = None) -> list[LineItem]: ...

    def get_insider_trades(self, ticker: str, end_date: str, start_date: str | None = None, limit: int = 1000, api_key: str | None = None) -> list[InsiderTrade]: ...

    def get_company_news(self, ticker: str, end_date: str, start_date: str | None = None, limit: int = 1000, api_key: str | None = None) -> list[CompanyNews]: ...

    def get_market_cap(self, ticker: str, end_date: str, api_key: str | None = None) -> float | None: ...
