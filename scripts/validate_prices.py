#!/usr/bin/env python3
"""Validate internal-provider prices against yfinance (Phase 1 baseline).

Per the migration plan there is no trustworthy Financial Datasets baseline in
this environment, so prices from the ``internal`` provider (quantai-market-data,
Polygon-backed, split/dividend-adjusted) are validated against
yfinance-adjusted close for a ticker basket, plus a few hand-checked anchors.

Run on a host where ``quantai_market_data`` is installed and
``QUANTAI_MARKETDATA_DB_URL`` + ``POLYGON_API_KEY`` are configured:

    python scripts/validate_prices.py
    python scripts/validate_prices.py --tickers AAPL MSFT --tolerance 0.01

Exit code is non-zero if any ticker is outside tolerance or data is missing,
so this can gate the default flip to the internal provider.
"""

from __future__ import annotations

import argparse
import sys

DEFAULT_TICKERS = ["AAPL", "MSFT", "NVDA", "JPM", "XOM"]
START = "2023-06-01"
END = "2023-12-31"

# Hand-checked anchors (yfinance-adjusted close, ~). Loose tolerance: these
# guard gross errors (wrong ticker, unadjusted series, off-by-100), not pennies.
ANCHORS = {
    # ticker: (date, approx_adjusted_close, abs_tol)
    "AAPL": ("2023-12-29", 192.0, 6.0),
}


def _internal_prices(ticker: str):
    from src.tools.providers.internal import InternalProvider

    rows = InternalProvider().get_prices(ticker, START, END)
    return {p.time[:10]: p.close for p in rows}


def _yf_prices(ticker: str):
    import yfinance as yf

    df = yf.download(ticker, start=START, end=END, auto_adjust=True, progress=False)
    if df is None or df.empty:
        return {}
    close = df["Close"]
    if hasattr(close, "columns"):  # MultiIndex when multiple tickers
        close = close.iloc[:, 0]
    return {d.strftime("%Y-%m-%d"): float(v) for d, v in close.items()}


def main() -> int:
    # The internal provider needs QUANTAI_MARKETDATA_DB_URL, which lives in
    # .env (not the shell env) — load it before any provider import/call.
    from dotenv import load_dotenv

    load_dotenv()

    ap = argparse.ArgumentParser(description="Validate internal-provider prices against yfinance.")
    ap.add_argument("--tickers", nargs="+", default=DEFAULT_TICKERS)
    ap.add_argument("--tolerance", type=float, default=0.02, help="relative tolerance (default 0.02 = 2 pct)")
    args = ap.parse_args()

    try:
        import yfinance  # noqa: F401
    except ImportError:
        print('yfinance not installed — run: .venv/bin/pip install -e ".[dev]"')
        return 2

    failures = 0
    for ticker in args.tickers:
        internal = _internal_prices(ticker)
        if not internal:
            print(f"✗ {ticker}: internal provider returned no data " f"(check quantai_market_data install + QUANTAI_MARKETDATA_DB_URL + POLYGON_API_KEY)")
            failures += 1
            continue
        yf = _yf_prices(ticker)
        if not yf:
            print(f"✗ {ticker}: yfinance returned no data (network?)")
            failures += 1
            continue

        common = sorted(set(internal) & set(yf))
        if not common:
            print(f"✗ {ticker}: no overlapping dates between internal and yfinance")
            failures += 1
            continue

        worst = max(abs(internal[d] - yf[d]) / max(yf[d], 1e-9) for d in common)
        status = "✓" if worst <= args.tolerance else "✗"
        print(f"{status} {ticker}: {len(common)} days, worst rel diff {worst:.2%} " f"(tol {args.tolerance:.0%})")
        if worst > args.tolerance:
            failures += 1

        if ticker in ANCHORS:
            d, approx, abs_tol = ANCHORS[ticker]
            got = internal.get(d)
            if got is None or abs(got - approx) > abs_tol:
                print(f"  ✗ anchor {ticker} {d}: got {got}, expected ~{approx} ±{abs_tol}")
                failures += 1
            else:
                print(f"  ✓ anchor {ticker} {d}: {got:.2f} ~ {approx}")

    print(f"\n{'VALIDATION FAILED' if failures else 'VALIDATION OK'}: {failures} issue(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
