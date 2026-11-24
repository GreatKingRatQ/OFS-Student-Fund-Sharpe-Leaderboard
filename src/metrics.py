"""Return and risk metrics from daily NAV. Standard library only."""
from __future__ import annotations

import math
from datetime import date
from statistics import mean, stdev

from flex import flows_between

TRADING_DAYS = 252


def daily_returns(nav: dict[date, float], flows: dict[date, float] | None = None):
    """Flow-adjusted daily returns: [(date, return, starting_capital), ...].

    Deposits/withdrawals are treated as arriving at the start of the day, so
    r = (NAV_today - (NAV_yesterday + flow)) / (NAV_yesterday + flow).
    Days before the account is funded (NAV_yesterday <= 0) are skipped, which also
    drops the funding day itself.
    """
    flows = flows or {}
    days = sorted(nav)
    out = []
    for prev, cur in zip(days, days[1:]):
        p = nav[prev]
        base = p + flows_between(flows, prev, cur)
        if p <= 0 or base <= 0:
            continue
        out.append((cur, nav[cur] / base - 1.0, base))
    return out


def sharpe(returns: list[float], rf_annual: float) -> float | None:
    """Annualised Sharpe: mean daily excess return / stdev of daily excess return * sqrt(252)."""
    if len(returns) < 2:
        return None
    rf_daily = (1 + rf_annual) ** (1 / TRADING_DAYS) - 1
    excess = [r - rf_daily for r in returns]
    sd = stdev(excess)
    if sd == 0:
        return None
    return mean(excess) / sd * math.sqrt(TRADING_DAYS)


def growth_index(returns: list[float]) -> list[float]:
    idx = [1.0]
    for r in returns:
        idx.append(idx[-1] * (1 + r))
    return idx


def max_drawdown(returns: list[float]) -> float:
    peak, worst = 1.0, 0.0
    for v in growth_index(returns):
        peak = max(peak, v)
        worst = min(worst, v / peak - 1)
    return worst


def summarise(returns: list[float], rf_annual: float) -> dict:
    n = len(returns)
    return {
        "days": n,
        "sharpe": sharpe(returns, rf_annual),
        "ret": (growth_index(returns)[-1] - 1) if n else None,
        "vol": stdev(returns) * math.sqrt(TRADING_DAYS) if n >= 2 else None,
        "mdd": max_drawdown(returns) if n else None,
    }


def combine(series: list[list[tuple]]) -> list[tuple]:
    """Whole-fund daily returns: capital-weighted average of each account's return that day."""
    by_day: dict[date, list[tuple[float, float]]] = {}
    for s in series:
        for d, r, base in s:
            by_day.setdefault(d, []).append((r, base))
    out = []
    for d in sorted(by_day):
        cap = sum(b for _, b in by_day[d])
        out.append((d, sum(r * b for r, b in by_day[d]) / cap, cap))
    return out
