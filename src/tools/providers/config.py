"""Provider selection from environment.

- ``DATA_PROVIDER`` sets the global default (default ``fd``).
- ``DATA_PROVIDER_<TYPE>`` overrides one data type, e.g.
  ``DATA_PROVIDER_PRICES=internal``. ``<TYPE>`` is the upper-cased data-type
  key from ``base.DATA_TYPES`` (PRICES, FINANCIAL_METRICS, LINE_ITEMS,
  INSIDER_TRADES, COMPANY_NEWS, MARKET_CAP).
- ``DATA_PROVIDER_FALLBACK_FD`` (default ``true``) — when the selected
  provider returns empty/None or raises, retry once via the ``fd`` provider.

Defaults keep Phase 0 at ``fd`` for every type → no behavior change.
"""

from __future__ import annotations

import os

DEFAULT_PROVIDER = "fd"


def provider_for(data_type: str) -> str:
    """Return the configured provider name for a data type."""
    specific = os.environ.get(f"DATA_PROVIDER_{data_type.upper()}")
    if specific:
        return specific.strip().lower()
    return os.environ.get("DATA_PROVIDER", DEFAULT_PROVIDER).strip().lower()


def fallback_to_fd_enabled() -> bool:
    """Whether to fall back to Financial Datasets on miss/error."""
    return os.environ.get("DATA_PROVIDER_FALLBACK_FD", "true").strip().lower() not in ("0", "false", "no")
