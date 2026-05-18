"""SEC EDGAR provider (free, authoritative, no API key).

- ``get_insider_trades`` — parsed Form 4 ownership filings
- ``search_line_items`` — raw statement values from the XBRL ``companyfacts``
  API, used as the fallback when Polygon lacks a tag

SEC asks for a descriptive ``User-Agent``; set ``SEC_EDGAR_USER_AGENT`` (e.g.
``"quantai you@example.com"``). Everything returns empty on failure so the
router can fall back.
"""

from __future__ import annotations

import logging
import os
import xml.etree.ElementTree as ET

import requests

from src.data.models import InsiderTrade, LineItem
from src.tools.providers import _derive
from src.tools.providers._http import get_json

logger = logging.getLogger(__name__)

_UA = {"User-Agent": os.environ.get("SEC_EDGAR_USER_AGENT", "quantai-trading research@example.com")}

# us-gaap concept -> our raw line-item name (first present wins for synonyms).
_GAAP = {
    "Revenues": "revenue",
    "RevenueFromContractWithCustomerExcludingAssessedTax": "revenue",
    "GrossProfit": "gross_profit",
    "OperatingIncomeLoss": "operating_income",
    "NetIncomeLoss": "net_income",
    "ResearchAndDevelopmentExpense": "research_and_development",
    "OperatingExpenses": "operating_expense",
    "InterestExpense": "interest_expense",
    "Assets": "total_assets",
    "AssetsCurrent": "current_assets",
    "Liabilities": "total_liabilities",
    "LiabilitiesCurrent": "current_liabilities",
    "StockholdersEquity": "shareholders_equity",
    "CashAndCashEquivalentsAtCarryingValue": "cash_and_equivalents",
    "DepreciationDepletionAndAmortization": "depreciation_and_amortization",
    "DepreciationAndAmortization": "depreciation_and_amortization",
    "NetCashProvidedByUsedInOperatingActivities": "_operating_cash_flow",
    "PaymentsToAcquirePropertyPlantAndEquipment": "capital_expenditure",
    "LongTermDebtNoncurrent": "_long_term_debt",
    "LongTermDebt": "_long_term_debt",
    "EarningsPerShareBasic": "earnings_per_share",
    "WeightedAverageNumberOfSharesOutstandingBasic": "outstanding_shares",
    "Goodwill": "_goodwill",
    "IntangibleAssetsNetExcludingGoodwill": "_intangibles",
}

_cik_cache: dict[str, str] | None = None


def _cik(ticker: str) -> str | None:
    global _cik_cache
    if _cik_cache is None:
        data = get_json("https://www.sec.gov/files/company_tickers.json", headers=_UA)
        _cik_cache = {}
        if data:
            for row in data.values():
                _cik_cache[row["ticker"].upper()] = str(row["cik_str"]).zfill(10)
    return (_cik_cache or {}).get(ticker.upper())


class SECEdgarProvider:
    name = "sec"

    # ------------------------------------------------------------------
    # Insider trades (Phase 2) — Form 4
    # ------------------------------------------------------------------

    def get_insider_trades(self, ticker: str, end_date: str, start_date: str | None = None, limit: int = 1000, api_key: str | None = None) -> list[InsiderTrade]:
        cik = _cik(ticker)
        if not cik:
            return []
        subs = get_json(f"https://data.sec.gov/submissions/CIK{cik}.json", headers=_UA)
        if not subs:
            return []
        recent = subs.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accns = recent.get("accessionNumber", [])
        docs = recent.get("primaryDocument", [])
        cik_int = str(int(cik))

        out: list[InsiderTrade] = []
        for form, fdate, accn, doc in zip(forms, dates, accns, docs):
            if form != "4":
                continue
            if fdate > end_date:
                continue
            if start_date and fdate < start_date:
                break  # recent[] is newest-first; older than window -> stop
            a = accn.replace("-", "")
            # primaryDocument is the XSL-rendered HTML viewer (e.g.
            # "xslF345X06/form4.xml"); the raw ownershipDocument XML is the
            # same basename at the accession root.
            raw_doc = doc.rsplit("/", 1)[-1] if "/" in doc else doc
            url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{a}/{raw_doc}"
            out.extend(self._parse_form4(url, ticker, fdate))
            if len(out) >= limit:
                break
        return out[:limit]

    def _parse_form4(self, url: str, ticker: str, filing_date: str) -> list[InsiderTrade]:
        try:
            resp = requests.get(url, headers=_UA, timeout=30)
            if resp.status_code != 200:
                return []
            root = ET.fromstring(resp.content)
        except (requests.RequestException, ET.ParseError) as e:
            logger.debug("Form 4 parse failed %s: %s", url, e)
            return []

        def _txt(node, path):
            el = node.find(path)
            if el is None:
                return None
            v = el.findtext("value")
            return (v if v is not None else el.text or "").strip() or None

        issuer = _txt(root, "issuer/issuerName")
        owner = root.find("reportingOwner")
        name = _txt(owner, "reportingOwnerId/rptOwnerName") if owner is not None else None
        rel = owner.find("reportingOwnerRelationship") if owner is not None else None
        is_dir = (rel.findtext("isDirector") in ("1", "true")) if rel is not None else None
        title = (rel.findtext("officerTitle") or None) if rel is not None else None

        trades: list[InsiderTrade] = []
        for tx in root.findall(".//nonDerivativeTransaction"):

            def num(path):
                t = _txt(tx, path)
                try:
                    return float(t) if t is not None else None
                except ValueError:
                    return None

            shares = num("transactionAmounts/transactionShares")
            pps = num("transactionAmounts/transactionPricePerShare")
            after = num("postTransactionAmounts/sharesOwnedFollowingTransaction")
            acq_disp = _txt(tx, "transactionAmounts/transactionAcquiredDisposedCode")
            signed = shares if (shares is not None and acq_disp != "D") else (-shares if shares is not None else None)
            trades.append(
                InsiderTrade(
                    ticker=ticker,
                    issuer=issuer,
                    name=name,
                    title=title,
                    is_board_director=is_dir,
                    transaction_date=_txt(tx, "transactionDate"),
                    transaction_shares=signed,
                    transaction_price_per_share=pps,
                    transaction_value=(abs(signed) * pps) if (signed is not None and pps is not None) else None,
                    shares_owned_before_transaction=(after - signed) if (after is not None and signed is not None) else None,
                    shares_owned_after_transaction=after,
                    security_title=_txt(tx, "securityTitle"),
                    filing_date=filing_date,
                )
            )
        return trades

    # ------------------------------------------------------------------
    # Line items (Phase 3 fallback) — XBRL companyfacts
    # ------------------------------------------------------------------

    def _records(self, ticker: str, end_date: str, period: str, limit: int) -> list[dict]:
        cik = _cik(ticker)
        if not cik:
            return []
        facts = get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json", headers=_UA)
        if not facts:
            return []
        gaap = facts.get("facts", {}).get("us-gaap", {})
        want_forms = {"10-K"} if period == "annual" else {"10-Q", "10-K"}

        # period_end -> {raw line item -> value}, keeping the latest-filed value.
        by_period: dict[str, dict] = {}
        filed_at: dict[tuple, str] = {}
        for concept, our in _GAAP.items():
            node = gaap.get(concept)
            if not node:
                continue
            for unit_rows in node.get("units", {}).values():
                for row in unit_rows:
                    if row.get("form") not in want_forms:
                        continue
                    end = row.get("end")
                    if not end or end > end_date:
                        continue
                    key = (end, our)
                    if key in filed_at and row.get("filed", "") <= filed_at[key]:
                        continue  # keep the most recently filed value (handles restatements)
                    filed_at[key] = row.get("filed", "")
                    by_period.setdefault(end, {"report_period": end, "period": period, "currency": "USD"})[our] = row.get("val")

        records = sorted(by_period.values(), key=lambda r: r["report_period"], reverse=True)
        for rec in records:
            if "_goodwill" in rec or "_intangibles" in rec:
                gi = (rec.get("_goodwill") or 0) + (rec.get("_intangibles") or 0)
                rec["goodwill_and_intangible_assets"] = gi or None
            _derive.enrich_raw(rec)
        return records[:limit]

    def search_line_items(self, ticker: str, line_items: list[str], end_date: str, period: str = "ttm", limit: int = 10, api_key: str | None = None) -> list[LineItem]:
        # companyfacts has annual + quarterly points; ttm is approximated as annual here.
        eff_period = "annual" if period == "ttm" else period
        rows = self._records(ticker, end_date, eff_period, limit)
        out: list[LineItem] = []
        for rec in rows:
            payload = {"ticker": ticker, "report_period": rec["report_period"], "period": period, "currency": "USD"}
            for name in line_items:
                if name == "annual":
                    payload["annual"] = eff_period == "annual"
                elif name in rec and not name.startswith("_"):
                    payload[name] = rec[name]
            out.append(LineItem(**payload))
        return out
