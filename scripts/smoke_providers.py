#!/usr/bin/env python3
"""Live provider data smoke (interim verification, no LLM/DB/FD).

Proves Phases 2-4 work against real data: Polygon news, SEC Form 4 insider,
Polygon/SEC line items, locally-computed metrics. Routes per data type via env
(process-only, never writes .env), with FD fallback OFF so failures are loud.

    python scripts/smoke_providers.py
    python scripts/smoke_providers.py --ticker MSFT

Exit 0 only if every required check passes (data present where expected and
Polygon/SEC reachable). Price-derived metrics (market_cap, P/E, EV) are
expected None here — no internal prices DB — and are reported, not failed.
"""

from __future__ import annotations

import argparse
import os
import sys

CORE_ITEMS = ["revenue", "net_income", "gross_profit", "operating_income",
              "total_assets", "shareholders_equity", "free_cash_flow"]


def _load_api(routing: dict):
    """Re-import src.tools.api fresh under the given DATA_PROVIDER_* routing."""
    os.environ["DATA_PROVIDER_FALLBACK_FD"] = "false"
    os.environ.setdefault("SEC_EDGAR_USER_AGENT", "quantai-trading murali.n.sastry@gmail.com")
    for k, v in routing.items():
        os.environ[k] = v
    for mod in [m for m in list(sys.modules) if m.startswith("src.tools")]:
        del sys.modules[mod]
    from src.tools import api  # noqa: WPS433  re-import under new env
    return api


def _line(ok: bool, label: str, detail: str) -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {detail}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="Live provider data smoke")
    ap.add_argument("--ticker", default="AAPL")
    ap.add_argument("--end", default="2026-05-15")
    ap.add_argument("--start", default="2024-01-01")
    args = ap.parse_args()
    t, end, start = args.ticker, args.end, args.start
    ok = True

    print(f"\n=== Provider smoke: {t} (news/insider since {start}, as of {end}) ===\n")

    # 1. News -> Polygon
    print("Polygon news (DATA_PROVIDER_COMPANY_NEWS=polygon)")
    try:
        api = _load_api({"DATA_PROVIDER_COMPANY_NEWS": "polygon"})
        news = api.get_company_news(t, end, start_date=start, limit=25)
        n_sent = sum(1 for x in news if x.sentiment)
        ok &= _line(bool(news) and all(x.url for x in news),
                    "news", f"{len(news)} items, {n_sent} with sentiment"
                    + (f", e.g. {news[0].source!r}: {news[0].title[:60]!r}" if news else ""))
    except Exception as e:
        ok = _line(False, "news", f"error: {e!r}")

    # 2. Insider -> SEC Form 4
    print("\nSEC Form 4 insider (DATA_PROVIDER_INSIDER_TRADES=sec)")
    try:
        api = _load_api({"DATA_PROVIDER_INSIDER_TRADES": "sec"})
        ins = api.get_insider_trades(t, end, start_date=start, limit=25)
        named = [x for x in ins if x.name and x.transaction_shares is not None]
        ok &= _line(bool(named), "insider",
                    f"{len(ins)} filings, {len(named)} with name+shares"
                    + (f", e.g. {named[0].name!r} {named[0].transaction_shares:+.0f} sh" if named else ""))
    except Exception as e:
        ok = _line(False, "insider", f"error: {e!r}")

    # 3. Line items -> Polygon, then SEC
    for prov in ("polygon", "sec"):
        print(f"\nLine items (DATA_PROVIDER_LINE_ITEMS={prov})")
        try:
            api = _load_api({"DATA_PROVIDER_LINE_ITEMS": prov})
            rows = api.search_line_items(t, CORE_ITEMS, end, period="annual", limit=4)
            top = rows[0] if rows else None
            rev = getattr(top, "revenue", None) if top else None
            ni = getattr(top, "net_income", None) if top else None
            present = sorted(k for k in (top.model_dump() if top else {})
                             if k in CORE_ITEMS and getattr(top, k, None) is not None)
            good = bool(rows) and (rev or 0) > 0
            # SEC is the required cross-check; Polygon may be plan-tier gated.
            (ok := ok and good) if prov == "sec" else None
            _line(good, f"line_items[{prov}]",
                  f"{len(rows)} periods; top={getattr(top,'report_period','-')} "
                  f"revenue={rev} net_income={ni} present={present}")
        except Exception as e:
            if prov == "sec":
                ok = False
            _line(False, f"line_items[{prov}]", f"error: {e!r}")

    # 4. Computed metrics -> metrics provider (line-item-derived; price-derived None here)
    print("\nComputed metrics (DATA_PROVIDER_FINANCIAL_METRICS=metrics)")
    try:
        api = _load_api({"DATA_PROVIDER_FINANCIAL_METRICS": "metrics"})
        m = api.get_financial_metrics(t, end, period="annual", limit=2)
        if not m:
            ok = _line(False, "metrics", "no metrics returned")
        else:
            r = m[0]
            derived = {k: getattr(r, k) for k in
                       ("net_margin", "gross_margin", "return_on_equity",
                        "return_on_assets", "revenue_growth")}
            have = {k: v for k, v in derived.items() if v is not None}
            ok &= _line(len(have) >= 3, "metrics(line-item-derived)",
                        f"{r.report_period}: " + ", ".join(f"{k}={v:.4g}" for k, v in have.items()))
            print(f"       (expected None w/o prices DB: market_cap={r.market_cap}, "
                  f"P/E={r.price_to_earnings_ratio}, EV={r.enterprise_value})")
    except Exception as e:
        ok = _line(False, "metrics", f"error: {e!r}")

    print(f"\n=== {'SMOKE PASS' if ok else 'SMOKE FAIL'} ===")
    print("(routing was process-env only; .env unchanged, defaults still fd)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
