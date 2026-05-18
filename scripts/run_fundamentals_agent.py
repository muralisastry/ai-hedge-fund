#!/usr/bin/env python3
"""Run the fundamentals analyst agent standalone against the new providers.

The `fundamentals` analyst is rule-based (no LLM) and calls only
`get_financial_metrics`, so it runs fully off the `metrics` provider
(Polygon vX financials -> SEC fallback -> local _derive) with no prices DB,
no FD, no LLM. It is invoked DIRECTLY (not via the graph) because
create_workflow() always adds the price-dependent risk/portfolio nodes.

    .venv/bin/python scripts/run_fundamentals_agent.py --tickers AAPL MSFT

Routing is set in process env only (never writes .env); defaults stay `fd`.
Exit non-zero if the agent raises or yields no signal for a requested ticker.

Known degradation: P/E, P/B, P/S are price-derived and are None without the
prices DB. The agent's price-ratios sub-signal scores 0 in that case, which it
treats as "not overvalued" => **bullish**. This is the agent's real behavior;
it is reported explicitly, not hidden.
"""

from __future__ import annotations

import argparse
import os
import sys


def _bootstrap_env() -> None:
    """Set provider routing BEFORE importing src.* (fresh process, no reload)."""
    from dotenv import load_dotenv

    load_dotenv()  # brings in POLYGON_API_KEY from .env
    os.environ["DATA_PROVIDER_FINANCIAL_METRICS"] = "metrics"
    os.environ["DATA_PROVIDER_FALLBACK_FD"] = "false"
    os.environ.setdefault("SEC_EDGAR_USER_AGENT", "quantai-trading murali.n.sastry@gmail.com")


def main() -> int:
    ap = argparse.ArgumentParser(description="Standalone fundamentals-agent run")
    ap.add_argument("--tickers", nargs="+", default=["AAPL", "MSFT"])
    ap.add_argument("--end", default="2026-05-15")
    ap.add_argument("--show-reasoning", action="store_true")
    args = ap.parse_args()

    _bootstrap_env()

    from langchain_core.messages import HumanMessage

    from src.agents.fundamentals import fundamentals_analyst_agent
    from src.utils.progress import progress

    state = {
        "messages": [HumanMessage(content="Analyze fundamentals")],
        "data": {
            "tickers": args.tickers,
            "end_date": args.end,
            "analyst_signals": {},
        },
        "metadata": {"show_reasoning": args.show_reasoning},  # accessed unconditionally
    }

    print(f"\n=== Fundamentals agent — {args.tickers} as of {args.end} "
          f"(financial_metrics -> metrics provider, FD fallback off) ===\n")

    progress.start()
    try:
        result = fundamentals_analyst_agent(state)
    except Exception as e:  # surface, don't swallow
        progress.stop()
        print(f"[FAIL] agent raised: {e!r}")
        return 1
    finally:
        progress.stop()

    signals = result["data"]["analyst_signals"].get("fundamentals_analyst_agent", {})
    failures = 0
    for t in args.tickers:
        sig = signals.get(t)
        if not sig:
            print(f"[FAIL] {t}: no signal (no financial metrics from the metrics provider)")
            failures += 1
            continue
        print(f"[PASS] {t}: {sig['signal'].upper()} (confidence {sig['confidence']:.0f}%)")
        for key in ("profitability_signal", "growth_signal",
                    "financial_health_signal", "price_ratios_signal"):
            sub = sig["reasoning"].get(key, {})
            detail = sub.get("details", "")
            degraded = " [DEGRADED: no prices DB -> 0 score treated as bullish]" \
                if key == "price_ratios_signal" and "P/E: N/A" in detail else ""
            print(f"    {key:24s} {sub.get('signal','?'):8s} {detail}{degraded}")
        print()

    ok = failures == 0
    print(f"=== {'AGENT RUN OK' if ok else 'AGENT RUN FAILED'} "
          f"({len(args.tickers) - failures}/{len(args.tickers)} tickers) ===")
    print("(routing was process-env only; .env unchanged, defaults still fd)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
