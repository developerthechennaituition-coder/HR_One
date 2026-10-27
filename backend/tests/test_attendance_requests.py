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
from app import leave, payroll  # noqa: E402
from app.db.mongodb import client, db, ensure_audit_logs_collection  # noqa: E402
from app.session import active_sessions  # noqa: E402

TODAY = date(2026, 10, 15)


def payload(*dates, remarks=None):
    return ar.AttendanceRequestCreate(
        dates=[{"attendance_date": iso, "requested_status": status} for iso, status in dates],
        remarks=remarks,
    )


def decisions(*items, comments=None):
    return ar.AttendanceRequestResponsePayload(
        decisions=[{"attendance_date": iso, "final_status": status} for iso, status in items],
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
                     "monthly_payroll_records", "leave_notifications", "employee_holiday_calendars", "holidays", "leave_policies"]:
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
        self.assertEqual(days["2026-09-01"]["status"], "Not Applicable")
        self.assertEqual((days["2026-09-03"]["status"], days["2026-09-03"]["attendance_category"], days["2026-09-03"]["check_in_time"]), ("Present", "Office", "09:05"))
        self.assertEqual(days["2026-09-04"]["attendance_category"], "Work From Home")
        self.assertEqual(days["2026-09-06"]["status"], "Weekly Off")
        self.assertEqual(days["2026-09-10"]["status"], "Holiday")
        self.assertEqual(days["2026-09-07"]["status"], "Not Marked")
        self.assertTrue(days["2026-09-07"]["eligible_for_request"])
        for iso in ["2026-09-01", "2026-09-03", "2026-09-06", "2026-09-10"]:
            self.assertFalse(days[iso]["eligible_for_request"])
        october = {d["attendance_date"]: d for d in ar.my_attendance_calendar(2026, 10, self.employee["auth"])["days"]}
        self.assertFalse(october["2026-10-15"]["eligible_for_request"])  # today
        self.assertEqual(october["2026-10-20"]["status"], "Upcoming")
        self.assert_http(400, ar.my_attendance_calendar, 2026, 11, self.employee["auth"])
        self.assertEqual(db["attendance"].count_documents({}), before)

    # -- Employee requests --------------------------------------------------

    def test_only_eligible_not_marked_dates_can_be_requested(self):
        for iso in ["2026-09-03", "2026-09-06", "2026-09-10", "2026-09-01", "2026-10-15", "2026-10-20"]:
            self.assert_http(422, ar.create_attendance_request, payload((iso, "Present")), self.employee["auth"])
        self.assertEqual(db["attendance_requests"].count_documents({}), 0)
        with self.assertRaises(Exception):
            payload(("2026-09-07", "Absent"))  # Employees cannot request Absent.

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

        self.assertEqual(result["status"], "partially_approved")
        self.assertEqual(result["summary"], {"applied": 3, "not_applied": 0, "notification_sent": True, "outdated_payroll_periods": []})
        self.assertEqual([(d["final_status"], d["outcome"]) for d in result["dates"]], [("Present", "approved"), ("Absent", "rejected"), ("Present", "changed")])
        self.assertEqual(result["reviewed_by"], str(self.hr["_id"]))
        actions = {item["action"] for item in db["audit_logs"].find({"$or": [{"entity_id": created["id"]}, {"new_value.attendance_request_id": created["id"]}]})}
        self.assertTrue({"ATTENDANCE_REQUEST_SUBMITTED", "ATTENDANCE_REQUEST_RESPONDED", "attendance.request_decision"} <= actions, actions)
        records = {r["attendance_date"]: r for r in db["attendance"].find({"attendance_request_id": created["id"]})}
        self.assertEqual({k: v["status"] for k, v in records.items()}, {"2026-09-07": "Present", "2026-09-08": "Absent", "2026-09-09": "Present"})
        for record in records.values():
            self.assertIsNone(record["check_in_time"])  # No fabricated punches.
            self.assertEqual(record["employee_id"], "E-201")
        self.assertEqual(self.calendar_day(self.employee, "2026-09-07")["status"], "Present")
        self.assertFalse(self.calendar_day(self.employee, "2026-09-07")["eligible_for_request"])
        self.assertEqual(db["attendance_request_date_claims"].count_documents({}), 0)

        after = payroll._attendance_summary_for_employee("E-201", 9, 2026)
        self.assertEqual(after["present_days"], before["present_days"] + 2)
        self.assertEqual(after["absent_days"], before["absent_days"] + 1)

        notes = list(db["leave_notifications"].find({"recipient_user_id": str(self.employee["_id"])}))
        self.assertEqual(len(notes), 1)
        for text in ["07 Sep 2026 — Requested Present — Approved as Present", "08 Sep 2026 — Requested Present — Changed to Absent",
                     "09 Sep 2026 — Requested Holiday — Changed to Present", "Verified with team lead", created["request_number"]]:
            self.assertIn(text, notes[0]["message"])

        # Retried submission: no reapplication, no duplicate records or notifications.
        self.assert_http(409, ar.respond_to_attendance_request, created["id"], decisions(("2026-09-07", "Absent"), ("2026-09-08", "Absent"), ("2026-09-09", "Absent")), self.hr["auth"])
        self.assertEqual(db["attendance"].count_documents({"attendance_request_id": created["id"]}), 3)
        self.assertEqual(db["leave_notifications"].count_documents({"recipient_user_id": str(self.employee["_id"])}), 1)
        history = ar.my_attendance_requests(self.employee["auth"])
        self.assertEqual(history[0]["status"], "partially_approved")

    def test_existing_records_created_after_submission_are_preserved(self):
        created = ar.create_attendance_request(payload(("2026-09-07", "Present"), ("2026-09-08", "Present")), self.employee["auth"])
        db["attendance"].insert_one({"user_id": str(self.employee["_id"]), "employee_id": "E-201", "attendance_date": "2026-09-08",
                                     "status": "Present", "check_in_time": "09:00", "attendance_source": "MANUAL", "attendance_type": "OFFICE"})

        result = ar.respond_to_attendance_request(created["id"], decisions(("2026-09-07", "Holiday"), ("2026-09-08", "Absent")), self.hr["auth"])

        self.assertEqual([d["applied"] for d in result["dates"]], [True, False])
        self.assertIn("existing record was preserved", result["dates"][1]["message"])
        manual = db["attendance"].find_one({"user_id": str(self.employee["_id"]), "attendance_date": "2026-09-08"})
        self.assertEqual((manual["status"], manual["check_in_time"], manual["attendance_source"]), ("Present", "09:00", "MANUAL"))
        self.assertEqual(result["status"], "rejected")  # Nothing approved as requested.

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
        self.assertFalse(days["2026-09-05"]["eligible_for_request"])
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
