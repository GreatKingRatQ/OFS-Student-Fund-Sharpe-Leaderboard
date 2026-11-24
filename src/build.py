"""Build the OFS Sharpe leaderboard page.

  python3 src/build.py                      # live: needs IBKR_FLEX_TOKEN and IBKR_FLEX_QUERY_ID
  python3 src/build.py --input report.xml   # from a Flex XML file you downloaded
  python3 src/build.py --demo               # invented sample data, for a preview

Writes site/index.html (a single self-contained page). Account numbers and NAV
balances are never written to the page, only return-based statistics.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import flex  # noqa: E402
import metrics  # noqa: E402

ROOT = HERE.parent
WINDOWS = {"inception": None, "1m": 21}   # None = all history; 21 trading days ~ one month
MIN_DAYS_1M = 15


def load_members(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if (r.get("name") or "").strip()]
    return [{k: (v or "").strip() for k, v in r.items()} for r in rows]


def load_manual_flows(path: Path) -> dict:
    out: dict = {}
    if not path.exists():
        return out
    with path.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            d, acct = flex.parse_date(r.get("date")), (r.get("account_id") or "").strip()
            if d and acct and r.get("amount"):
                out.setdefault(acct, {}).setdefault(d, 0.0)
                out[acct][d] += float(r["amount"])
    return out


def window_stats(series, n, rf, min_days):
    s = series if n is None else series[-n:]
    stats = metrics.summarise([r for _, r, _ in s], rf)
    stats["start"] = s[0][0].isoformat() if s else None
    stats["ranked"] = stats["sharpe"] is not None and stats["days"] >= min_days
    return stats


def r4(x):
    return None if x is None else round(x, 4)


def describe_report(text: str) -> None:
    """Print the shape of the IBKR report (format, row types, fields, dates). No values."""
    from collections import Counter
    recs = list(flex._records(text))
    fmt = "XML" if text.lstrip("\ufeff \r\n\t").startswith("<") else "CSV"
    kinds = Counter(flex.report_kind(f) or (t if t != "csv" else "other") for t, f in recs)
    print(f"IBKR report: {fmt}, rows by type: " + ", ".join(f"{k}={n}" for k, n in kinds.most_common(12)))
    for want in ("changeinnav", "navsummary"):
        rows = [f for _, f in recs if flex.report_kind(f) == want]
        if rows:
            dates = sorted(filter(None, (flex.parse_date(f.get("todate") or f.get("reportdate")) for f in rows)))
            print(f"  {want} fields: {', '.join(sorted(rows[0]))}")
            if dates:
                print(f"  {want} dates: {dates[0]} to {dates[-1]}")
    if not kinds.get("changeinnav") and not kinds.get("navsummary"):
        print("  ! No daily account values found. Add 'Change in NAV' (with Breakout by Day) "
              "or 'Net Asset Value (NAV) in Base' to the Flex Query.")


def build(xml_text: str, members: list[dict], manual: dict, rf: float, min_days: int, demo: bool, rf_label: str):
    if not demo:
        describe_report(xml_text)
    nav, flows, generated = flex.parse_statement(xml_text)
    for acct, extra in manual.items():
        f = flows.setdefault(acct, {})
        for d, amt in extra.items():
            f[d] = f.get(d, 0.0) + amt

    warnings, out_members, all_series = [], [], []
    known = {m["account_id"] for m in members if m.get("account_id")}
    for acct in sorted(set(nav) - known):
        warnings.append(f"Account {acct} is in the IBKR report but not in members.csv; ignored.")

    print(f"IBKR report: daily values for {len(nav)} account(s): {', '.join(sorted(nav)) or 'none'}")
    for m in members:
        acct = m.get("account_id", "")
        days = sorted(nav.get(acct, {}))
        series = metrics.daily_returns(nav.get(acct, {}), flows.get(acct, {})) if acct else []
        if not acct:
            note = "No IBKR account linked yet"
        elif not days:
            note = "Account not found in the IBKR report"
        elif len(days) < 2:
            note = "IBKR sent only one day of values"
        elif not series:
            note = "Account not funded yet"
        elif all(r == 0 for _, r, _ in series):
            note = "No trades yet"
        else:
            note = ""
        print(f"  {m['name']} ({acct or 'no account'}): {len(days)} daily values"
              + (f" {days[0]} to {days[-1]}" if days else "")
              + f", {len(flows.get(acct, {}))} deposit day(s), {len(series)} returns. {note}")
        for d, r, _ in series:
            if abs(r) > 0.25:
                warnings.append(f"{m['name']}: {r:+.1%} on {d}. If that was a cash transfer, add it to manual_flows.csv.")
        if series:
            all_series.append(series)
        wins = {
            key: {k: (r4(v) if isinstance(v, float) else v)
                  for k, v in window_stats(series, n, rf, min_days if n is None else MIN_DAYS_1M).items()}
            for key, n in WINDOWS.items()
        }
        out_members.append({
            "name": m["name"],
            "role": m.get("role", ""),
            "windows": wins,
            "note": note,
            "spark": [round(v, 4) for v in metrics.growth_index([r for _, r, _ in series])] if series else [],
        })

    fund_series = metrics.combine(all_series)
    fund = {key: {k: (r4(v) if isinstance(v, float) else v)
                  for k, v in window_stats(fund_series, n, rf, 2).items()}
            for key, n in WINDOWS.items()}
    as_of = max((s[-1][0] for s in all_series), default=None)

    data = {
        "demo": demo,
        "as_of": as_of.isoformat() if as_of else None,
        "built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "report_generated": generated,
        "rf": rf,
        "rf_label": rf_label,
        "min_days": min_days,
        "fund": fund,
        "members": out_members,
    }
    return data, warnings


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", help="Flex Query XML file instead of calling IBKR")
    ap.add_argument("--demo", action="store_true", help="build with invented sample data")
    ap.add_argument("--members", default=str(ROOT / "members.csv"))
    ap.add_argument("--manual-flows", default=str(ROOT / "manual_flows.csv"))
    ap.add_argument("--out", default=str(ROOT / "site" / "index.html"))
    args = ap.parse_args()

    members = load_members(Path(args.members))
    if args.demo:
        import make_demo_data
        xml_text = make_demo_data.generate(members)
    elif args.input:
        xml_text = Path(args.input).read_text(encoding="utf-8")
    else:
        token, query = os.environ.get("IBKR_FLEX_TOKEN"), os.environ.get("IBKR_FLEX_QUERY_ID")
        if not token or not query:
            sys.exit("Set IBKR_FLEX_TOKEN and IBKR_FLEX_QUERY_ID, or use --input / --demo.")
        xml_text = flex.fetch_statement(token, query)

    rf = float(os.environ.get("RISK_FREE_RATE") or 0.04)
    rf_label = os.environ.get("RISK_FREE_LABEL") or "Bank of England Bank Rate"
    min_days = int(os.environ.get("MIN_DAYS") or 20)
    data, warnings = build(xml_text, members, load_manual_flows(Path(args.manual_flows)),
                           rf, min_days, args.demo, rf_label)

    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    html = (HERE / "template.html").read_text(encoding="utf-8").replace("/*__DATA__*/null", payload)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")

    ranked = sum(1 for m in data["members"] if m["windows"]["inception"]["ranked"])
    print(f"Wrote {out}: {len(data['members'])} members, {ranked} ranked, data as of {data['as_of']}")
    for w in warnings:
        print(f"::warning::{w}")


if __name__ == "__main__":
    main()
