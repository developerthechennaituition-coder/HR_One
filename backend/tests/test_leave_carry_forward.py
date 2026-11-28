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



class CasualLeaveMonthlyClaimTests(unittest.TestCase):
    class FakeClaims:
        def __init__(self):
            self.items = []

        def insert_one(self, document):
            key = (document["user_id"], document["leave_type_id"], document["month"])
            if any((item["user_id"], item["leave_type_id"], item["month"]) == key for item in self.items):
                from pymongo.errors import DuplicateKeyError
                raise DuplicateKeyError("duplicate monthly CL claim")
            self.items.append(dict(document))

        def delete_many(self, query):
            request_id = query.get("request_id")
            self.items = [item for item in self.items if item.get("request_id") != request_id]

        def update_many(self, query, update):
            for item in self.items:
                if item.get("request_id") == query.get("request_id"):
                    item.update(update.get("$set", {}))

    def test_cl_claim_reserves_each_calendar_month_once(self):
        from unittest.mock import patch
        from fastapi import HTTPException
        from app import leave

        fake = self.FakeClaims()
        with patch.object(leave, "leave_cl_monthly_claims_collection", fake):
            leave._reserve_cl_monthly_claims(
                "employee-1", "cl-type", [date(2026, 11, 28), date(2026, 12, 2)], "request-1"
            )
            self.assertEqual({item["month"] for item in fake.items}, {"2026-11", "2026-12"})
            with self.assertRaises(HTTPException) as caught:
                leave._reserve_cl_monthly_claims("employee-1", "cl-type", [date(2026, 11, 30)], "request-2")
            self.assertEqual(caught.exception.status_code, 409)
            self.assertEqual({item["request_id"] for item in fake.items}, {"request-1"})
            leave._commit_cl_monthly_claims("request-1")
            self.assertTrue(all(item["status"] == "approved" for item in fake.items))
            leave._release_cl_monthly_claims("request-1")
            self.assertEqual(fake.items, [])


if __name__ == "__main__":
    unittest.main()
