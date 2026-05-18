"""Unit tests for the internal (quantai-market-data) provider + router wiring.

These run without a Postgres DB or network: the ``quantai_market_data``
package is replaced with an in-memory fake via ``sys.modules``.
"""

import datetime
import sys
import types

import pandas as pd
import pytest


def _fake_qmd(df: pd.DataFrame):
    """Build a fake quantai_market_data module returning *df*."""
    mod = types.ModuleType("quantai_market_data")
    mod.get_daily_range = lambda symbol, start, end, **kw: df
    providers = types.ModuleType("quantai_market_data.providers")

    class _PC:
        def get_ticker_details(self, symbol):
            return {"market_cap": 3.21e12, "name": "Fake Inc"}

    providers.PolygonClient = _PC
    mod.providers = providers
    return mod, providers


@pytest.fixture
def install_fake_qmd(monkeypatch):
    def _install(df):
        mod, providers = _fake_qmd(df)
        monkeypatch.setitem(sys.modules, "quantai_market_data", mod)
        monkeypatch.setitem(sys.modules, "quantai_market_data.providers", providers)

    return _install


SAMPLE = pd.DataFrame(
    [
        # intentionally out of order to verify sorting
        {"date": datetime.date(2024, 1, 3), "open": 12.0, "high": 13.0, "low": 11.5, "close": 12.5, "adj_close": 12.5, "volume": 2000.0},
        {"date": datetime.date(2024, 1, 2), "open": 10.0, "high": 11.0, "low": 9.5, "close": 10.5, "adj_close": 10.5, "volume": 1000.0},
    ]
)


def test_get_prices_maps_and_sorts(install_fake_qmd):
    install_fake_qmd(SAMPLE)
    from src.tools.providers.internal import InternalProvider

    prices = InternalProvider().get_prices("AAPL", "2024-01-01", "2024-01-31")
    assert [p.time for p in prices] == ["2024-01-02T00:00:00Z", "2024-01-03T00:00:00Z"]
    p0 = prices[0]
    assert (p0.open, p0.high, p0.low, p0.close) == (10.0, 11.0, 9.5, 10.5)
    assert p0.volume == 1000 and isinstance(p0.volume, int)


def test_get_prices_empty_df(install_fake_qmd):
    install_fake_qmd(pd.DataFrame(columns=["date", "open", "high", "low", "close", "adj_close", "volume"]))
    from src.tools.providers.internal import InternalProvider

    assert InternalProvider().get_prices("AAPL", "2024-01-01", "2024-01-31") == []


def test_get_prices_missing_package(monkeypatch):
    """No quantai_market_data installed -> [] (router will fall back to fd)."""
    monkeypatch.setitem(sys.modules, "quantai_market_data", None)
    from src.tools.providers.internal import InternalProvider

    assert InternalProvider().get_prices("AAPL", "2024-01-01", "2024-01-31") == []


def test_market_cap_today_only(install_fake_qmd):
    install_fake_qmd(SAMPLE)
    from src.tools.providers.internal import InternalProvider

    prov = InternalProvider()
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    assert prov.get_market_cap("AAPL", today) == pytest.approx(3.21e12)
    # Non-today -> None (Phase 4 will compute historical from shares x price)
    assert prov.get_market_cap("AAPL", "2020-01-01") is None


def test_router_uses_internal_then_falls_back(monkeypatch, install_fake_qmd):
    """DATA_PROVIDER_PRICES=internal; empty internal result falls back to fd."""
    monkeypatch.setenv("DATA_PROVIDER_PRICES", "internal")
    monkeypatch.setenv("DATA_PROVIDER_FALLBACK_FD", "true")
    install_fake_qmd(pd.DataFrame(columns=["date", "open", "high", "low", "close", "adj_close", "volume"]))

    # Reload providers so env + fake module take effect.
    for m in [k for k in list(sys.modules) if k.startswith("src.tools")]:
        del sys.modules[m]
    from src.tools.providers import financialdatasets as fdmod
    from src.tools.providers import router

    called = {}
    monkeypatch.setattr(
        fdmod.FinancialDatasetsProvider,
        "get_prices",
        lambda self, t, s, e, api_key=None: called.setdefault("fd", True) or [],
    )
    router.get_prices("AAPL", "2024-01-01", "2024-01-31")
    assert called.get("fd") is True  # fell back to FD when internal returned empty
