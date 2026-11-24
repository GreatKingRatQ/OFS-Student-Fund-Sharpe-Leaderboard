"""Invented Flex-style XML for previewing the leaderboard. Not real performance."""
from __future__ import annotations

import random
from datetime import date, timedelta


def business_days(start: date, end: date):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def generate(members: list[dict], start=date(2026, 10, 9), end=date(2026, 11, 20), seed=7) -> str:
    rng = random.Random(seed)
    days = list(business_days(start, end))
    market = {d: rng.gauss(0.0004, 0.008) for d in days}
    statements = []
    for i, m in enumerate(members):
        acct = m.get("account_id")
        if not acct:
            continue
        alloc = float(m.get("allocation") or 1000)
        beta, drift, idio = rng.uniform(0.5, 1.4), rng.gauss(0.0003, 0.0009), rng.uniform(0.004, 0.016)
        nav_rows, cash_rows = [], []
        nav = 0.0
        for j, d in enumerate(days):
            if j == 1:  # funded on the first Monday
                nav = alloc
                cash_rows.append((d, alloc))
            elif j > 1:
                nav *= 1 + beta * market[d] + drift + rng.gauss(0, idio)
                if i == 5 and d == date(2026, 11, 2):  # one mid-period top-up, to exercise flow adjustment
                    nav += 500
                    cash_rows.append((d, 500.0))
            nav_rows.append(f'<EquitySummaryByReportDateInBase accountId="{acct}" reportDate="{d:%Y%m%d}" '
                            f'cash="0" stock="0" total="{nav:.2f}" />')
        cash = "".join(
            f'<CashTransaction accountId="{acct}" type="Deposits/Withdrawals" reportDate="{d:%Y%m%d}" '
            f'dateTime="{d:%Y%m%d};120000" amount="{a:.2f}" currency="GBP" fxRateToBase="1" '
            f'description="DEMO TRANSFER" levelOfDetail="DETAIL" />' for d, a in cash_rows)
        statements.append(
            f'<FlexStatement accountId="{acct}" fromDate="{start:%Y%m%d}" toDate="{end:%Y%m%d}" '
            f'whenGenerated="{end + timedelta(days=1):%Y%m%d};021500">'
            f'<EquitySummaryInBase>{"".join(nav_rows)}</EquitySummaryInBase>'
            f'<CashTransactions>{cash}</CashTransactions></FlexStatement>')
    return (f'<FlexQueryResponse queryName="OFS Sharpe (demo)" type="AF">'
            f'<FlexStatements count="{len(statements)}">{"".join(statements)}</FlexStatements></FlexQueryResponse>')
