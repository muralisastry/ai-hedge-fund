#!/usr/bin/env python3
"""Field-level parity harness: Financial Datasets vs. a candidate provider.

Workflow per migration phase:

  1. Capture golden fixtures from FD (once, with a valid FINANCIAL_DATASETS_API_KEY):
       python scripts/data_parity.py capture --provider fd

  2. After implementing a provider, diff it against the golden fixtures:
       python scripts/data_parity.py compare --provider internal

A data type is "green" (safe to flip its default) when ``compare`` reports no
mismatches outside tolerance for the ticker basket.

Numeric fields are compared with a relative tolerance (default 0.5%); OHLCV,
share counts and identifiers are compared exactly. The harness never raises on
missing API keys / network — it reports and exits non-zero so CI can gate.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

# Default basket: large caps, a bank, energy, a small-cap, an ADR, a recent IPO.
DEFAULT_TICKERS = ["AAPL", "MSFT", "NVDA", "JPM", "XOM", "ETSY", "TSM", "ARM"]
DEFAULT_START = "2023-01-01"
DEFAULT_END = "2024-01-01"
FIXTURE_DIR = Path(__file__).resolve().parent / "parity_fixtures"

# Line items requested across all agents (union); see the migration plan.
LINE_ITEMS = [
    "capital_expenditure",
    "depreciation_and_amortization",
    "net_income",
    "outstanding_shares",
    "total_assets",
    "total_liabilities",
    "shareholders_equity",
    "dividends_and_other_cash_distributions",
    "issuance_or_purchase_of_equity_shares",
    "gross_profit",
    "revenue",
    "free_cash_flow",
    "operating_income",
    "operating_margin",
    "gross_margin",
    "ebit",
    "ebitda",
    "current_assets",
    "current_liabilities",
    "cash_and_equivalents",
    "total_debt",
    "working_capital",
    "interest_expense",
    "operating_expense",
    "research_and_development",
    "goodwill_and_intangible_assets",
    "earnings_per_share",
    "book_value_per_share",
    "return_on_invested_capital",
    "debt_to_equity",
    "annual",
]

EXACT_FIELDS = {"open", "close", "high", "low", "volume", "time", "ticker", "report_period", "period", "currency", "filing_date", "date", "url", "outstanding_shares", "weighted_average_shares"}


def _serialize(obj):
    if obj is None:
        return None
    if isinstance(obj, list):
        return [_serialize(o) for o in obj]
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return obj


def _calls(api, ticker: str, start: str, end: str) -> dict:
    """Every public function's output for one ticker, JSON-serializable."""
    return {
        "get_prices": _serialize(api.get_prices(ticker, start, end)),
        "get_financial_metrics": _serialize(api.get_financial_metrics(ticker, end)),
        "search_line_items": _serialize(api.search_line_items(ticker, LINE_ITEMS, end)),
        "get_insider_trades": _serialize(api.get_insider_trades(ticker, end, start_date=start)),
        "get_company_news": _serialize(api.get_company_news(ticker, end, start_date=start)),
        "get_market_cap": api.get_market_cap(ticker, end),
    }


def _load_api(provider: str):
    """Import a fresh src.tools.api bound to *provider* (forces all data types)."""
    os.environ["DATA_PROVIDER"] = provider
    os.environ.setdefault("DATA_PROVIDER_FALLBACK_FD", "false")
    for mod in [m for m in list(sys.modules) if m.startswith("src.tools")]:
        del sys.modules[mod]
    from src.tools import api  # noqa: WPS433  (re-import with new env)

    return api


def _num_mismatch(a, b, tol: float) -> bool:
    if a is None and b is None:
        return False
    if a is None or b is None:
        return True
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return a != b
    if math.isnan(a) and math.isnan(b):
        return False
    denom = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / denom > tol


def _diff(path: str, base, cand, tol: float, out: list):
    if isinstance(base, dict) and isinstance(cand, dict):
        for k in sorted(set(base) | set(cand)):
            _diff(f"{path}.{k}", base.get(k), cand.get(k), tol, out)
        return
    if isinstance(base, list) and isinstance(cand, list):
        if len(base) != len(cand):
            out.append(f"{path}: length {len(base)} != {len(cand)}")
        for i, (b, c) in enumerate(zip(base, cand)):
            _diff(f"{path}[{i}]", b, c, tol, out)
        return
    field = path.rsplit(".", 1)[-1].split("[")[0]
    if field in EXACT_FIELDS or isinstance(base, str) or isinstance(cand, str):
        if base != cand:
            out.append(f"{path}: {base!r} != {cand!r}")
    elif _num_mismatch(base, cand, tol):
        out.append(f"{path}: {base!r} != {cand!r} (>{tol:.1%})")


def cmd_capture(args) -> int:
    api = _load_api(args.provider)
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for ticker in args.tickers:
        data = _calls(api, ticker, args.start, args.end)
        path = FIXTURE_DIR / f"{args.provider}_{ticker}.json"
        path.write_text(json.dumps(data, indent=2, default=str))
        n = sum(len(v) for v in data.values() if isinstance(v, list))
        print(f"captured {ticker}: {n} records -> {path.name}")
    return 0


def cmd_compare(args) -> int:
    api = _load_api(args.provider)
    total_issues = 0
    for ticker in args.tickers:
        base_path = FIXTURE_DIR / f"{args.baseline}_{ticker}.json"
        if not base_path.exists():
            print(f"SKIP {ticker}: no baseline ({base_path.name}) — run `capture` first")
            continue
        baseline = json.loads(base_path.read_text())
        candidate = _calls(api, ticker, args.start, args.end)
        issues: list[str] = []
        for fn in baseline:
            _diff(f"{ticker}.{fn}", baseline[fn], candidate.get(fn), args.tolerance, issues)
        if issues:
            total_issues += len(issues)
            print(f"\n✗ {ticker}: {len(issues)} mismatch(es)")
            for line in issues[:40]:
                print(f"    {line}")
            if len(issues) > 40:
                print(f"    … +{len(issues) - 40} more")
        else:
            print(f"✓ {ticker}: parity OK")
    print(f"\n{'PARITY FAILED' if total_issues else 'PARITY OK'}: {total_issues} total mismatch(es)")
    return 1 if total_issues else 0


def main() -> int:
    p = argparse.ArgumentParser(description="FD-vs-provider data parity harness")
    sub = p.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--tickers", nargs="+", default=DEFAULT_TICKERS)
    common.add_argument("--start", default=DEFAULT_START)
    common.add_argument("--end", default=DEFAULT_END)

    c = sub.add_parser("capture", parents=[common], help="snapshot a provider's output as golden fixtures")
    c.add_argument("--provider", default="fd")
    c.set_defaults(func=cmd_capture)

    d = sub.add_parser("compare", parents=[common], help="diff a provider against captured fixtures")
    d.add_argument("--provider", required=True, help="candidate provider, e.g. internal")
    d.add_argument("--baseline", default="fd", help="fixture prefix to diff against (default fd)")
    d.add_argument("--tolerance", type=float, default=0.005, help="relative numeric tolerance (default 0.5%%)")
    d.set_defaults(func=cmd_compare)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
