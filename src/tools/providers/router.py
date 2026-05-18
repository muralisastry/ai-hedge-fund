"""Per-data-type dispatch with optional Financial Datasets fallback.

The public functions in ``src/tools/api.py`` call the module-level functions
here. Each looks up the configured provider for its data type, calls it, and
— if the result is empty/None or the provider raised, the provider is not
already ``fd``, and fallback is enabled — retries once via the ``fd``
provider.

Provider registry is lazy. Phase 0 registers only ``fd``; later phases add
``internal``, ``polygon`` and ``sec`` here.
"""

from __future__ import annotations

import logging

from src.data.cache import get_cache
from src.data.models import CompanyNews, FinancialMetrics, InsiderTrade, LineItem, Price
from src.tools.providers import base, config

logger = logging.getLogger(__name__)

_cache = get_cache()

_INSTANCES: dict[str, object] = {}


def _build(name: str):
    if name == "fd":
        from src.tools.providers.financialdatasets import FinancialDatasetsProvider

        return FinancialDatasetsProvider()
    if name == "internal":
        from src.tools.providers.internal import InternalProvider

        return InternalProvider()
    if name == "polygon":
        from src.tools.providers.polygon import PolygonProvider

        return PolygonProvider()
    if name == "sec":
        from src.tools.providers.sec_edgar import SECEdgarProvider

        return SECEdgarProvider()
    if name == "metrics":
        from src.tools.providers.metrics import MetricsProvider

        return MetricsProvider()
    raise KeyError(f"Unknown data provider: {name!r}")


def _provider(name: str):
    inst = _INSTANCES.get(name)
    if inst is None:
        inst = _build(name)
        _INSTANCES[name] = inst
    return inst


def _is_empty(result) -> bool:
    return result is None or (isinstance(result, list) and len(result) == 0)


def _dispatch(data_type: str, method: str, *args, **kwargs):
    """Call the configured provider for *data_type*, fall back to fd."""
    name = config.provider_for(data_type)
    try:
        provider = _provider(name)
        result = getattr(provider, method)(*args, **kwargs)
    except Exception as e:  # providers should not raise, but never let it bubble
        logger.warning("Provider %r failed on %s: %s", name, method, e)
        result = None

    if name != "fd" and _is_empty(result) and config.fallback_to_fd_enabled():
        logger.info("Provider %r returned no data for %s; falling back to fd", name, method)
        try:
            result = getattr(_provider("fd"), method)(*args, **kwargs)
        except Exception as e:
            logger.warning("FD fallback failed on %s: %s", method, e)
            result = None

    if result is None and method != "get_market_cap":
        return []
    return result


def get_prices(ticker: str, start_date: str, end_date: str, api_key: str | None = None) -> list[Price]:
    return _dispatch(base.PRICES, "get_prices", ticker, start_date, end_date, api_key=api_key)


def get_financial_metrics(ticker: str, end_date: str, period: str = "ttm", limit: int = 10, api_key: str | None = None) -> list[FinancialMetrics]:
    return _dispatch(base.FINANCIAL_METRICS, "get_financial_metrics", ticker, end_date, period=period, limit=limit, api_key=api_key)


def search_line_items(ticker: str, line_items: list[str], end_date: str, period: str = "ttm", limit: int = 10, api_key: str | None = None) -> list[LineItem]:
    # Cache key includes the requested line-item set so differing requests for
    # the same ticker don't collide (the FD path was previously uncached).
    cache_key = f"{ticker}_{period}_{end_date}_{limit}_{','.join(sorted(line_items))}"
    if cached := _cache.get_line_items(cache_key):
        return [LineItem(**li) for li in cached]
    results = _dispatch(base.LINE_ITEMS, "search_line_items", ticker, line_items, end_date, period=period, limit=limit, api_key=api_key)
    if results:
        _cache.set_line_items(cache_key, [li.model_dump() for li in results])
    return results


def get_insider_trades(ticker: str, end_date: str, start_date: str | None = None, limit: int = 1000, api_key: str | None = None) -> list[InsiderTrade]:
    return _dispatch(base.INSIDER_TRADES, "get_insider_trades", ticker, end_date, start_date=start_date, limit=limit, api_key=api_key)


def get_company_news(ticker: str, end_date: str, start_date: str | None = None, limit: int = 1000, api_key: str | None = None) -> list[CompanyNews]:
    return _dispatch(base.COMPANY_NEWS, "get_company_news", ticker, end_date, start_date=start_date, limit=limit, api_key=api_key)


def get_market_cap(ticker: str, end_date: str, api_key: str | None = None) -> float | None:
    return _dispatch(base.MARKET_CAP, "get_market_cap", ticker, end_date, api_key=api_key)
