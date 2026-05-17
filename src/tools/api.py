"""Public data-access API used by all agents, the backtester and the web app.

Signatures and return types are unchanged. The implementations now delegate to
``src.tools.providers.router``, which selects a provider per data type from
configuration (``DATA_PROVIDER`` / ``DATA_PROVIDER_<TYPE>``) and falls back to
Financial Datasets on miss/error. With no configuration set, every data type
uses Financial Datasets — identical to the previous behavior.
"""

import pandas as pd

# Re-exported for backward compatibility (kept so external imports keep working).
from src.data.models import (  # noqa: F401
    CompanyFactsResponse,
    CompanyNews,
    CompanyNewsResponse,
    FinancialMetrics,
    FinancialMetricsResponse,
    InsiderTrade,
    InsiderTradeResponse,
    LineItem,
    LineItemResponse,
    Price,
    PriceResponse,
)
from src.tools.providers import router
from src.tools.providers.financialdatasets import _make_api_request  # noqa: F401


def get_prices(ticker: str, start_date: str, end_date: str, api_key: str = None) -> list[Price]:
    """Fetch price data (provider-routed; defaults to Financial Datasets)."""
    return router.get_prices(ticker, start_date, end_date, api_key=api_key)


def get_financial_metrics(
    ticker: str,
    end_date: str,
    period: str = "ttm",
    limit: int = 10,
    api_key: str = None,
) -> list[FinancialMetrics]:
    """Fetch financial metrics (provider-routed; defaults to Financial Datasets)."""
    return router.get_financial_metrics(ticker, end_date, period=period, limit=limit, api_key=api_key)


def search_line_items(
    ticker: str,
    line_items: list[str],
    end_date: str,
    period: str = "ttm",
    limit: int = 10,
    api_key: str = None,
) -> list[LineItem]:
    """Fetch raw financial-statement line items (provider-routed)."""
    return router.search_line_items(ticker, line_items, end_date, period=period, limit=limit, api_key=api_key)


def get_insider_trades(
    ticker: str,
    end_date: str,
    start_date: str | None = None,
    limit: int = 1000,
    api_key: str = None,
) -> list[InsiderTrade]:
    """Fetch insider trades (provider-routed; defaults to Financial Datasets)."""
    return router.get_insider_trades(ticker, end_date, start_date=start_date, limit=limit, api_key=api_key)


def get_company_news(
    ticker: str,
    end_date: str,
    start_date: str | None = None,
    limit: int = 1000,
    api_key: str = None,
) -> list[CompanyNews]:
    """Fetch company news (provider-routed; defaults to Financial Datasets)."""
    return router.get_company_news(ticker, end_date, start_date=start_date, limit=limit, api_key=api_key)


def get_market_cap(
    ticker: str,
    end_date: str,
    api_key: str = None,
) -> float | None:
    """Fetch market cap (provider-routed; defaults to Financial Datasets)."""
    return router.get_market_cap(ticker, end_date, api_key=api_key)


def prices_to_df(prices: list[Price]) -> pd.DataFrame:
    """Convert prices to a DataFrame."""
    df = pd.DataFrame([p.model_dump() for p in prices])
    df["Date"] = pd.to_datetime(df["time"])
    df.set_index("Date", inplace=True)
    numeric_cols = ["open", "close", "high", "low", "volume"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df.sort_index(inplace=True)
    return df


def get_price_data(ticker: str, start_date: str, end_date: str, api_key: str = None) -> pd.DataFrame:
    prices = get_prices(ticker, start_date, end_date, api_key=api_key)
    return prices_to_df(prices)
