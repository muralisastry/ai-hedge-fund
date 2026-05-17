"""Minimal HTTP helper shared by the Polygon and SEC providers.

Deliberately small: a GET with bounded retry/backoff that never raises (returns
None on failure) so providers stay "return empty, don't throw" and the router
can fall back. When Phase 5 moves ingestion into quantai-market-data, those
calls will instead go through that package's rate-limited PolygonClient.
"""

from __future__ import annotations

import logging
import time

import requests

logger = logging.getLogger(__name__)


def get_json(url: str, params: dict | None = None, headers: dict | None = None, max_retries: int = 3, timeout: float = 30.0):
    """GET *url* and return parsed JSON, or None on any failure/non-200."""
    for attempt in range(max_retries + 1):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            logger.warning("HTTP error %s %s: %s", url, params, e)
            return None

        if resp.status_code == 429 and attempt < max_retries:
            delay = 2**attempt  # 1s, 2s, 4s
            time.sleep(delay)
            continue
        if resp.status_code != 200:
            logger.warning("GET %s -> %d", url, resp.status_code)
            return None
        try:
            return resp.json()
        except ValueError as e:
            logger.warning("Bad JSON from %s: %s", url, e)
            return None
    return None
