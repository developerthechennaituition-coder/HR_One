"""Integration tests for religion-based holiday calendars against a real MongoDB.

Run in its own process with a throwaway database, for example:
    MONGODB_DATABASE=hrms_religion_holiday_test python -m unittest tests.test_religion_holidays
The tests skip unless the database name ends with "_test", and drop it afterwards.
"""

import os
import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

os.environ.setdefault("MONGODB_DATABASE", "hrms_religion_holiday_test")

from bson import ObjectId  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from pymongo.errors import PyMongoError  # noqa: E402

from app import attendance_requests as ar  # noqa: E402
from app import core_routes as cr  # noqa: E402
from app import leave, payroll  # noqa: E402
from app.db.mongodb import (  # noqa: E402
    client,
    db,
    ensure_audit_logs_collection,
    ensure_holiday_calendars_collection,
    ensure_holidays_collection,
)
from app.session import active_sessions  # noqa: E402

THIS_YEAR = datetime.now(cr.ORGANIZATION_TIMEZONE).year


class ReligionHolidayCalendarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not db.name.endswith("_test"):
            raise unittest.SkipTest(f"Refusing to run against database {db.name!r}; use a *_test database.")
        try:
            client.admin.command("ping")
        except PyMongoError as exc:
            raise unittest.SkipTest(f"MongoDB is not reachable: {exc}")
        client.drop_database(db.name)
        ensure_audit_logs_collection()
        ensure_holiday_calendars_collection()
        ensure_holidays_collection()
        # A legacy year-only unique index must be replaced by religion + year.
        db["holiday_calendars"].create_index(
            "calendar_year", unique=True, partialFilterExpression={"is_active": True},
            name="holiday_calendar_active_year_unique",
        )
        db["attendance"].create_index([("user_id", 1), ("attendance_date", 1)], unique=True)
        cr.create_indexes()

    @classmethod
    def tearDownClass(cls):
        client.drop_database(db.name)

    def setUp(self):
        for name in ["users", "employee_details", "holiday_calendars", "holidays", "attendance", "notifications",
                     "notification_recipients", "monthly_payroll_records", "leave_policies", "organization_audit_history",
                     "attendance_requests", "attendance_request_date_claims", "companies", "employee_holiday_calendars"]:
            db[name].delete_many({})
        self.company_id = str(db["companies"].insert_one(
            {"company": "Acme", "legal_entity": "Acme Pvt Ltd", "branch": "HQ", "location": "Chennai", "is_active": True}
        ).inserted_id)
        self.hr = self.make_user("Hema HR", "HR-1", "HR Admin", None)
        self.hindu = self.make_user("Asha", "E-1", "Employee", "Hindu")
        self.hindu_two = self.make_user("Bala", "E-2", "Employee", " hindu ")
        self.christian = self.make_user("Clara", "E-3", "Employee", "Christian")
        self.no_religion = self.make_user("Dev", "E-4", "Employee", "")
        self.inactive = self.make_user("Old", "E-5", "Employee", "Jain", is_active=False)

    def make_user(self, name, employee_id, role, religion, is_active=True):
        user = {"_id": ObjectId(), "name": name, "employee_id": employee_id, "role": role, "is_active": is_active,
                "date_of_joining": datetime(2024, 1, 1)}
        db["users"].insert_one(user)
        db["employee_details"].insert_one({"user_id": str(user["_id"]), "employee_id": employee_id, "religion": religion})
        token = f"token-{user['_id']}"
        active_sessions[token] = str(user["_id"])
        self.addCleanup(active_sessions.pop, token, None)
        user["auth"] = f"Bearer {token}"
        return user

    def calendar_payload(self, religion="Hindu", year=THIS_YEAR, name="Hindu holidays"):
        return cr.HolidayCalendarPayload(company_id=self.company_id, location="Chennai", calendar_name=name,
                                         calendar_year=year, religion=religion)

    def holiday_payload(self, calendar_id, iso, name, **extra):
        return cr.HolidayPayload(calendar_id=calendar_id, holiday_date=date.fromisoformat(iso), holiday_name=name, **extra)

    def assert_http(self, status_code, func, *args, **kwargs):
        with self.assertRaises(HTTPException) as error:
            func(*args, **kwargs)
        self.assertEqual(error.exception.status_code, status_code, error.exception.detail)
        return str(error.exception.detail)

    def recipients(self, title_part):
        notes = [item["_id"] for item in db["notifications"].find({"title": {"$regex": title_part}})]
        return {item["user_id"] for item in db["notification_recipients"].find({"notification_id": {"$in": notes}})}

    def published_calendar(self):
        calendar = cr.create_holiday_calendar(self.calendar_payload(), user=self.hr)
        cr.create_holiday(calendar["id"], self.holiday_payload(calendar["id"], f"{THIS_YEAR}-12-24", "Festival Eve"), user=self.hr)
        cr.create_holiday(calendar["id"], self.holiday_payload(calendar["id"], f"{THIS_YEAR}-12-28", "Festival Day"), user=self.hr)
        cr.publish_holiday_calendar(calendar["id"], user=self.hr)
        return calendar

    # -- Calendar management -------------------------------------------------

    def test_options_list_only_religions_of_active_employees_and_two_years(self):
        options = cr.holiday_calendar_options(_user=self.hr)
        self.assertEqual(options["religions"], ["Christian", "Hindu"])  # deduplicated, blank and inactive excluded
        self.assertEqual(options["allowed_years"], [THIS_YEAR, THIS_YEAR + 1])
        self.assertNotIn("holiday_calendar_active_year_unique", db["holiday_calendars"].index_information())

    def test_calendar_creation_rules(self):
        self.assertIn("Select the religion", self.assert_http(422, cr.create_holiday_calendar, self.calendar_payload(religion=None), user=self.hr))
        self.assertIn("No active employee", self.assert_http(422, cr.create_holiday_calendar, self.calendar_payload(religion="Jain"), user=self.hr))
        for year in (THIS_YEAR - 1, THIS_YEAR + 2):
            self.assertIn("current year", self.assert_http(422, cr.create_holiday_calendar, self.calendar_payload(year=year), user=self.hr))
        first = cr.create_holiday_calendar(self.calendar_payload(religion="hindu"), user=self.hr)
        self.assertEqual((first["religion"], first["religion_key"], first["status"]), ("Hindu", "hindu", "Draft"))
        self.assertIn("already exists", self.assert_http(409, cr.create_holiday_calendar, self.calendar_payload(), user=self.hr))
        # Same religion next year and another religion this year are separate calendars.
        cr.create_holiday_calendar(self.calendar_payload(year=THIS_YEAR + 1), user=self.hr)
        cr.create_holiday_calendar(self.calendar_payload(religion="Christian", name="Christian holidays"), user=self.hr)
        # A duplicate that bypasses the pre-check is still stopped by the unique index.
        with self.assertRaises(Exception):
            db["holiday_calendars"].insert_one({"company_id": self.company_id, "location": "Chennai", "calendar_name": "x",
                                                "calendar_year": THIS_YEAR, "religion_key": "hindu", "is_active": True})

    def test_holiday_validation_and_published_calendar_metadata(self):
        calendar = cr.create_holiday_calendar(self.calendar_payload(), user=self.hr)
        self.assert_http(400, cr.create_holiday, calendar["id"], self.holiday_payload(calendar["id"], f"{THIS_YEAR + 1}-01-05", "Wrong year"), user=self.hr)
        self.assert_http(422, cr.publish_holiday_calendar, calendar["id"], user=self.hr)  # nothing to publish
        cr.create_holiday(calendar["id"], self.holiday_payload(calendar["id"], f"{THIS_YEAR}-12-24", "Festival Eve"), user=self.hr)
        self.assertIn("already on", self.assert_http(409, cr.create_holiday, calendar["id"], self.holiday_payload(calendar["id"], f"{THIS_YEAR}-12-24", "Other"), user=self.hr))
        cr.publish_holiday_calendar(calendar["id"], user=self.hr)
        renamed = cr.update_holiday_calendar(calendar["id"], self.calendar_payload(name="Hindu festivals"), user=self.hr)
        self.assertEqual(renamed["calendar_name"], "Hindu festivals")
        self.assert_http(409, cr.update_holiday_calendar, calendar["id"], self.calendar_payload(religion="Christian"), user=self.hr)
        self.assert_http(409, cr.update_holiday_calendar, calendar["id"], self.calendar_payload(year=THIS_YEAR + 1), user=self.hr)

    # -- Visibility and notifications -------------------------------------------

    def test_draft_is_private_and_publish_notifies_only_that_religion(self):
        calendar = cr.create_holiday_calendar(self.calendar_payload(), user=self.hr)
        cr.create_holiday(calendar["id"], self.holiday_payload(calendar["id"], f"{THIS_YEAR}-12-24", "Festival Eve"), user=self.hr)
        self.assertEqual(cr.get_my_religion_holidays(year=THIS_YEAR, user=self.hindu)["holidays"], [])
        self.assertEqual(db["notifications"].count_documents({}), 0)  # draft changes notify nobody
        self.assert_http(404, cr.get_holiday_calendar, calendar["id"], user=self.hindu)

        cr.publish_holiday_calendar(calendar["id"], user=self.hr)
        cr.publish_holiday_calendar(calendar["id"], user=self.hr)  # repeated submit
        self.assertEqual(db["notifications"].count_documents({"title": {"$regex": "published"}}), 1)
        self.assertEqual(self.recipients("published"), {str(self.hindu["_id"]), str(self.hindu_two["_id"])})

        mine = cr.get_my_religion_holidays(year=THIS_YEAR, user=self.hindu_two)
        self.assertEqual((mine["religion"], [h["holiday_name"] for h in mine["holidays"]]), ("hindu", ["Festival Eve"]))
        # Other religions and employees without a religion cannot see it, even directly.
        self.assertEqual(cr.get_my_religion_holidays(year=THIS_YEAR, user=self.christian)["holidays"], [])
        missing = cr.get_my_religion_holidays(year=THIS_YEAR, user=self.no_religion)
        self.assertIsNone(missing["religion"])
        self.assert_http(404, cr.get_holiday_calendar, calendar["id"], user=self.christian)
        self.assert_http(404, cr.list_calendar_holidays, calendar["id"], user=self.christian)
        self.assertEqual(cr.list_holiday_calendars(user=self.christian), [])
        self.assertEqual(len(cr.list_holiday_calendars(user=self.hindu)), 1)
        self.assert_http(400, cr.get_my_religion_holidays, year=THIS_YEAR + 2, user=self.hindu)
        self.assertEqual(cr.get_my_religion_holidays(year=THIS_YEAR - 1, user=self.hindu)["holidays"], [])  # history readable

    def test_published_changes_notify_reconcile_payroll_and_keep_audit(self):
        calendar = self.published_calendar()
        db["monthly_payroll_records"].insert_many([
            {"employee_id": "E-1", "period": f"{THIS_YEAR}-12", "status": "Calculated"},
            {"employee_id": "E-2", "period": f"{THIS_YEAR}-12", "status": "Paid"},
            {"employee_id": "E-3", "period": f"{THIS_YEAR}-12", "status": "Calculated"},
        ])
        added = cr.create_holiday(calendar["id"], self.holiday_payload(calendar["id"], f"{THIS_YEAR}-12-30", "Year End"), user=self.hr)
        self.assertEqual(added["status"], "Published")
        self.assertEqual(self.recipients("Holiday added"), {str(self.hindu["_id"]), str(self.hindu_two["_id"])})
        records = {item["employee_id"]: item for item in db["monthly_payroll_records"].find()}
        self.assertTrue(records["E-1"].get("attendance_outdated"))
        self.assertNotIn("attendance_outdated", records["E-2"])  # finalized payroll untouched
        self.assertNotIn("attendance_outdated", records["E-3"])  # other religion untouched

        holiday_id = added["id"]
        self.assert_http(422, cr.update_holiday, holiday_id, self.holiday_payload(calendar["id"], f"{THIS_YEAR}-12-31", "Year End"), reason=None)
        cr.update_holiday(holiday_id, self.holiday_payload(calendar["id"], f"{THIS_YEAR}-12-31", "Year End"), reason="Moved by HR")
        cr.update_holiday(holiday_id, self.holiday_payload(calendar["id"], f"{THIS_YEAR}-12-31", "Year End"), reason="Moved by HR")
        self.assertEqual(db["notifications"].count_documents({"title": {"$regex": "Holiday updated"}}), 1)
        cr.deactivate_holiday(holiday_id, reason="Not observed")
        self.assertEqual(self.recipients("Holiday removed"), {str(self.hindu["_id"]), str(self.hindu_two["_id"])})
        names = [h["holiday_name"] for h in cr.get_my_religion_holidays(year=THIS_YEAR, user=self.hindu)["holidays"]]
        self.assertEqual(names, ["Festival Eve", "Festival Day"])
        self.assertEqual(db["holidays"].count_documents({"_id": ObjectId(holiday_id), "status": "Cancelled"}), 1)  # soft delete
        actions = {event["action"] for event in cr.get_holiday_calendar_audit(calendar["id"], _user=self.hr)}
        self.assertTrue({"holiday_calendar_created", "holiday_calendar_published", "holiday_added", "holiday_changed", "holiday_removed"} <= actions)

    # -- Attendance and payroll integration -------------------------------------

    def test_published_holidays_reach_attendance_and_payroll_with_precedence(self):
        calendar = cr.create_holiday_calendar(self.calendar_payload(), user=self.hr)
        cr.create_holiday(calendar["id"], self.holiday_payload(calendar["id"], f"{THIS_YEAR}-12-24", "Festival Eve"), user=self.hr)
        cr.create_holiday(calendar["id"], self.holiday_payload(calendar["id"], f"{THIS_YEAR}-12-28", "Festival Day"), user=self.hr)
        today = patch.object(ar, "company_today", return_value=date(THIS_YEAR, 12, 31))
        today.start()
        self.addCleanup(today.stop)
        days = {d["attendance_date"]: d for d in ar.my_attendance_calendar(THIS_YEAR, 12, self.hindu["auth"])["days"]}
        self.assertEqual(days[f"{THIS_YEAR}-12-24"]["status"], "Not Marked")  # unpublished holidays do not count

        cr.publish_holiday_calendar(calendar["id"], user=self.hr)
        # An actual Present record wins over the holiday.
        db["attendance"].insert_one({"user_id": str(self.hindu["_id"]), "employee_id": "E-1", "attendance_date": f"{THIS_YEAR}-12-28",
                                     "status": "Present", "check_in_time": "09:00", "check_out_time": "18:00"})
        days = {d["attendance_date"]: d for d in ar.my_attendance_calendar(THIS_YEAR, 12, self.hindu["auth"])["days"]}
        self.assertEqual((days[f"{THIS_YEAR}-12-24"]["status"], days[f"{THIS_YEAR}-12-24"]["holiday_name"]), ("Holiday", "Festival Eve"))
        self.assertEqual(days[f"{THIS_YEAR}-12-28"]["status"], "Present")
        other = {d["attendance_date"]: d for d in ar.my_attendance_calendar(THIS_YEAR, 12, self.christian["auth"])["days"]}
        self.assertEqual(other[f"{THIS_YEAR}-12-24"]["status"], "Not Marked")
        self.assertEqual(db["attendance"].count_documents({"status": "Holiday"}), 0)  # no records are generated

        # Leave working days skip the holiday; payroll counts it once.
        working = leave._working_dates(self.hindu, date(THIS_YEAR, 12, 23), date(THIS_YEAR, 12, 25))
        self.assertNotIn(date(THIS_YEAR, 12, 24), working)
        summary = payroll._attendance_summary_for_employee("E-1", 12, THIS_YEAR)
        self.assertEqual((summary["holiday_days"], summary["present_days"]), (1, 1))
        self.assertEqual(payroll._attendance_summary_for_employee("E-3", 12, THIS_YEAR)["holiday_days"], 0)

    def test_religion_calendars_are_not_assigned_and_legacy_lookup_is_company_wide(self):
        calendar = self.published_calendar()
        employee_id = str(db["employees"].insert_one({"employee_id": "E-1", "employee_name": "Asha", "is_active": True}).inserted_id)
        payload = cr.EmployeeHolidayCalendarPayload(employee_id=employee_id, calendar_id=calendar["id"])
        self.assertIn("automatically", self.assert_http(400, cr.assign_employee_holiday_calendar, payload))
        db["employees"].delete_many({})
        # The profile's company-wide calendar must not pick up a religion calendar.
        info = cr.get_my_organization_information(user=self.hindu)
        self.assertIsNone(info["holiday_calendar"]["calendar"])


if __name__ == "__main__":
    unittest.main()
