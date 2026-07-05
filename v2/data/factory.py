"""Provider selection for the v2 data layer.

Mirrors the v1 pattern (``src/tools/providers/config.py``) at v2's smaller
scale: one env var, ``DATA_PROVIDER`` (the repo default is ``internal``),
overridable per call site. Imports are lazy so selecting one provider never
requires the other's dependencies.
"""

from __future__ import annotations

import os

from v2.data.protocol import DataClient

_DEFAULT_PROVIDER = "internal"


def make_client(provider: str | None = None) -> DataClient:
    """Build a DataClient for *provider* (or env ``DATA_PROVIDER``).

    Supported: ``internal`` (quantai-market-data warehouse), ``fd``
    (financialdatasets.ai, needs ``FINANCIAL_DATASETS_API_KEY``).
    """
    name = (provider or os.environ.get("DATA_PROVIDER") or _DEFAULT_PROVIDER).lower()
    if name == "internal":
        from v2.data.internal import InternalClient

        return InternalClient()
    if name == "fd":
        from v2.data.client import FDClient

        return FDClient()
    raise ValueError(f"unknown DATA_PROVIDER {name!r} (expected 'internal' or 'fd')")
