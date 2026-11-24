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
from datetime import date, datetime, timedelta

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


def fetch_statement(token: str, query_id: str, attempts: int = 30, max_wait: int = 30) -> str:
    """Generate the Flex Query report and return its XML text.

    IBKR can take several minutes to build a long report, so this keeps asking for
    up to about 14 minutes, waiting a little longer each time.
    """
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
    print("IBKR accepted the request; waiting for the statement...", flush=True)

    waited, err = 0, None
    for i in range(attempts):
        delay = min(max_wait, 5 * (i + 1))
        time.sleep(delay)
        waited += delay
        body = _get("GetStatement", {"t": token, "q": ref.strip(), "v": "3"})
        err = _error(body)
        if err is None:
            print(f"Statement ready after {waited}s", flush=True)
            return body
        print(f"  {waited}s: IBKR says {err[0]} {err[1]}", flush=True)
        if err[0] not in RETRYABLE:
            raise FlexError(f"GetStatement failed: {err[0]} {err[1]}")
    raise FlexError(f"IBKR had not produced the statement after {waited // 60} minutes "
                    f"(last reply {err[0]} {err[1]}). Try a shorter Period in the Flex Query.")


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


def _key(name: str) -> str:
    """Normalise a field name so 'ClientAccountID', 'accountId' and 'Account ID' compare equal."""
    k = "".join(ch for ch in name.lower() if ch.isalnum())
    return "accountid" if k in ("clientaccountid", "accountid", "account") else k


def _records(text: str):
    """Yield (kind, fields) for every row of an XML or CSV Flex report, with normalised field names.

    kind is the XML tag, or "csv" for CSV rows. In XML, a row without an account
    number takes the account of the FlexStatement it sits in.
    """
    body = text.lstrip("\ufeff \r\n\t")
    if body.startswith("<"):
        root = ET.fromstring(body)
        for st in list(root.iter("FlexStatement")) or [root]:
            for el in st.iter():
                if el is st or not el.attrib:
                    continue
                f = {_key(k): v for k, v in el.attrib.items()}
                if not f.get("accountid"):
                    f["accountid"] = st.get("accountId", "")
                yield el.tag, f
    else:
        import csv
        import io
        header = None
        for row in csv.reader(io.StringIO(body)):
            if not row or not any(c.strip() for c in row):
                continue
            keys = [_key(c) for c in row]
            if "accountid" in keys and not any(_num(c) is not None for c in row):
                header = keys          # IBKR repeats the header before each section/day
                continue
            if header:
                yield "csv", dict(zip(header, row))


def report_kind(f: dict) -> str | None:
    if {"startingvalue", "endingvalue", "todate"} <= f.keys():
        return "changeinnav"           # Change in NAV, one row per account per day with Breakout by Day
    if {"reportdate", "total"} <= f.keys() and "symbol" not in f:
        return "navsummary"            # Net Asset Value (NAV) in Base
    if {"type", "amount"} <= f.keys():
        return "cash"                  # Cash Transactions
    return None


def parse_statement(text: str):
    """Parse an Activity Flex Query report (XML or CSV).

    Uses the daily Change in NAV section if present; otherwise the NAV in Base
    section plus Cash Transactions (Deposits/Withdrawals).

    Returns (nav, flows, generated):
      nav[account][date] = account value at the close, in base currency
      flows[account][date] = net deposits (+) / withdrawals (-) in base currency
      generated = report generation timestamp string, if present
    """
    recs = list(_records(text))
    change = [f for _, f in recs if report_kind(f) == "changeinnav"]
    nav: dict[str, dict[date, float]] = defaultdict(dict)
    flows: dict[str, dict[date, float]] = defaultdict(lambda: defaultdict(float))

    if change:
        first: dict[str, tuple[date, float]] = {}
        for f in change:
            acct = (f.get("accountid") or "").strip()
            d, start, end = parse_date(f.get("todate")), _num(f.get("startingvalue")), _num(f.get("endingvalue"))
            if not (acct and d and end is not None):
                continue
            if parse_date(f.get("fromdate")) not in (None, d):
                continue  # a whole-period row, not a daily one
            nav[acct][d] = end
            flow = _num(f.get("depositswithdrawals")) or 0.0
            if flow:
                flows[acct][d] += flow
            if start is not None and (acct not in first or d < first[acct][0]):
                first[acct] = (d, start)
        # The day before an account's first row closed at that row's starting value.
        for acct, (d, start) in first.items():
            nav[acct].setdefault(d - timedelta(days=1), start)
    else:
        for tag, f in recs:
            kind = report_kind(f)
            acct = (f.get("accountid") or "").strip()
            if kind == "navsummary" and tag in ("csv", "EquitySummaryByReportDateInBase"):
                d, total = parse_date(f.get("reportdate")), _num(f.get("total"))
                if acct and d and total is not None:
                    nav[acct][d] = total
            elif kind == "cash":
                t = (f.get("type") or "").lower()
                if ("deposit" not in t and "withdraw" not in t) or (f.get("levelofdetail") or "DETAIL").upper() != "DETAIL":
                    continue
                d = parse_date(f.get("reportdate")) or parse_date(f.get("datetime")) or parse_date(f.get("settledate"))
                amount = _num(f.get("amount"))
                if acct and d and amount is not None:
                    flows[acct][d] += amount * (_num(f.get("fxratetobase")) or 1.0)

    generated = next((f.get("whengenerated") for t, f in recs if f.get("whengenerated")), None)
    if generated is None and text.lstrip().startswith("<"):
        st = ET.fromstring(text.lstrip("\ufeff \r\n\t")).find(".//FlexStatement")
        generated = st.get("whenGenerated") if st is not None else None
    return dict(nav), {a: dict(f) for a, f in flows.items()}, generated


def flows_between(flows: dict[date, float], start: date, end: date) -> float:
    """Sum of flows dated after `start` and on or before `end` (weekend flows roll to the next NAV day)."""
    if not flows:
        return 0.0
    days = sorted(flows)
    lo, hi = bisect_right(days, start), bisect_right(days, end)
    return sum(flows[d] for d in days[lo:hi])
