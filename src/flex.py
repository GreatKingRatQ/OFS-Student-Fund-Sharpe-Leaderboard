"""Fetch and parse IBKR Flex Web Service reports. Standard library only.

Docs: https://www.interactivebrokers.com/campus/ibkr-api-page/flex-web-service
"""
from __future__ import annotations

import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from bisect import bisect_right
from collections import defaultdict
from datetime import date, datetime

BASE = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService"
# IBKR rejects requests without a User-Agent header (the value doesn't matter).
HEADERS = {"User-Agent": "ofs-sharpe-leaderboard/1.0"}
# Error codes that mean "try again shortly" (statement still generating, server busy, too many requests).
RETRYABLE = {"1001", "1004", "1005", "1006", "1007", "1008", "1009", "1018", "1019", "1021"}


class FlexError(RuntimeError):
    pass


def _get(path: str, params: dict, timeout: int = 90) -> str:
    url = f"{BASE}/{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _error(body: str) -> tuple[str, str] | None:
    """Return (code, message) if body is a FlexStatementResponse error, else None."""
    if "<FlexStatementResponse" not in body[:1000]:
        return None
    root = ET.fromstring(body)
    if root.findtext("Status") == "Success":
        return None
    return (root.findtext("ErrorCode") or "?").strip(), (root.findtext("ErrorMessage") or "").strip()


def fetch_statement(token: str, query_id: str, attempts: int = 15, wait: float = 6.0) -> str:
    """Generate the Flex Query report and return its XML text."""
    params = {"t": token, "q": query_id, "v": "3"}
    for i in range(4):
        body = _get("SendRequest", params)
        err = _error(body)
        if err is None:
            break
        if err[0] not in RETRYABLE or i == 3:
            raise FlexError(f"SendRequest failed: {err[0]} {err[1]}")
        time.sleep(10)
    ref = ET.fromstring(body).findtext("ReferenceCode")
    if not ref:
        raise FlexError("SendRequest returned no ReferenceCode")

    for i in range(attempts):
        time.sleep(3 if i == 0 else wait)
        body = _get("GetStatement", {"t": token, "q": ref.strip(), "v": "3"})
        err = _error(body)
        if err is None:
            return body
        if err[0] not in RETRYABLE:
            raise FlexError(f"GetStatement failed: {err[0]} {err[1]}")
    raise FlexError("Statement was still generating after all retries; try again later")


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    value = value.strip().split(";")[0].split(" ")[0].split("T")[0]
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%m/%d/%Y", "%d-%b-%y", "%d-%b-%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    return None


def _num(value: str | None) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(value.replace(",", ""))
    except ValueError:
        return None


def parse_statement(xml_text: str):
    """Parse an Activity Flex Query report.

    Returns (nav, flows, generated):
      nav[account][date] = total NAV in base currency (NAV Summary in Base section)
      flows[account][date] = net deposits (+) / withdrawals (-) in base currency
      generated = report generation timestamp string, if present
    """
    root = ET.fromstring(xml_text)
    nav: dict[str, dict[date, float]] = defaultdict(dict)
    flows: dict[str, dict[date, float]] = defaultdict(lambda: defaultdict(float))

    for el in root.iter("EquitySummaryByReportDateInBase"):
        acct, d, total = el.get("accountId"), parse_date(el.get("reportDate")), _num(el.get("total"))
        if acct and d and total is not None:
            nav[acct][d] = total

    for el in root.iter("CashTransaction"):
        kind = (el.get("type") or "").lower()
        if "deposit" not in kind and "withdraw" not in kind:
            continue
        if el.get("levelOfDetail", "DETAIL").upper() != "DETAIL":
            continue  # skip SUMMARY rows so nothing is counted twice
        acct = el.get("accountId")
        d = parse_date(el.get("reportDate")) or parse_date(el.get("dateTime")) or parse_date(el.get("settleDate"))
        amount = _num(el.get("amount"))
        if acct and d and amount is not None:
            flows[acct][d] += amount * (_num(el.get("fxRateToBase")) or 1.0)

    stmt = root.find(".//FlexStatement")
    generated = stmt.get("whenGenerated") if stmt is not None else None
    return dict(nav), {a: dict(f) for a, f in flows.items()}, generated


def flows_between(flows: dict[date, float], start: date, end: date) -> float:
    """Sum of flows dated after `start` and on or before `end` (weekend flows roll to the next NAV day)."""
    if not flows:
        return 0.0
    days = sorted(flows)
    lo, hi = bisect_right(days, start), bisect_right(days, end)
    return sum(flows[d] for d in days[lo:hi])
