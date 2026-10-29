"""On-screen payslip endpoints: same data as the PDF, correct access scope.

Run in its own process with a throwaway database, for example:
    MONGODB_DATABASE=hrms_payslip_view_test python -m unittest tests.test_payslip_view
"""

import os
import unittest
from datetime import datetime, timezone

os.environ.setdefault("MONGODB_DATABASE", "hrms_payslip_view_test")

from bson import ObjectId  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from pymongo.errors import PyMongoError  # noqa: E402

from app import payroll  # noqa: E402
from app.db.mongodb import client, db  # noqa: E402
from app.session import active_sessions  # noqa: E402


class PayslipViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not db.name.endswith("_test"):
            raise unittest.SkipTest(f"Refusing to run against database {db.name!r}; use a *_test database.")
        try:
            client.admin.command("ping")
        except PyMongoError as exc:
            raise unittest.SkipTest(f"MongoDB is not reachable: {exc}")
        client.drop_database(db.name)

    @classmethod
    def tearDownClass(cls):
        client.drop_database(db.name)

    def setUp(self):
        for name in ["users", "monthly_payroll_records", "companies", "employee_salary_profiles"]:
            db[name].delete_many({})
        self.hr = self.make_user("HR-1", "HR Admin")
        self.employee = self.make_user("E-1", "Employee")
        self.other = self.make_user("E-2", "Employee")
        db["companies"].insert_one({"company": "Acme", "legal_entity": "Acme Pvt Ltd", "branch": "HQ", "location": "Chennai", "is_active": True})
        self.record_id = str(db["monthly_payroll_records"].insert_one({
            "employee_id": "E-1", "employee_name": "Asha Kumar with a very long employee name", "designation": "Engineer",
            "department": "IT", "period": "2026-09", "month": 9, "year": 2026, "currency": "INR", "status": "Paid",
            "paid_days": 29, "lop_days": 1, "present_days": 21, "weekly_off_days": 4, "holiday_days": 1,
            "earnings": [{"name": "Basic", "amount": 30000}, {"name": "HRA", "amount": 12000}],
            "deductions": [{"name": "PF", "amount": 1800}],
            "gross_earnings": 42000, "total_deductions": 1800, "net_salary": 40200,
            "bank_name": "State Bank", "bank_account_number": "1234567890", "pan_card_number": "ABCDE1234F",
            "updated_at": datetime(2026, 10, 2, tzinfo=timezone.utc),
        }).inserted_id)
        db["monthly_payroll_records"].insert_one({"employee_id": "E-1", "period": "2026-10", "status": "Calculated", "net_salary": 1})

    def make_user(self, employee_id, role):
        user = {"_id": ObjectId(), "name": employee_id, "employee_id": employee_id, "role": role, "is_active": True}
        db["users"].insert_one(user)
        token = f"token-{user['_id']}"
        active_sessions[token] = str(user["_id"])
        self.addCleanup(active_sessions.pop, token, None)
        user["auth"] = f"Bearer {token}"
        return user

    def assert_http(self, status_code, func, *args):
        with self.assertRaises(HTTPException) as error:
            func(*args)
        self.assertEqual(error.exception.status_code, status_code, error.exception.detail)

    def test_hr_view_matches_the_finalized_record(self):
        view = payroll.get_payslip(self.record_id, self.hr["auth"], None)
        self.assertEqual((view["employee_id"], view["period"], view["net_salary"]), ("E-1", "2026-09", 40200.0))
        self.assertEqual([item["name"] for item in view["earnings"]], ["Basic", "HRA"])
        self.assertEqual((view["gross_earnings"], view["total_deductions"]), (42000, 1800))
        self.assertEqual(view["attendance"]["paid_days"], 29)
        self.assertEqual((view["company"]["name"], view["bank_name"], view["pay_date"]), ("Acme Pvt Ltd", "State Bank", "2026-10-02"))
        self.assertIn("Forty Thousand", view["net_salary_in_words"])

    def test_access_is_scoped(self):
        self.assert_http(403, payroll.get_payslip, self.record_id, self.employee["auth"], None)  # HR endpoint
        own = payroll.get_my_payslip(self.record_id, self.employee["auth"], None)
        self.assertEqual((own["employee_id"], own["net_salary"]), ("E-1", 40200.0))
        self.assert_http(404, payroll.get_my_payslip, self.record_id, self.other["auth"], None)  # someone else's
        draft = str(db["monthly_payroll_records"].find_one({"period": "2026-10"})["_id"])
        self.assert_http(404, payroll.get_payslip, draft, self.hr["auth"], None)  # not finalized
        self.assert_http(404, payroll.get_payslip, "bad-id", self.hr["auth"], None)


if __name__ == "__main__":
    unittest.main()
