"""Integration tests for the attendance request workflow against a real MongoDB.

Run in its own process with a throwaway database, for example:
    MONGODB_DATABASE=hrms_attendance_request_test python -m unittest tests.test_attendance_requests
The tests skip unless the database name ends with "_test", and drop it afterwards.
"""

import os
import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

os.environ.setdefault("MONGODB_DATABASE", "hrms_attendance_request_test")

from bson import ObjectId  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from pymongo.errors import PyMongoError  # noqa: E402

from app import attendance_requests as ar  # noqa: E402
from app import attendance as attendance_module, leave, payroll  # noqa: E402
from app.db.mongodb import client, db, ensure_audit_logs_collection  # noqa: E402
from app.session import active_sessions  # noqa: E402

TODAY = date(2026, 10, 15)


def payload(*dates, remarks=None):
    """Each date is (iso, status) or (iso, "Present", check_in, check_out)."""
    return ar.AttendanceRequestCreate(
        dates=[
            {"attendance_date": item[0], "requested_status": item[1],
             "requested_check_in_time": item[2] if len(item) > 2 else None,
             "requested_check_out_time": item[3] if len(item) > 3 else None}
            for item in dates
        ],
        remarks=remarks,
    )


def decisions(*items, comments=None):
    """Each item is (iso, final_status[, check_in, check_out]) or (iso, "reject")."""
    return ar.AttendanceRequestResponsePayload(
        decisions=[
            {"attendance_date": item[0], "action": "reject"} if item[1] == "reject" else
            {"attendance_date": item[0], "action": "approve", "final_status": item[1],
             "final_check_in_time": item[2] if len(item) > 2 else None,
             "final_check_out_time": item[3] if len(item) > 3 else None}
            for item in items
        ],
        review_comments=comments,
    )


class AttendanceRequestWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not db.name.endswith("_test"):
            raise unittest.SkipTest(f"Refusing to run against database {db.name!r}; use a *_test database.")
        try:
            client.admin.command("ping")
        except PyMongoError as exc:
            raise unittest.SkipTest(f"MongoDB is not reachable: {exc}")
        client.drop_database(db.name)
        db["attendance"].create_index([("user_id", 1), ("attendance_date", 1)], unique=True)
        ensure_audit_logs_collection()  # Same $jsonSchema validator as the live database.
        db["leave_notifications"].create_index("leave_notification_id", unique=True)
        ar.ensure_attendance_request_indexes()

    @classmethod
    def tearDownClass(cls):
        client.drop_database(db.name)

    def setUp(self):
        for name in ["users", "attendance", "attendance_requests", "attendance_request_date_claims",
                     "monthly_payroll_records", "leave_notifications", "employee_holiday_calendars", "holidays", "leave_policies",
                     "leave_types", "leave_balances", "leave_requests", "permission_requests", "essl_raw_punches", "audit_logs"]:
            db[name].delete_many({})
        self.today = patch.object(ar, "company_today", return_value=TODAY)
        self.today.start()
        self.addCleanup(self.today.stop)
        self.employee = self.make_user("Asha Kumar", "E-201", "Employee", date_of_joining=datetime(2026, 9, 2))
        self.other = self.make_user("Bala Raman", "E-202", "Employee", date_of_joining=datetime(2025, 1, 1))
        self.hr = self.make_user("Hema HR", "HR-1", "HR Admin")
        # Weekly off is whatever HR Admin configured; there is no built-in default.
        self.set_weekly_offs(["sunday"])
        db["employee_holiday_calendars"].insert_one({"employee_id": "E-201", "calendar_id": "CAL-1", "is_active": True})
        db["holidays"].insert_one({"calendar_id": "CAL-1", "holiday_date": datetime(2026, 9, 10), "is_active": True, "holiday_name": "Festival"})
        db["attendance"].insert_many([
            {"user_id": str(self.employee["_id"]), "employee_id": "E-201", "attendance_date": "2026-09-03", "status": "Present",
             "check_in_time": "09:05", "check_out_time": "18:10", "attendance_source": "ESSL", "attendance_type": "OFFICE"},
            {"user_id": str(self.employee["_id"]), "employee_id": "E-201", "attendance_date": "2026-09-04", "status": "Present",
             "check_in_time": "09:20", "check_out_time": None, "attendance_source": "WFH_FACE_GPS", "attendance_type": "WFH"},
        ])

    def set_weekly_offs(self, days):
        db["leave_policies"].update_one(
            {"leave_type_id": "__company_policy__"},
            {"$set": {"weekly_off_weekdays": days}},
            upsert=True,
        )

    def make_user(self, name, employee_id, role, **extra):
        user = {"_id": ObjectId(), "name": name, "employee_id": employee_id, "role": role, "is_active": True, "department": "Finance", **extra}
        db["users"].insert_one(user)
        token = f"token-{user['_id']}"
        active_sessions[token] = str(user["_id"])
        self.addCleanup(active_sessions.pop, token, None)
        user["auth"] = f"Bearer {token}"
        return user

    def calendar_day(self, user, iso):
        result = ar.my_attendance_calendar(int(iso[:4]), int(iso[5:7]), user["auth"])
        return next(day for day in result["days"] if day["attendance_date"] == iso)

    def assert_http(self, status_code, func, *args):
        with self.assertRaises(HTTPException) as error:
            func(*args)
        self.assertEqual(error.exception.status_code, status_code, error.exception.detail)
        return error.exception.detail

    # -- Calendar ----------------------------------------------------------

    def test_calendar_reports_authoritative_statuses_without_creating_records(self):
        before = db["attendance"].count_documents({})
        result = ar.my_attendance_calendar(2026, 9, self.employee["auth"])
        days = {day["attendance_date"]: day for day in result["days"]}

        self.assertEqual(len(days), 30)
        # Pre-joining dates are no longer shown as "Not Applicable" by default.
        self.assertEqual(days["2026-09-01"]["status"], "Before Joining")
        self.assertEqual(days["2026-09-01"]["ineligible_reason"], "This date is before your joining date.")
        self.assertEqual((days["2026-09-03"]["status"], days["2026-09-03"]["attendance_category"], days["2026-09-03"]["check_in_time"]), ("Present", "Office", "09:05"))
        self.assertEqual(days["2026-09-04"]["attendance_category"], "Work From Home")
        self.assertEqual(days["2026-09-06"]["status"], "Weekly Off")
        self.assertEqual(days["2026-09-10"]["status"], "Holiday")
        self.assertEqual(days["2026-09-07"]["status"], "Not Marked")
        self.assertTrue(days["2026-09-07"]["eligible_for_request"])
        self.assertFalse(days["2026-09-01"]["eligible_for_request"])
        for iso in ["2026-09-03", "2026-09-06", "2026-09-10"]:
            self.assertTrue(days[iso]["eligible_for_request"], iso)
        october = {d["attendance_date"]: d for d in ar.my_attendance_calendar(2026, 10, self.employee["auth"])["days"]}
        self.assertFalse(october["2026-10-15"]["eligible_for_request"])  # today
        self.assertEqual(october["2026-10-20"]["status"], "Upcoming")
        # The calendar spans the joining month through today + 89 days (2027-01-12).
        self.assertEqual(len(ar.my_attendance_calendar(2027, 1, self.employee["auth"])["days"]), 31)
        self.assert_http(400, ar.my_attendance_calendar, 2027, 2, self.employee["auth"])
        self.assert_http(400, ar.my_attendance_calendar, 2026, 8, self.employee["auth"])  # before the joining month
        self.assertEqual(db["attendance"].count_documents({}), before)

    def test_calendar_window_and_joining_bounds(self):
        result = ar.my_attendance_calendar(2026, 9, self.employee["auth"])
        self.assertEqual((result["joining_date"], result["today"], result["calendar_end"]), ("2026-09-02", "2026-10-15", "2027-01-12"))
        days = {day["attendance_date"]: day for day in result["days"]}
        self.assertTrue(days["2026-09-01"]["is_before_joining"])
        self.assertFalse(days["2026-09-02"]["is_before_joining"])
        # Year boundary and leap-year months inside a window are accepted.
        with patch.object(ar, "company_today", return_value=date(2027, 12, 15)):
            self.assertEqual(len(ar.my_attendance_calendar(2028, 2, self.employee["auth"])["days"]), 29)
            self.assertEqual(ar.my_attendance_calendar(2028, 3, self.employee["auth"])["calendar_end"], "2028-03-13")
            self.assert_http(400, ar.my_attendance_calendar, 2028, 4, self.employee["auth"])
        # Profile's monthly attendance endpoint applies the same bounds.
        with patch.object(attendance_module, "company_today", return_value=TODAY):
            self.assertEqual(len(attendance_module.get_my_attendance(2026, 9, self.employee["auth"])), 2)
            self.assertEqual(attendance_module.get_my_attendance(2027, 1, self.employee["auth"]), [])
            self.assert_http(400, attendance_module.get_my_attendance, 2026, 8, self.employee["auth"])
            self.assert_http(400, attendance_module.get_my_attendance, 2027, 2, self.employee["auth"])
        # A future-dated joiner can still open the current month (no 400);
        # every date in it is before joining and nothing can be requested.
        db["users"].update_one({"_id": self.employee["_id"]}, {"$set": {"date_of_joining": datetime(2026, 11, 3)}})
        october = ar.my_attendance_calendar(2026, 10, self.employee["auth"])
        self.assertEqual(october["joining_date"], "2026-11-03")
        self.assertTrue(all(day["is_before_joining"] and not day["eligible_for_request"] for day in october["days"]))
        self.assertEqual(len(ar.my_attendance_calendar(2026, 11, self.employee["auth"])["days"]), 30)
        self.assert_http(400, ar.my_attendance_calendar, 2026, 9, self.employee["auth"])
        with patch.object(attendance_module, "company_today", return_value=TODAY):
            self.assertEqual(attendance_module.get_my_attendance(2026, 10, self.employee["auth"]), [])
        # A missing joining date does not invent a lower bound.
        db["users"].update_one({"_id": self.employee["_id"]}, {"$unset": {"date_of_joining": ""}})
        self.assertIsNone(ar.my_attendance_calendar(2026, 8, self.employee["auth"])["joining_date"])

    def test_future_dates_show_holidays_and_approved_leave_without_records(self):
        before = db["attendance"].count_documents({})
        leave_type = db["leave_types"].insert_one({"name": "Casual Leave", "code": "CL"}).inserted_id
        db["holidays"].insert_one({"calendar_id": "CAL-1", "holiday_date": datetime(2026, 11, 5), "is_active": True, "holiday_name": "Deepavali"})
        db["leave_requests"].insert_one({
            "user_id": str(self.employee["_id"]), "status": "approved", "leave_type_id": str(leave_type),
            "from_date": "2026-11-10", "to_date": "2026-11-11", "leave_dates": ["2026-11-10", "2026-11-11"], "day_part": "full_day",
        })
        days = {d["attendance_date"]: d for d in ar.my_attendance_calendar(2026, 11, self.employee["auth"])["days"]}
        self.assertEqual((days["2026-11-05"]["status"], days["2026-11-05"]["is_holiday"]), ("Holiday", True))
        self.assertEqual((days["2026-11-10"]["status"], days["2026-11-10"]["leave_type_code"]), ("Leave", "CL"))
        self.assertEqual(days["2026-11-08"]["status"], "Weekly Off")
        self.assertEqual(days["2026-11-12"]["status"], "Upcoming")
        for iso in ["2026-11-05", "2026-11-10", "2026-11-12"]:
            self.assertTrue(days[iso]["is_future"], iso)
            self.assertFalse(days[iso]["eligible_for_request"], iso)
        self.assertEqual(db["attendance"].count_documents({}), before)  # Nothing is generated for future cells.

    def test_last_window_month_is_full_but_details_stop_at_the_90th_day(self):
        # TODAY = 2026-10-15, so the 90th day is 2027-01-12.
        db["holidays"].insert_many([
            {"calendar_id": "CAL-1", "holiday_date": datetime(2027, 1, 11), "is_active": True, "holiday_name": "Pongal"},
            {"calendar_id": "CAL-1", "holiday_date": datetime(2027, 1, 20), "is_active": True, "holiday_name": "Later"},
        ])
        db["permission_requests"].insert_many([
            {"user_id": str(self.employee["_id"]), "permission_date": "2026-10-20", "start_time": "10:00", "end_time": "11:00", "status": "approved", "reason": "Bank"},
            {"user_id": str(self.employee["_id"]), "permission_date": "2026-10-21", "start_time": "10:00", "end_time": "11:00", "status": "rejected", "reason": "No"},
            {"user_id": str(self.employee["_id"]), "permission_date": "2027-01-14", "start_time": "10:00", "end_time": "11:00", "status": "pending", "reason": "Late"},
        ])
        january = {d["attendance_date"]: d for d in ar.my_attendance_calendar(2027, 1, self.employee["auth"])["days"]}
        self.assertEqual(len(january), 31)  # The whole month of the 90th day is returned.
        self.assertEqual(january["2027-01-11"]["status"], "Holiday")
        self.assertTrue(january["2027-01-11"]["has_upcoming_details"])
        self.assertFalse(january["2027-01-12"]["is_after_window"])
        for iso in ["2027-01-13", "2027-01-14", "2027-01-20"]:
            self.assertTrue(january[iso]["is_after_window"], iso)
            self.assertEqual(january[iso]["status"], "Upcoming", iso)
            self.assertFalse(january[iso]["has_upcoming_details"], iso)
            self.assertEqual(january[iso]["permissions"], [], iso)
        october = {d["attendance_date"]: d for d in ar.my_attendance_calendar(2026, 10, self.employee["auth"])["days"]}
        self.assertEqual([(p["start_time"], p["status"]) for p in october["2026-10-20"]["permissions"]], [("10:00", "approved")])
        self.assertTrue(october["2026-10-20"]["has_upcoming_details"])
        self.assertEqual(october["2026-10-21"]["permissions"], [])  # Rejected permissions are not shown.
        self.assertFalse(october["2026-10-21"]["has_upcoming_details"])
        # HR Admin sees the same calendar for the employee; employees cannot use the HR view.
        hr_view = ar.employee_attendance_calendar(str(self.employee["_id"]), 2026, 10, self.hr["auth"])
        self.assertEqual(hr_view["days"], ar.my_attendance_calendar(2026, 10, self.employee["auth"])["days"])
        self.assert_http(403, ar.employee_attendance_calendar, str(self.other["_id"]), 2026, 10, self.employee["auth"])
        self.assert_http(404, ar.employee_attendance_calendar, str(ObjectId()), 2026, 10, self.hr["auth"])

    def test_approval_does_not_write_before_the_joining_date(self):
        created = ar.create_attendance_request(payload(("2026-09-07", "Present", "09:00", "18:00")), self.employee["auth"])
        # HR later corrects the joining date to after the requested date.
        db["users"].update_one({"_id": self.employee["_id"]}, {"$set": {"date_of_joining": datetime(2026, 9, 10)}})
        detail = self.assert_http(409, ar.respond_to_attendance_request, created["id"], decisions(("2026-09-07", "Present", "09:00", "18:00")), self.hr["auth"])
        self.assertIn("before the employee's joining date (2026-09-10)", detail["dates"][0]["message"])
        self.assertIsNone(db["attendance"].find_one({"user_id": str(self.employee["_id"]), "attendance_date": "2026-09-07"}))

    def test_manual_wfh_and_leave_sync_respect_the_joining_date(self):
        user_id = str(self.employee["_id"])
        with patch.object(attendance_module, "company_today", return_value=date(2026, 9, 2)):
            # Manual attendance: yesterday is before joining, today is allowed.
            detail = self.assert_http(400, attendance_module.save_attendance, attendance_module.AttendanceCreate(
                user_id=user_id, attendance_date="2026-09-01", status="Absent"), self.hr["auth"])
            self.assertIn("before the employee's joining date (2026-09-02)", detail)
            attendance_module.save_attendance(attendance_module.AttendanceCreate(
                user_id=user_id, attendance_date="2026-09-02", status="Absent"), self.hr["auth"])
            self.assertEqual(db["attendance"].find_one({"user_id": user_id, "attendance_date": "2026-09-02"})["status"], "Absent")
        # WFH check-in is refused before the joining date, ahead of biometric checks.
        with (
            patch.object(attendance_module, "company_today", return_value=date(2026, 9, 1)),
            patch.object(attendance_module, "_ensure_wfh_attendance_enabled"),
            patch.object(attendance_module, "_require_wfh_employee", return_value=self.employee),
            patch.object(attendance_module, "_assert_wfh_email_scope"),
            patch.object(attendance_module, "_validate_wfh_biometric_attendance") as biometric,
        ):
            self.assert_http(400, attendance_module.wfh_check_in, None, self.employee["auth"])
            self.assert_http(400, attendance_module.wfh_check_out, None, self.employee["auth"])
            biometric.assert_not_called()
        # Automated leave sync skips pre-joining dates and keeps later ones.
        leave_request = {"_id": ObjectId(), "user_id": user_id, "day_part": "full_day"}
        attendance_module._apply_leave_to_date(leave_request, "2026-09-01", override_absent=False)
        attendance_module._apply_leave_to_date(leave_request, "2026-09-05", override_absent=False)
        self.assertIsNone(db["attendance"].find_one({"user_id": user_id, "attendance_date": "2026-09-01"}))
        self.assertEqual(db["attendance"].find_one({"user_id": user_id, "attendance_date": "2026-09-05"})["status"], "Leave")

    # -- Employee requests --------------------------------------------------

    def test_ineligible_dates_and_invalid_transitions_are_rejected(self):
        for iso in ["2026-09-01", "2026-10-15", "2026-10-20"]:  # before joining, today, future
            self.assert_http(422, ar.create_attendance_request, payload((iso, "Present")), self.employee["auth"])
        self.assert_http(422, ar.create_attendance_request, payload(("2026-09-10", "Absent")), self.employee["auth"])  # Holiday -> Present only
        self.assert_http(422, ar.create_attendance_request, payload(("2026-09-06", "Weekly Off")), self.employee["auth"])  # already Weekly Off
        self.assert_http(422, ar.create_attendance_request, payload(("2026-09-03", "Present", "09:05", "18:10")), self.employee["auth"])  # times unchanged
        self.assert_http(422, ar.create_attendance_request, payload(("2026-09-03", "Present")), self.employee["auth"])  # cannot erase times
        db["attendance"].insert_one({"user_id": str(self.employee["_id"]), "employee_id": "E-201", "attendance_date": "2026-09-11", "status": "Leave", "leave_type_code": "CL"})
        detail = self.assert_http(422, ar.create_attendance_request, payload(("2026-09-11", "Present")), self.employee["auth"])
        self.assertIn("Leave module", detail)
        self.assertEqual(db["attendance_requests"].count_documents({}), 0)
        with self.assertRaises(Exception):
            payload(("2026-09-07", "Late"))  # Unknown statuses are refused by the schema.

    def test_multi_date_request_and_duplicate_prevention(self):
        created = ar.create_attendance_request(
            payload(("2026-09-08", "Present"), ("2026-09-07", "Present"), ("2026-09-09", "Holiday"), remarks="Biometric was down"),
            self.employee["auth"],
        )
        self.assertEqual(created["status"], "pending")
        self.assertEqual(created["user_id"], str(self.employee["_id"]))
        self.assertEqual([d["attendance_date"] for d in created["dates"]], ["2026-09-07", "2026-09-08", "2026-09-09"])
        self.assertTrue(created["request_number"].startswith("AR"))
        self.assertEqual(db["leave_notifications"].count_documents({"recipient_user_id": str(self.hr["_id"])}), 1)

        self.assertEqual(self.calendar_day(self.employee, "2026-09-08")["request"]["status"], "pending")
        self.assert_http(422, ar.create_attendance_request, payload(("2026-09-08", "Present")), self.employee["auth"])
        self.assert_http(422, ar.create_attendance_request, payload(("2026-09-11", "Present"), ("2026-09-11", "Holiday")), self.employee["auth"])
        # Simulated race: the claim is taken between eligibility check and insert.
        db["attendance_request_date_claims"].insert_one({"user_id": str(self.employee["_id"]), "attendance_date": "2026-09-11", "request_id": "concurrent"})
        self.assert_http(409, ar.create_attendance_request, payload(("2026-09-12", "Present"), ("2026-09-11", "Present")), self.employee["auth"])
        self.assertIsNone(db["attendance_request_date_claims"].find_one({"attendance_date": "2026-09-12"}))
        self.assertEqual(db["attendance_requests"].count_documents({}), 1)
        self.assertEqual(db["attendance"].count_documents({"attendance_source": "ATTENDANCE_REQUEST"}), 0)

    def test_ownership_and_role_restrictions(self):
        created = ar.create_attendance_request(payload(("2026-09-07", "Present")), self.employee["auth"])
        self.assertEqual(ar.my_attendance_requests(self.other["auth"]), [])
        self.assert_http(403, ar.get_attendance_request, created["id"], self.other["auth"])
        self.assert_http(403, ar.cancel_attendance_request, created["id"], self.other["auth"])
        self.assert_http(403, ar.list_attendance_requests, None, None, None, None, None, self.employee["auth"])
        self.assert_http(403, ar.respond_to_attendance_request, created["id"], decisions(("2026-09-07", "Present")), self.employee["auth"])
        self.assertEqual(db["attendance_requests"].find_one({"_id": ObjectId(created["id"])})["status"], "pending")

    def test_hr_admin_cannot_decide_own_request(self):
        db["users"].update_one({"_id": self.hr["_id"]}, {"$set": {"date_of_joining": datetime(2025, 1, 1)}})
        own = ar.create_attendance_request(payload(("2026-09-07", "Present")), self.hr["auth"])
        self.assert_http(403, ar.respond_to_attendance_request, own["id"], decisions(("2026-09-07", "Present")), self.hr["auth"])
        self.assertEqual(db["attendance"].count_documents({"user_id": str(self.hr["_id"])}), 0)

    def test_employee_can_cancel_only_pending_requests(self):
        created = ar.create_attendance_request(payload(("2026-09-07", "Present")), self.employee["auth"])
        self.assertEqual(ar.cancel_attendance_request(created["id"], self.employee["auth"])["status"], "cancelled")
        self.assert_http(409, ar.cancel_attendance_request, created["id"], self.employee["auth"])
        self.assert_http(409, ar.respond_to_attendance_request, created["id"], decisions(("2026-09-07", "Present")), self.hr["auth"])
        self.assertTrue(self.calendar_day(self.employee, "2026-09-07")["eligible_for_request"])

    # -- HR decisions -------------------------------------------------------

    def test_incomplete_or_invalid_decisions_change_nothing(self):
        created = ar.create_attendance_request(payload(("2026-09-07", "Present"), ("2026-09-08", "Present")), self.employee["auth"])
        self.assert_http(422, ar.respond_to_attendance_request, created["id"], decisions(("2026-09-07", "Present")), self.hr["auth"])
        self.assert_http(422, ar.respond_to_attendance_request, created["id"], decisions(("2026-09-07", "Present"), ("2026-09-08", "Present"), ("2026-09-09", "Present")), self.hr["auth"])
        detail = ar.get_attendance_request(created["id"], self.hr["auth"])
        self.assertEqual(detail["status"], "pending")
        self.assertTrue(detail["can_respond"])
        self.assertEqual([d["current_status"] for d in detail["dates"]], ["Not Marked", "Not Marked"])
        self.assertEqual(db["attendance"].count_documents({"attendance_source": "ATTENDANCE_REQUEST"}), 0)
        self.assertEqual(db["leave_notifications"].count_documents({"recipient_user_id": str(self.employee["_id"])}), 0)

    def test_per_date_decisions_update_attendance_payroll_and_notify_once(self):
        before = payroll._attendance_summary_for_employee("E-201", 9, 2026)
        created = ar.create_attendance_request(
            payload(("2026-09-07", "Present"), ("2026-09-08", "Present"), ("2026-09-09", "Holiday")), self.employee["auth"],
        )
        pending = payroll._attendance_summary_for_employee("E-201", 9, 2026)
        self.assertEqual(pending["present_days"], before["present_days"])  # Pending is not approved attendance.

        result = ar.respond_to_attendance_request(
            created["id"],
            decisions(("2026-09-07", "Present"), ("2026-09-08", "Absent"), ("2026-09-09", "Present"), comments="Verified with team lead"),
            self.hr["auth"],
        )

        self.assertEqual(result["status"], "approved")
        self.assertEqual(result["summary"], {"applied": 3, "rejected": 0, "not_applied": 0, "notification_sent": True, "outdated_payroll_periods": []})
        self.assertEqual([(d["final_status"], d["outcome"]) for d in result["dates"]], [("Present", "approved"), ("Absent", "changed"), ("Present", "changed")])
        self.assertEqual(result["reviewed_by"], str(self.hr["_id"]))
        actions = {item["action"] for item in db["audit_logs"].find({"$or": [{"entity_id": created["id"]}, {"new_value.attendance_request_id": created["id"]}]})}
        self.assertTrue({"ATTENDANCE_REQUEST_SUBMITTED", "ATTENDANCE_REQUEST_RESPONDED", "attendance.request_decision"} <= actions, actions)
        records = {r["attendance_date"]: r for r in db["attendance"].find({"attendance_request_id": created["id"]})}
        self.assertEqual({k: v["status"] for k, v in records.items()}, {"2026-09-07": "Present", "2026-09-08": "Absent", "2026-09-09": "Present"})
        for record in records.values():
            self.assertIsNone(record["check_in_time"])  # No fabricated punches.
            self.assertEqual(record["employee_id"], "E-201")
        self.assertEqual(self.calendar_day(self.employee, "2026-09-07")["status"], "Present")
        self.assertIsNone(self.calendar_day(self.employee, "2026-09-07")["request"])  # claim released
        self.assertEqual(db["attendance_request_date_claims"].count_documents({}), 0)

        after = payroll._attendance_summary_for_employee("E-201", 9, 2026)
        self.assertEqual(after["present_days"], before["present_days"] + 2)
        self.assertEqual(after["absent_days"], before["absent_days"] + 1)

        notes = list(db["leave_notifications"].find({"recipient_user_id": str(self.employee["_id"])}))
        self.assertEqual(len(notes), 1)
        for text in ["07 Sep 2026 — Requested Present — Approved as Present", "08 Sep 2026 — Requested Present — Approved with changes as Absent",
                     "09 Sep 2026 — Requested Holiday — Approved with changes as Present", "Verified with team lead", created["request_number"]]:
            self.assertIn(text, notes[0]["message"])

        # Retried submission: no reapplication, no duplicate records or notifications.
        self.assert_http(409, ar.respond_to_attendance_request, created["id"], decisions(("2026-09-07", "Absent"), ("2026-09-08", "Absent"), ("2026-09-09", "Absent")), self.hr["auth"])
        self.assertEqual(db["attendance"].count_documents({"attendance_request_id": created["id"]}), 3)
        self.assertEqual(db["leave_notifications"].count_documents({"recipient_user_id": str(self.employee["_id"])}), 1)
        history = ar.my_attendance_requests(self.employee["auth"])
        self.assertEqual(history[0]["status"], "approved")

    def test_existing_records_created_after_submission_are_preserved(self):
        created = ar.create_attendance_request(payload(("2026-09-07", "Present"), ("2026-09-08", "Present")), self.employee["auth"])
        db["attendance"].insert_one({"user_id": str(self.employee["_id"]), "employee_id": "E-201", "attendance_date": "2026-09-08",
                                     "status": "Present", "check_in_time": "09:00", "attendance_source": "MANUAL", "attendance_type": "OFFICE"})

        result = ar.respond_to_attendance_request(created["id"], decisions(("2026-09-07", "Holiday"), ("2026-09-08", "Absent")), self.hr["auth"])

        self.assertEqual([d["applied"] for d in result["dates"]], [True, False])
        self.assertIn("existing record was preserved", result["dates"][1]["message"])
        manual = db["attendance"].find_one({"user_id": str(self.employee["_id"]), "attendance_date": "2026-09-08"})
        self.assertEqual((manual["status"], manual["check_in_time"], manual["attendance_source"]), ("Present", "09:00", "MANUAL"))
        self.assertEqual(result["status"], "partially_approved")

    def test_calculated_draft_payroll_is_flagged_and_cannot_be_approved(self):
        created = ar.create_attendance_request(payload(("2026-09-07", "Present")), self.other["auth"])
        draft_id = db["monthly_payroll_records"].insert_one({"employee_id": "E-202", "period": "2026-09", "status": "Calculated"}).inserted_id

        detail = ar.get_attendance_request(created["id"], self.hr["auth"])
        self.assertFalse(detail["dates"][0]["payroll_locked"])
        self.assertIn("marks that payroll as outdated", detail["dates"][0]["payroll_note"])

        result = ar.respond_to_attendance_request(created["id"], decisions(("2026-09-07", "Present")), self.hr["auth"])
        self.assertEqual(result["status"], "approved")
        self.assertEqual(result["summary"]["outdated_payroll_periods"], ["2026-09"])
        self.assertEqual(db["attendance"].find_one({"user_id": str(self.other["_id"]), "attendance_date": "2026-09-07"})["status"], "Present")
        draft = db["monthly_payroll_records"].find_one({"_id": draft_id})
        self.assertTrue(draft["attendance_outdated"])
        self.assertEqual(draft["attendance_outdated_dates"], ["2026-09-07"])

        # Payroll approval of the outdated draft is refused until it is recalculated.
        with self.assertRaises(HTTPException) as error:
            payroll.payroll_action(str(draft_id), payroll.PayrollActionRequest(action="approve"), self.hr["auth"], None)
        self.assertEqual(error.exception.status_code, 409)
        self.assertIn("recalculate", error.exception.detail)
        self.assertEqual(db["monthly_payroll_records"].find_one({"_id": draft_id})["status"], "Calculated")

    def test_finalized_payroll_locks_requests_and_decisions(self):
        created = ar.create_attendance_request(payload(("2026-09-07", "Present")), self.other["auth"])
        db["monthly_payroll_records"].insert_one({"employee_id": "E-202", "period": "2026-09", "status": "Paid"})

        detail = self.assert_http(409, ar.respond_to_attendance_request, created["id"], decisions(("2026-09-07", "Present")), self.hr["auth"])
        self.assertIn("locked", detail["dates"][0]["message"])
        self.assertEqual(db["attendance_requests"].find_one({"_id": ObjectId(created["id"])})["status"], "pending")
        self.assertEqual(db["attendance"].count_documents({"user_id": str(self.other["_id"])}), 0)
        self.assertEqual(db["leave_notifications"].count_documents({"recipient_user_id": str(self.other["_id"])}), 0)
        self.assertTrue(ar.get_attendance_request(created["id"], self.hr["auth"])["dates"][0]["payroll_locked"])
        day = self.calendar_day(self.other, "2026-09-14")
        self.assertFalse(day["eligible_for_request"])
        self.assertIn("locked", day["ineligible_reason"])
        self.assert_http(422, ar.create_attendance_request, payload(("2026-09-14", "Present")), self.other["auth"])

    # -- Weekly off configured by HR Admin ---------------------------------

    def test_weekly_off_has_no_default_and_supports_multiple_days(self):
        self.set_weekly_offs([])
        result = ar.my_attendance_calendar(2026, 9, self.employee["auth"])
        self.assertFalse(result["weekly_off_configured"])
        sunday = next(day for day in result["days"] if day["attendance_date"] == "2026-09-06")
        self.assertEqual(sunday["status"], "Not Marked")
        self.assertTrue(sunday["eligible_for_request"])

        self.set_weekly_offs(["saturday", "sunday"])
        result = ar.my_attendance_calendar(2026, 9, self.employee["auth"])
        days = {day["attendance_date"]: day for day in result["days"]}
        self.assertEqual(result["weekly_off_weekdays"], ["saturday", "sunday"])
        self.assertEqual((days["2026-09-05"]["status"], days["2026-09-06"]["status"]), ("Weekly Off", "Weekly Off"))
        self.assertNotIn("Weekly Off", days["2026-09-05"]["request_options"])
        # Leave day counting uses the same HR configuration.
        working = leave._working_dates(self.other, date(2026, 9, 4), date(2026, 9, 8))
        self.assertEqual([d.isoformat() for d in working], ["2026-09-04", "2026-09-07", "2026-09-08"])

    def test_company_policy_validates_weekly_off_days(self):
        self.assertEqual(leave.LeaveCompanyPolicy().weekly_off_weekdays, [])
        self.assertEqual(leave.LeaveCompanyPolicy(weekly_off_weekdays=["Sunday", "saturday", "sunday"]).weekly_off_weekdays, ["saturday", "sunday"])
        with self.assertRaises(Exception):
            leave.LeaveCompanyPolicy(weekly_off_weekdays=["funday"])
        with self.assertRaises(Exception):
            leave.LeaveCompanyPolicy(weekly_off_weekdays=["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"])

    def test_legacy_single_weekly_off_is_migrated_without_sunday_default(self):
        for legacy, expected in [("sunday", []), ("friday", ["friday"])]:
            db["leave_policies"].replace_one({"leave_type_id": "__company_policy__"}, {"leave_type_id": "__company_policy__", "weekly_off_weekday": legacy}, upsert=True)
            leave.ensure_leave_indexes()
            policy = db["leave_policies"].find_one({"leave_type_id": "__company_policy__"})
            self.assertEqual(policy["weekly_off_weekdays"], expected)
            self.assertNotIn("weekly_off_weekday", policy)

    # -- Status options, time corrections and rejection --------------------

    def test_request_options_follow_the_current_status(self):
        self.set_weekly_offs([])
        days = {d["attendance_date"]: d for d in ar.my_attendance_calendar(2026, 9, self.other["auth"])["days"]}
        # Medical Leave stays locked until the month's Weekly Off allowance is used.
        self.assertEqual(days["2026-09-07"]["request_options"], ["Present", "Absent", "Holiday", "Weekly Off"])
        self.assertTrue(any("unlocks after all 4 Weekly Off days" in note for note in days["2026-09-07"]["option_notes"]))
        db["attendance"].insert_many([
            {"user_id": str(self.other["_id"]), "employee_id": "E-202", "attendance_date": "2026-09-08", "status": "Absent"},
            {"user_id": str(self.other["_id"]), "employee_id": "E-202", "attendance_date": "2026-09-09", "status": "Weekly Off"},
            {"user_id": str(self.other["_id"]), "employee_id": "E-202", "attendance_date": "2026-09-10", "status": "Present", "check_in_time": "09:00", "check_out_time": "18:00"},
        ])
        days = {d["attendance_date"]: d for d in ar.my_attendance_calendar(2026, 9, self.other["auth"])["days"]}
        self.assertEqual(days["2026-09-08"]["request_options"], ["Present", "Holiday", "Weekly Off"])
        self.assertEqual(days["2026-09-09"]["request_options"], ["Present", "Absent"])
        self.assertEqual(days["2026-09-10"]["request_options"], ["Present", "Absent", "Weekly Off"])
        # Four configured Sundays use the allowance: Medical Leave replaces Weekly Off.
        self.set_weekly_offs(["sunday"])
        days = {d["attendance_date"]: d for d in ar.my_attendance_calendar(2026, 9, self.other["auth"])["days"]}
        self.assertEqual(days["2026-09-08"]["request_options"], ["Present", "Holiday", "Medical Leave"])
        self.assertEqual(days["2026-09-10"]["request_options"], ["Present", "Absent", "Medical Leave"])

    def test_present_time_correction_is_applied_only_after_hr_approval(self):
        employee_id = str(self.employee["_id"])
        db["essl_raw_punches"].insert_many([
            {"user_id": employee_id, "punch_at": datetime(2026, 9, 3, 3, 35, tzinfo=timezone.utc)},   # 09:05 IST
            {"user_id": employee_id, "punch_at": datetime(2026, 9, 3, 12, 40, tzinfo=timezone.utc)},  # 18:10 IST
        ])
        self.assert_http(422, ar.create_attendance_request, payload(("2026-09-03", "Present", "18:00", "09:00")), self.employee["auth"])
        self.assert_http(422, ar.create_attendance_request, payload(("2026-09-03", "Absent", "09:00", None)), self.employee["auth"])
        created = ar.create_attendance_request(payload(("2026-09-03", "Present", "08:55", "18:10"), remarks="Forgot to punch in on time"), self.employee["auth"])
        item = created["dates"][0]
        self.assertEqual((item["original_status"], item["original_check_in_time"], item["original_check_out_time"]), ("Present", "09:05", "18:10"))
        self.assertEqual((item["requested_check_in_time"], item["requested_check_out_time"]), ("08:55", "18:10"))
        record = db["attendance"].find_one({"user_id": employee_id, "attendance_date": "2026-09-03"})
        self.assertEqual((record["check_in_time"], record["attendance_source"]), ("09:05", "ESSL"))  # unchanged before approval

        detail = ar.get_attendance_request(created["id"], self.hr["auth"])
        self.assertEqual(detail["dates"][0]["allowed_final_statuses"][0], "Present")
        self.assert_http(422, ar.respond_to_attendance_request, created["id"], decisions(("2026-09-03", "Present", "19:00", "18:10")), self.hr["auth"])
        result = ar.respond_to_attendance_request(created["id"], decisions(("2026-09-03", "Present", "09:00", "18:10")), self.hr["auth"])
        self.assertEqual((result["status"], result["dates"][0]["outcome"]), ("approved", "changed"))
        record = db["attendance"].find_one({"user_id": employee_id, "attendance_date": "2026-09-03"})
        self.assertEqual((record["status"], record["check_in_time"], record["check_out_time"]), ("Present", "09:00", "18:10"))
        self.assertEqual((record["attendance_source"], record["attendance_type"]), ("ATTENDANCE_REQUEST", "OFFICE"))
        self.assertEqual(record["correction_previous"]["check_in_time"], "09:05")
        self.assertNotIn("last_punch", record)
        daily = attendance_module._calculate_daily_attendance(employee_id, date(2026, 9, 3), self.employee)
        self.assertTrue(daily["first_check_in"].startswith("2026-09-03T09:00"))  # approved time replaces raw punches
        note = db["leave_notifications"].find_one({"recipient_user_id": employee_id})
        self.assertIn("Requested Present (08:55 – 18:10) — Approved with changes as Present (09:00 – 18:10)", note["message"])

    def test_rejection_requires_remarks_and_leaves_attendance_unchanged(self):
        created = ar.create_attendance_request(payload(("2026-09-07", "Present"), ("2026-09-08", "Holiday")), self.employee["auth"])
        self.assert_http(422, ar.respond_to_attendance_request, created["id"], decisions(("2026-09-07", "reject"), ("2026-09-08", "reject")), self.hr["auth"])
        result = ar.respond_to_attendance_request(created["id"], decisions(("2026-09-07", "reject"), ("2026-09-08", "Holiday"), comments="No proof for 7 Sep"), self.hr["auth"])
        self.assertEqual(result["status"], "partially_approved")
        self.assertEqual([d["outcome"] for d in result["dates"]], ["rejected", "approved"])
        self.assertIsNone(db["attendance"].find_one({"user_id": str(self.employee["_id"]), "attendance_date": "2026-09-07"}))
        note = db["leave_notifications"].find_one({"recipient_user_id": str(self.employee["_id"])})
        self.assertIn("07 Sep 2026 — Requested Present — Rejected", note["message"])
        self.assertIn("Remarks: No proof for 7 Sep", note["message"])

        only_reject = ar.create_attendance_request(payload(("2026-09-09", "Present")), self.employee["auth"])
        result = ar.respond_to_attendance_request(only_reject["id"], decisions(("2026-09-09", "reject"), comments="Not verified"), self.hr["auth"])
        self.assertEqual((result["status"], result["summary"]["rejected"]), ("rejected", 1))

    # -- Monthly limits -----------------------------------------------------

    def test_weekly_off_limit_of_four_per_month(self):
        self.set_weekly_offs([])
        auth = self.other["auth"]
        self.assertEqual(ar.my_attendance_calendar(2026, 9, auth)["limits"]["weekly_off_limit"], 4)
        five = [(f"2026-09-0{day}", "Weekly Off") for day in range(1, 6)]
        self.assertIn("monthly limit of 4", self.assert_http(422, ar.create_attendance_request, payload(*five), auth))
        first = ar.create_attendance_request(payload(*five[:3]), auth)
        # Pending requests reserve the allowance, so a concurrent fifth cannot slip through.
        second = ar.create_attendance_request(payload(("2026-09-07", "Weekly Off")), auth)
        day = self.calendar_day(self.other, "2026-09-08")
        self.assertNotIn("Weekly Off", day["request_options"])
        self.assertIn("Medical Leave", day["request_options"])  # offered instead
        self.assertTrue(any("Weekly Off is unavailable" in note for note in day["option_notes"]))
        self.assert_http(422, ar.create_attendance_request, payload(("2026-09-08", "Weekly Off")), auth)

        ar.respond_to_attendance_request(first["id"], decisions(*[(iso, "Weekly Off") for iso, _ in five[:3]]), self.hr["auth"])
        ar.respond_to_attendance_request(second["id"], decisions(("2026-09-07", "Weekly Off")), self.hr["auth"])
        self.assertEqual(db["attendance"].count_documents({"user_id": str(self.other["_id"]), "status": "Weekly Off"}), 4)
        # HR cannot approve a fifth Weekly Off either, even when changing another request's status.
        third = ar.create_attendance_request(payload(("2026-09-08", "Present")), auth)
        detail = ar.get_attendance_request(third["id"], self.hr["auth"])
        self.assertNotIn("Weekly Off", detail["dates"][0]["allowed_final_statuses"])
        self.assert_http(422, ar.respond_to_attendance_request, third["id"], decisions(("2026-09-08", "Weekly Off")), self.hr["auth"])
        # The next calendar month has its own allowance.
        self.assertIn("Weekly Off", self.calendar_day(self.other, "2026-10-01")["request_options"])

    def test_rejected_weekly_off_requests_do_not_use_the_allowance(self):
        self.set_weekly_offs([])
        auth = self.other["auth"]
        created = ar.create_attendance_request(payload(*[(f"2026-09-0{day}", "Weekly Off") for day in range(1, 5)]), auth)
        self.assertNotIn("Weekly Off", self.calendar_day(self.other, "2026-09-08")["request_options"])
        ar.respond_to_attendance_request(created["id"], decisions(*[(f"2026-09-0{day}", "reject") for day in range(1, 5)], comments="Not eligible"), self.hr["auth"])
        self.assertIn("Weekly Off", self.calendar_day(self.other, "2026-09-08")["request_options"])

    def test_medical_leave_limit_of_one_per_month(self):
        # Four configured Sundays use the September Weekly Off allowance.
        auth = self.other["auth"]
        self.assert_http(422, ar.create_attendance_request, payload(("2026-09-07", "Medical Leave"), ("2026-09-08", "Medical Leave")), auth)
        created = ar.create_attendance_request(payload(("2026-09-07", "Medical Leave")), auth)
        self.assertNotIn("Medical Leave", self.calendar_day(self.other, "2026-09-08")["request_options"])  # pending reserves it
        result = ar.respond_to_attendance_request(created["id"], decisions(("2026-09-07", "Medical Leave")), self.hr["auth"])
        self.assertEqual(result["status"], "approved")
        record = db["attendance"].find_one({"user_id": str(self.other["_id"]), "attendance_date": "2026-09-07"})
        self.assertEqual((record["status"], record["leave_type_code"], record["medical_leave"]), ("Leave", "ML", True))
        self.assertEqual(db["leave_balances"].count_documents({}), 0)  # monthly entitlement, no leave balance used
        self.assertEqual(self.calendar_day(self.other, "2026-09-07")["status_label"], "Medical Leave")
        summary = payroll._attendance_summary_for_employee("E-202", 9, 2026)
        self.assertEqual((summary["medical_leave_days"], summary["paid_leave_days"], summary["weekly_off_days"]), (1, 0, 4))
        self.assertEqual(summary["paid_days"], 5)

        day = self.calendar_day(self.other, "2026-09-08")
        self.assertNotIn("Medical Leave", day["request_options"])
        self.assertTrue(any("monthly limit of 1 medical leave" in note for note in day["option_notes"]))
        self.assert_http(422, ar.create_attendance_request, payload(("2026-09-08", "Medical Leave")), auth)
        other = ar.create_attendance_request(payload(("2026-09-08", "Present")), auth)
        self.assert_http(422, ar.respond_to_attendance_request, other["id"], decisions(("2026-09-08", "Medical Leave")), self.hr["auth"])
        # A new calendar month has its own allowance.
        self.assertIn("Medical Leave", self.calendar_day(self.other, "2026-10-01")["request_options"])

    def test_medical_leave_unlocks_after_four_weekly_offs(self):
        self.set_weekly_offs([])
        auth = self.other["auth"]
        self.assertIn("unlocks after", self.assert_http(422, ar.create_attendance_request, payload(("2026-09-08", "Medical Leave")), auth))
        weekly = ar.create_attendance_request(payload(*[(f"2026-09-0{day}", "Weekly Off") for day in range(1, 5)]), auth)
        self.assertIn("Medical Leave", self.calendar_day(self.other, "2026-09-08")["request_options"])
        medical = ar.create_attendance_request(payload(("2026-09-08", "Medical Leave")), auth)
        # If the Weekly Off days are rejected, Medical Leave cannot be approved.
        ar.respond_to_attendance_request(weekly["id"], decisions(*[(f"2026-09-0{day}", "reject") for day in range(1, 5)], comments="Worked"), self.hr["auth"])
        self.assertIn("unlocks after", str(self.assert_http(422, ar.respond_to_attendance_request, medical["id"], decisions(("2026-09-08", "Medical Leave")), self.hr["auth"])))

    def test_payroll_counts_weekly_offs_and_medical_leave_as_payable(self):
        self.set_weekly_offs([])
        db["attendance"].insert_many([
            {"user_id": str(self.other["_id"]), "employee_id": "E-202", "attendance_date": f"2026-09-0{day}", "status": "Weekly Off"}
            for day in range(1, 5)
        ] + [
            {"user_id": str(self.other["_id"]), "employee_id": "E-202", "attendance_date": "2026-09-05", "status": "Leave", "leave_type_code": "ML", "medical_leave": True},
            {"user_id": str(self.other["_id"]), "employee_id": "E-202", "attendance_date": "2026-09-07", "status": "Present"},
            {"user_id": str(self.other["_id"]), "employee_id": "E-202", "attendance_date": "2026-09-08", "status": "Absent"},
        ])
        summary = payroll._attendance_summary_for_employee("E-202", 9, 2026)
        self.assertEqual((summary["weekly_off_days"], summary["medical_leave_days"], summary["present_days"]), (4, 1, 1))
        self.assertEqual((summary["paid_days"], summary["lop_days"]), (6, 1))

    def test_hr_can_finalize_a_date_as_not_applicable_counted_as_lop(self):
        auth = self.other["auth"]
        created = ar.create_attendance_request(payload(("2026-09-07", "Present", "09:00", "18:00"), ("2026-09-08", "Holiday")), auth)
        # Not Applicable is an HR-only final status; employees cannot request it.
        self.assertEqual(ar.get_attendance_request(created["id"], self.hr["auth"])["dates"][0]["allowed_final_statuses"][-1], "Not Applicable")
        with self.assertRaises(Exception):
            payload(("2026-09-09", "Not Applicable"))
        result = ar.respond_to_attendance_request(created["id"], decisions(("2026-09-07", "Not Applicable"), ("2026-09-08", "Not Applicable")), self.hr["auth"])
        self.assertEqual([item["outcome"] for item in result["dates"]], ["changed", "changed"])
        record = db["attendance"].find_one({"user_id": str(self.other["_id"]), "attendance_date": "2026-09-07"})
        self.assertEqual((record["status"], record["check_in_time"]), ("Not Applicable", None))
        day = self.calendar_day(self.other, "2026-09-07")
        self.assertEqual(day["status"], "Not Applicable")
        self.assertIn("Present", day["request_options"])  # employee may ask HR to correct it
        summary = payroll._attendance_summary_for_employee("E-202", 9, 2026)
        self.assertEqual((summary["not_applicable_days"], summary["lop_days"]), (2, 2))
        self.assertEqual(summary["paid_days"], 4)  # only the four configured Sundays
        note = db["leave_notifications"].find_one({"recipient_user_id": str(self.other["_id"])})
        self.assertIn("Approved with changes as Not Applicable", note["message"])

    def test_leave_module_medical_leave_follows_the_monthly_rule(self):
        medical_type = db["leave_types"].insert_one({"code": "ML", "name": "Medical Leave", "is_active": True, "is_paid": True}).inserted_id
        self.assertTrue(leave._is_medical_leave_type({"code": "ML", "name": "Medical Leave"}))
        self.assertFalse(leave._is_medical_leave_type({"code": "SL", "name": "Sick Leave"}))
        # September has four configured Sundays, so Medical Leave is unlocked.
        self.assertIsNone(ar.leave_module_medical_problem(self.other, [date(2026, 9, 8)], "full_day"))
        self.assertIn("1 day per month", ar.leave_module_medical_problem(self.other, [date(2026, 9, 8), date(2026, 9, 9)], "full_day"))
        # A pending Medical Leave application uses the month's allowance in both modules.
        db["leave_requests"].insert_one({"user_id": str(self.other["_id"]), "leave_type_id": str(medical_type), "status": "pending",
                                         "from_date": "2026-09-08", "to_date": "2026-09-08", "leave_dates": ["2026-09-08"], "day_part": "full_day"})
        self.assertIn("1 day per month", ar.leave_module_medical_problem(self.other, [date(2026, 9, 9)], "full_day"))
        self.assertNotIn("Medical Leave", self.calendar_day(self.other, "2026-09-09")["request_options"])
        # Without four Weekly Offs in the month it stays locked.
        self.set_weekly_offs([])
        self.assertIn("only after all 4 Weekly Off days", ar.leave_module_medical_problem(self.other, [date(2026, 10, 8)], "full_day"))
        # The yearly Medical Leave balance is one day for every month.
        db["leave_policies"].update_one({"leave_type_id": "__company_policy__"}, {"$set": {"probation_period_months": 3}})
        self.assertEqual(leave._medical_entitlement(self.other, 3.0), 12.0)

    def test_retired_sl_leave_type_is_merged_into_ml(self):
        user_id = str(self.other["_id"])
        ml_id = str(db["leave_types"].insert_one({"code": "ML", "name": "Medical Leave", "is_active": True}).inserted_id)
        sl_id = str(db["leave_types"].insert_one({"code": "SL", "name": "Medical Leave", "is_active": True}).inserted_id)
        db["leave_policies"].insert_one({"leave_type_id": sl_id, "annual_entitlement": 12, "is_active": True})
        db["leave_balances"].insert_many([
            {"user_id": user_id, "leave_type_id": sl_id, "year": 2026, "used": 1.0, "pending": 0.0},
            {"user_id": user_id, "leave_type_id": ml_id, "year": 2026, "used": 2.0, "pending": 0.0},
        ])
        db["leave_requests"].insert_one({"user_id": user_id, "leave_type_id": sl_id, "status": "approved"})
        db["attendance"].insert_one({"user_id": user_id, "employee_id": "E-202", "attendance_date": "2026-09-08", "status": "Leave", "leave_type_code": "SL"})
        leave._migrate_sl_to_medical_leave(datetime.now(timezone.utc))
        leave._migrate_sl_to_medical_leave(datetime.now(timezone.utc))  # idempotent
        self.assertEqual(db["leave_types"].count_documents({"code": "SL"}), 0)
        self.assertEqual(db["leave_policies"].count_documents({"leave_type_id": sl_id}), 0)
        self.assertEqual(db["leave_balances"].find_one({"user_id": user_id, "leave_type_id": ml_id, "year": 2026})["used"], 3.0)
        self.assertEqual(db["leave_balances"].count_documents({"leave_type_id": sl_id}), 0)
        self.assertEqual(db["leave_requests"].find_one({"user_id": user_id})["leave_type_id"], ml_id)
        self.assertEqual(db["attendance"].find_one({"attendance_date": "2026-09-08"})["leave_type_code"], "ML")
        # With no ML type yet, SL is simply re-coded in place.
        db["leave_types"].delete_many({})
        kept = db["leave_types"].insert_one({"code": "SL", "name": "Sick Leave", "is_active": True}).inserted_id
        leave._migrate_sl_to_medical_leave(datetime.now(timezone.utc))
        self.assertEqual(db["leave_types"].find_one({"_id": kept})["code"], "ML")

    def test_weekly_off_and_medical_limits_both_reached(self):
        # Four configured Sundays already use the September weekly-off allowance.
        db["attendance"].insert_one({"user_id": str(self.other["_id"]), "employee_id": "E-202", "attendance_date": "2026-09-01",
                                     "status": "Leave", "leave_type_code": "ML", "medical_leave": True})
        day = self.calendar_day(self.other, "2026-09-07")
        self.assertEqual(day["request_options"], ["Present", "Absent", "Holiday"])
        self.assertEqual(len(day["option_notes"]), 2)

    def test_response_is_serialized_per_employee_and_month(self):
        first = ar.create_attendance_request(payload(("2026-09-07", "Present")), self.employee["auth"])
        second = ar.create_attendance_request(payload(("2026-09-08", "Present")), self.employee["auth"])
        db["attendance_requests"].update_one({"_id": ObjectId(second["id"])}, {"$set": {"status": "processing", "processing_started_at": datetime.now(timezone.utc)}})
        self.assert_http(409, ar.respond_to_attendance_request, first["id"], decisions(("2026-09-07", "Present")), self.hr["auth"])
        self.assertEqual(db["attendance_requests"].find_one({"_id": ObjectId(first["id"])})["status"], "pending")


    def test_concurrent_response_is_rejected_while_processing(self):
        created = ar.create_attendance_request(payload(("2026-09-07", "Present")), self.employee["auth"])
        db["attendance_requests"].update_one({"_id": ObjectId(created["id"])}, {"$set": {"status": "processing", "processing_started_at": datetime.now(timezone.utc)}})
        self.assert_http(409, ar.respond_to_attendance_request, created["id"], decisions(("2026-09-07", "Present")), self.hr["auth"])
        self.assertEqual(db["attendance"].count_documents({"attendance_source": "ATTENDANCE_REQUEST"}), 0)

    def test_hr_inbox_filters(self):
        ar.create_attendance_request(payload(("2026-09-07", "Present")), self.employee["auth"])
        second = ar.create_attendance_request(payload(("2026-09-08", "Holiday")), self.other["auth"])
        ar.respond_to_attendance_request(second["id"], decisions(("2026-09-08", "Holiday")), self.hr["auth"])

        self.assertEqual(len(ar.list_attendance_requests(None, None, None, None, None, self.hr["auth"])), 2)
        pending = ar.list_attendance_requests("pending", None, None, None, None, self.hr["auth"])
        self.assertEqual([item["employee_id"] for item in pending], ["E-201"])
        self.assertEqual([item["employee_id"] for item in ar.list_attendance_requests("approved", None, None, None, None, self.hr["auth"])], ["E-202"])
        self.assertEqual(len(ar.list_attendance_requests(None, "bala", None, None, None, self.hr["auth"])), 1)
        self.assertEqual(len(ar.list_attendance_requests(None, None, "finance", "2026-09", None, self.hr["auth"])), 2)
        self.assertEqual(len(ar.list_attendance_requests(None, None, None, None, date(2026, 9, 8), self.hr["auth"])), 1)


if __name__ == "__main__":
    unittest.main()
