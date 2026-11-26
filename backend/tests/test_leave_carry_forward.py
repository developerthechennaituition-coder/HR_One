"""Focused tests for the employee-joining-month ML cycle boundaries."""
from datetime import date
import unittest

from app.leave import _six_month_cycle


class MedicalLeaveCycleTests(unittest.TestCase):
    def test_cycle_starts_in_joining_month_and_runs_six_months(self):
        start, end = _six_month_cycle(date(2026, 3, 17), date(2026, 8, 25))
        self.assertEqual(start, date(2026, 3, 1))
        self.assertEqual(end, date(2026, 9, 1))

    def test_cycle_restarts_at_six_month_boundary(self):
        start, end = _six_month_cycle(date(2026, 3, 17), date(2026, 9, 1))
        self.assertEqual(start, date(2026, 9, 1))
        self.assertEqual(end, date(2027, 3, 1))

    def test_cycle_handles_joining_month_end_dates(self):
        start, end = _six_month_cycle(date(2026, 8, 31), date(2027, 2, 28))
        self.assertEqual(start, date(2027, 2, 1))
        self.assertEqual(end, date(2027, 8, 1))


if __name__ == "__main__":
    unittest.main()
