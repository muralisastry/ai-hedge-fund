"""Load .env for v2 tests and share provider-gating helpers."""

import os

from dotenv import load_dotenv

load_dotenv()


def has_fd_key() -> bool:
    """True only when a real-looking Financial Datasets key is configured.

    The suite retired FD; .env commonly carries a placeholder (e.g.
    "your-financial-datasets-api-key"), which must not un-skip live FD tests.
    """
    key = os.environ.get("FINANCIAL_DATASETS_API_KEY", "")
    return bool(key) and not key.lower().startswith("your")


def has_warehouse() -> bool:
    """True when the shared quantai-market-data Postgres DSN is configured."""
    return bool(os.environ.get("QUANTAI_MARKETDATA_DB_URL"))
