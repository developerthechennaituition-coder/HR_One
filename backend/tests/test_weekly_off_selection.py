"""Integration tests for employee Weekly Off selection."""

import os
import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

os.environ.setdefault("MONGODB_DATABASE", "hrms_weekly_off_selection_test")

from bson import ObjectId  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app import attendance_requests, weekly_off_selection  # noqa: E402
from app.db.mongodb import client, db  # noqa: E402


class WeeklyOffSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not db.name.endswith("_test"):
            raise unittest.SkipTest(f"Refusing to run against database {db.name!r}; use a *_test database.")
        try:
            client.admin.command("ping")
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"MongoDB is not reachable: {exc}")
        client.drop_database(db.name)
        db["attendance"].create_index([("user_id", 1), ("attendance_date", 1)], unique=True)
        attendance_requests.ensure_attendance_request_indexes()

    @classmethod
    def tearDownClass(cls):
        if db.name.endswith("_test"):
            client.drop_database(db.name)

    def setUp(self):
        for collection in (
            "users", "attendance", "attendance_requests", "attendance_request_date_claims",
            "monthly_payroll_records", "leave_policies", "leave_requests", "leave_types",
            "employee_details", "employees", "essl_raw_punches", "holidays",
            "employee_holiday_calendars", "audit_logs", "attendance_modification_history",
            "leave_notifications", "notification_sequences", "leave_cl_monthly_claims",
        ):
            db[collection].delete_many({})
        self.today = date(2026, 10, 25)
        self.employee = {
            "_id": ObjectId(), "name": "Asha", "employee_id": "E100",
            "role": "Employee", "is_active": True, "date_of_joining": "2026-10-18",
        }
        self.manager = {
            "_id": ObjectId(), "name": "Reporting Manager", "employee_id": "MGR1",
            "role": "Employee", "is_active": True,
        }
        self.employee["reporting_manager_id"] = str(self.manager["_id"])
        self.admin = {
            "_id": ObjectId(), "name": "HR Admin", "employee_id": "HR1",
            "role": "HR Admin", "is_active": True,
        }
        db["users"].insert_many([self.employee.copy(), self.manager.copy(), self.admin.copy()])
        patchers = (
            patch.object(weekly_off_selection, "company_today", return_value=self.today),
            patch.object(attendance_requests, "company_today", return_value=self.today),
            patch.object(attendance_requests, "_current_user", side_effect=self.current_user),
            patch.object(attendance_requests, "_require_hr_admin", side_effect=self.require_admin),
        )
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def current_user(self, authorization):
        if authorization == "Bearer admin":
            return self.admin
        if authorization == "Bearer manager":
            return self.manager
        if authorization == "Bearer employee":
            return self.employee
        raise HTTPException(401, "Authentication required")

    def require_admin(self, authorization, message):
        user = self.current_user(authorization)
        if user["role"] != "HR Admin":
            raise HTTPException(403, message)
        return user

    def select(self, dates):
        return weekly_off_selection.create_weekly_off_selections(
            weekly_off_selection.WeeklyOffSelectionPayload(attendance_dates=dates),
            "Bearer employee",
        )


    def test_joining_month_entitlement_and_reporting_manager_approval(self):
        data = weekly_off_selection.my_weekly_off_selections("Bearer employee")
        self.assertEqual(len(data["eligible_dates"]), 15)
        summary = {item["period"]: item for item in data["monthly_entitlements"]}
        self.assertEqual(summary["2026-10"]["limit"], 2)
        self.assertEqual(summary["2026-11"]["limit"], 4)
        attendance_request_calendar = attendance_requests.my_attendance_calendar(
            2026, 10, "Bearer employee",
        )
        self.assertEqual(
            attendance_request_calendar["limits"]["weekly_off_limit"],
            summary["2026-10"]["limit"],
        )

        result = self.select([self.today])
        request = result["requests"][0]
        self.assertEqual((request["status"], request["approval_stage"]), ("pending", "reporting_manager"))
        self.assertIsNotNone(db["leave_notifications"].find_one({
            "weekly_off_request_id": request["id"], "recipient_user_id": str(self.manager["_id"]),
        }))
        self.assertIsNone(db["attendance"].find_one({
            "user_id": str(self.employee["_id"]), "attendance_date": self.today.isoformat(),
        }))
        with self.assertRaises(HTTPException) as wrong_approver:
            weekly_off_selection.decide_weekly_off_selection(
                request["id"], weekly_off_selection.WeeklyOffDecisionPayload(action="approve"), "Bearer admin",
            )
        self.assertEqual(wrong_approver.exception.status_code, 403)
        weekly_off_selection.decide_weekly_off_selection(
            request["id"], weekly_off_selection.WeeklyOffDecisionPayload(action="approve"), "Bearer manager",
        )
        attendance = db["attendance"].find_one({
            "user_id": str(self.employee["_id"]), "attendance_date": self.today.isoformat(),
        })
        self.assertEqual(attendance["status"], "Weekly Off")
        self.assertEqual(attendance["attendance_source"], "APPROVED_WEEKLY_OFF_REQUEST")

        with self.assertRaises(HTTPException) as duplicate:
            self.select([self.today])
        self.assertEqual(duplicate.exception.status_code, 409)

        second = self.select([date(2026, 10, 26)])["requests"][0]
        self.assertEqual((second["status"], second["approval_stage"]), ("pending", "hr_admin"))
        with self.assertRaises(HTTPException) as limit:
            self.select([date(2026, 10, 27)])
        self.assertEqual(limit.exception.status_code, 409)

    def test_admin_approval_rejection_and_approved_cancellation(self):
        iso = self.today.isoformat()
        db["attendance"].insert_one({
            "user_id": str(self.employee["_id"]), "attendance_date": iso,
            "status": "Absent", "updated_at": datetime(2026, 10, 24, tzinfo=timezone.utc),
        })
        db["monthly_payroll_records"].insert_one({
            "employee_id": "E100", "period": "2026-10", "status": "Calculated",
        })

        submitted = self.select([self.today])["requests"][0]
        self.assertEqual(submitted["status"], "pending")
        self.assertEqual(db["attendance"].find_one({"user_id": str(self.employee["_id"]), "attendance_date": iso})["status"], "Absent")
        weekly_off_selection.decide_weekly_off_selection(
            submitted["id"], weekly_off_selection.WeeklyOffDecisionPayload(action="approve"),
            "Bearer admin",
        )
        applied = db["attendance"].find_one({"user_id": str(self.employee["_id"]), "attendance_date": iso})
        self.assertEqual(applied["status"], "Weekly Off")
        self.assertIsNotNone(db["leave_notifications"].find_one({
            "weekly_off_request_id": submitted["id"], "recipient_user_id": str(self.employee["_id"]),
            "notification_type": "weekly_off_decision",
        }))
        self.assertTrue(db["monthly_payroll_records"].find_one({"period": "2026-10"})["attendance_outdated"])

        weekly_off_selection.cancel_my_weekly_off_selection(submitted["id"], "Bearer employee")
        weekly_off_selection.decide_weekly_off_cancellation(
            submitted["id"], weekly_off_selection.WeeklyOffDecisionPayload(action="approve"),
            "Bearer admin",
        )
        restored = db["attendance"].find_one({"user_id": str(self.employee["_id"]), "attendance_date": iso})
        self.assertEqual(restored["status"], "Absent")
        self.assertEqual(db["attendance_requests"].find_one({"_id": ObjectId(submitted["id"])})["status"], "cancelled")

        rejected_date = date(2026, 10, 26)
        db["attendance"].insert_one({
            "user_id": str(self.employee["_id"]), "attendance_date": rejected_date.isoformat(),
            "status": "Holiday", "updated_at": datetime(2026, 10, 24, tzinfo=timezone.utc),
        })
        rejected = self.select([rejected_date])["requests"][0]
        weekly_off_selection.decide_weekly_off_selection(
            rejected["id"], weekly_off_selection.WeeklyOffDecisionPayload(action="reject"),
            "Bearer admin",
        )
        self.assertEqual(
            db["attendance"].find_one({"user_id": str(self.employee["_id"]), "attendance_date": rejected_date.isoformat()})["status"],
            "Holiday",
        )

        cancellation_date = date(2026, 10, 27)
        db["attendance"].insert_one({
            "user_id": str(self.employee["_id"]), "attendance_date": cancellation_date.isoformat(),
            "status": "Absent",
        })
        cancellation = self.select([cancellation_date])["requests"][0]
        weekly_off_selection.decide_weekly_off_selection(
            cancellation["id"], weekly_off_selection.WeeklyOffDecisionPayload(action="approve"),
            "Bearer admin",
        )
        pending_cancel = weekly_off_selection.cancel_my_weekly_off_selection(
            cancellation["id"], "Bearer employee",
        )
        self.assertEqual(pending_cancel["cancellation"]["status"], "pending")
        self.assertTrue(pending_cancel["notification_sent"])
        self.assertIsNotNone(db["leave_notifications"].find_one({
            "weekly_off_request_id": cancellation["id"],
            "recipient_user_id": str(self.admin["_id"]),
        }))
        weekly_off_selection.decide_weekly_off_cancellation(
            cancellation["id"], weekly_off_selection.WeeklyOffDecisionPayload(action="reject"),
            "Bearer admin",
        )
        retained = db["attendance"].find_one({
            "user_id": str(self.employee["_id"]), "attendance_date": cancellation_date.isoformat(),
        })
        self.assertEqual(retained["status"], "Weekly Off")
        retained_request = db["attendance_requests"].find_one({"_id": ObjectId(cancellation["id"])})
        self.assertEqual((retained_request["status"], retained_request["cancellation"]["status"]), ("approved", "rejected"))


    def test_only_immediately_adjacent_holiday_dates_require_hr_and_notify(self):
        db["employee_holiday_calendars"].insert_one({
            "employee_id": "E100", "calendar_id": "CAL-HOL", "is_active": True,
        })
        db["holidays"].insert_one({
            "calendar_id": "CAL-HOL", "holiday_date": datetime(2026, 10, 26),
            "holiday_name": "Company Day", "is_active": True,
        })

        self.assertIsNone(weekly_off_selection._holiday_proximity_reason(self.employee, date(2026, 10, 24)))
        self.assertIsNone(weekly_off_selection._holiday_proximity_reason(self.employee, date(2026, 10, 28)))
        before, after = self.select([date(2026, 10, 25), date(2026, 10, 27)])["requests"]
        self.assertEqual((before["status"], after["status"]), ("pending", "pending"))
        self.assertEqual((before["approval_stage"], after["approval_stage"]), ("hr_admin", "hr_admin"))
        with self.assertRaises(HTTPException) as wrong_approver:
            weekly_off_selection.decide_weekly_off_selection(
                before["id"], weekly_off_selection.WeeklyOffDecisionPayload(action="approve"), "Bearer manager",
            )
        self.assertEqual(wrong_approver.exception.status_code, 403)
        self.assertIn("immediately before", before["approval_reason"])
        self.assertIn("immediately after", after["approval_reason"])
        self.assertEqual(db["attendance"].count_documents({"status": "Weekly Off"}), 0)
        self.assertEqual(
            db["leave_notifications"].count_documents({
                "weekly_off_request_id": {"$in": [before["id"], after["id"]]},
                "recipient_user_id": str(self.admin["_id"]),
            }),
            2,
        )

        far_date = date(2026, 11, 1)
        far_request = self.select([far_date])["requests"][0]
        self.assertEqual((far_request["status"], far_request["approval_stage"]), ("pending", "reporting_manager"))


    def test_weekly_off_within_three_days_after_previous_selection_requires_admin(self):
        first_date = self.today
        first = self.select([first_date])["requests"][0]
        self.assertEqual(first["status"], "pending")
        weekly_off_selection.decide_weekly_off_selection(
            first["id"], weekly_off_selection.WeeklyOffDecisionPayload(action="approve"), "Bearer manager",
        )
        second_date = first_date + timedelta(days=3)
        second = self.select([second_date])["requests"][0]
        self.assertEqual((second["status"], second["approval_stage"]), ("pending", "hr_admin"))
        self.assertEqual(second["previous_weekly_off_date"], first_date.isoformat())
        self.assertIn("3 calendar day(s)", second["approval_reason"])
        self.assertIsNone(db["attendance"].find_one({
            "user_id": str(self.employee["_id"]), "attendance_date": second_date.isoformat(),
        }))
        self.assertIsNotNone(db["leave_notifications"].find_one({
            "weekly_off_request_id": second["id"], "recipient_user_id": str(self.admin["_id"]),
        }))


    def test_four_day_gap_after_previous_weekly_off_still_requires_reporting_manager(self):
        first_date = self.today
        first = self.select([first_date])["requests"][0]
        weekly_off_selection.decide_weekly_off_selection(
            first["id"], weekly_off_selection.WeeklyOffDecisionPayload(action="approve"), "Bearer manager",
        )
        second_date = first_date + timedelta(days=4)
        second = self.select([second_date])["requests"][0]
        self.assertEqual((second["status"], second["approval_stage"]), ("pending", "reporting_manager"))
        self.assertIsNone(second["previous_weekly_off_date"])

    def test_weekly_off_dates_in_same_submission_apply_gap_rule_in_order(self):
        first_date = self.today
        second_date = first_date + timedelta(days=2)
        first, second = self.select([first_date, second_date])["requests"]
        self.assertEqual((first["status"], first["approval_stage"]), ("pending", "reporting_manager"))
        self.assertEqual((second["status"], second["approval_stage"]), ("pending", "hr_admin"))
        self.assertEqual(second["previous_weekly_off_date"], first_date.isoformat())


    def test_reporting_manager_approved_weekly_off_cancellation_requires_same_approver(self):
        selected = self.select([self.today])["requests"][0]
        self.assertEqual(selected["status"], "pending")
        weekly_off_selection.decide_weekly_off_selection(
            selected["id"], weekly_off_selection.WeeklyOffDecisionPayload(action="approve"), "Bearer manager",
        )
        result = weekly_off_selection.cancel_my_weekly_off_selection(selected["id"], "Bearer employee")
        self.assertEqual(result["cancellation"]["status"], "pending")
        self.assertEqual(result["cancellation"]["approver_id"], str(self.manager["_id"]))
        self.assertTrue(result["notification_sent"])
        self.assertIsNotNone(db["leave_notifications"].find_one({
            "weekly_off_request_id": selected["id"],
            "recipient_user_id": str(self.manager["_id"]),
            "notification_type": "weekly_off_cancellation",
        }))
        weekly_off_selection.decide_weekly_off_cancellation(
            selected["id"], weekly_off_selection.WeeklyOffDecisionPayload(action="approve"), "Bearer manager",
        )
        self.assertEqual(db["attendance_requests"].find_one({"_id": ObjectId(selected["id"])})["status"], "cancelled")
        self.assertIsNone(db["attendance"].find_one({
            "user_id": str(self.employee["_id"]), "attendance_date": self.today.isoformat(),
        }))

    def test_date_window_and_finalized_payroll_are_enforced(self):
        with self.assertRaises(HTTPException) as out_of_range:
            self.select([date(2026, 11, 9)])
        self.assertEqual(out_of_range.exception.status_code, 422)

        db["monthly_payroll_records"].insert_one({
            "employee_id": "E100", "period": "2026-10", "status": "Approved",
        })
        with self.assertRaises(HTTPException) as locked:
            self.select([self.today])
        self.assertEqual(locked.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
