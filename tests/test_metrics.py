"""Run with: python3 -m unittest discover tests"""
import math
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import flex  # noqa: E402
import metrics  # noqa: E402


class Returns(unittest.TestCase):
    def test_deposit_is_not_a_gain(self):
        nav = {date(2026, 11, 2): 1000.0, date(2026, 11, 3): 1520.0}
        flows = {date(2026, 11, 3): 500.0}
        (_, r, base), = metrics.daily_returns(nav, flows)
        self.assertAlmostEqual(base, 1500.0)
        self.assertAlmostEqual(r, 20 / 1500)

    def test_weekend_deposit_rolls_to_monday(self):
        nav = {date(2026, 11, 6): 1000.0, date(2026, 11, 9): 1500.0}
        flows = {date(2026, 11, 7): 500.0}  # Saturday
        (_, r, _), = metrics.daily_returns(nav, flows)
        self.assertAlmostEqual(r, 0.0)

    def test_funding_day_skipped(self):
        nav = {date(2026, 10, 9): 0.0, date(2026, 10, 12): 1000.0, date(2026, 10, 13): 1010.0}
        out = metrics.daily_returns(nav, {date(2026, 10, 12): 1000.0})
        self.assertEqual([d for d, _, _ in out], [date(2026, 10, 13)])
        self.assertAlmostEqual(out[0][1], 0.01)


class Sharpe(unittest.TestCase):
    def test_matches_hand_calculation(self):
        rets = [0.01, -0.005, 0.002, 0.007, -0.003]
        rf = 0.04
        rfd = (1 + rf) ** (1 / 252) - 1
        ex = [r - rfd for r in rets]
        m = sum(ex) / len(ex)
        sd = math.sqrt(sum((x - m) ** 2 for x in ex) / (len(ex) - 1))
        self.assertAlmostEqual(metrics.sharpe(rets, rf), m / sd * math.sqrt(252))

    def test_undefined_cases(self):
        self.assertIsNone(metrics.sharpe([0.01], 0.04))
        self.assertIsNone(metrics.sharpe([0.01, 0.01, 0.01], 0.04))

    def test_drawdown(self):
        self.assertAlmostEqual(metrics.max_drawdown([0.1, -0.5, 0.2]), -0.5)


class Parse(unittest.TestCase):
    XML = """<FlexQueryResponse><FlexStatements count="1"><FlexStatement accountId="U1" whenGenerated="20261121;021500">
      <EquitySummaryInBase>
        <EquitySummaryByReportDateInBase accountId="U1" reportDate="2026-11-19" total="1,000.00"/>
        <EquitySummaryByReportDateInBase accountId="U1" reportDate="2026-11-20" total="1105"/>
      </EquitySummaryInBase>
      <CashTransactions>
        <CashTransaction accountId="U1" type="Deposits/Withdrawals" reportDate="2026-11-20" amount="100" fxRateToBase="1"/>
        <CashTransaction accountId="U1" type="Deposits/Withdrawals" reportDate="2026-11-20" amount="100" levelOfDetail="SUMMARY"/>
        <CashTransaction accountId="U1" type="Dividends" reportDate="2026-11-20" amount="5"/>
      </CashTransactions></FlexStatement></FlexStatements></FlexQueryResponse>"""

    def test_parse(self):
        nav, flows, gen = flex.parse_statement(self.XML)
        self.assertEqual(nav["U1"][date(2026, 11, 20)], 1105.0)
        self.assertEqual(flows["U1"], {date(2026, 11, 20): 100.0})  # summary row and dividend ignored
        (_, r, _), = metrics.daily_returns(nav["U1"], flows["U1"])
        self.assertAlmostEqual(r, 5 / 1100)
        self.assertEqual(gen, "20261121;021500")


if __name__ == "__main__":
    unittest.main()
