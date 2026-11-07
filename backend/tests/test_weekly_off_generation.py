"""Tests for HR Admin "Generate Weekly Off" (Modify the Previous Attendance popup).

Planner rules are pure and always run. Endpoint tests use MongoDB like the other
integration tests and skip unless the database name ends with "_test":
    MONGODB_DATABASE=hrms_weekly_off_test python -m unittest tests.test_weekly_off_generation
"""

import os
import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

os.environ.setdefault("MONGODB_DATABASE", "hrms_weekly_off_test")

from bson import ObjectId  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app import attendance as att  # noqa: E402
from app.db.mongodb import client, db  # noqa: E402
from app.weekly_off_planner import (  # noqa: E402
    rank_weekdays, suggest_weekly_off_dates, validate_weekly_off_selection,
)

TODAY = date(2026, 11, 7)  # Saturday


def d(text):
    return date.fromisoformat(text)


def weekdays_of(year, month, weekday, last_day=None):
    import calendar
    last = last_day or calendar.monthrange(year, month)[1]
    return [date(year, month, n) for n in range(1, last + 1) if date(year, month, n).weekday() == weekday]


class PlannerTests(unittest.TestCase):
    def test_most_common_weekday_wins_and_max_four(self):
        candidates = weekdays_of(2026, 10, 6) + weekdays_of(2026, 10, 2)[:2]  # 4 Sundays, 2 Wednesdays
        picked = suggest_weekly_off_dates(candidates, 4)
        self.assertEqual(picked, weekdays_of(2026, 10, 6))
        self.assertEqual(len(suggest_weekly_off_dates(candidates, 9)), 4)  # never more than four

    def test_five_occurrences_still_four(self):
        sundays = weekdays_of(2026, 11, 6)  # five Sundays? Nov 2026 has 5 Sundays
        self.assertEqual(len(sundays), 5)
        self.assertEqual(suggest_weekly_off_dates(sundays, 4), sundays[:4])

    def test_fills_from_next_weekday_when_top_weekday_is_short(self):
        candidates = weekdays_of(2026, 10, 6)[:2] + weekdays_of(2026, 10, 5)[:1] + weekdays_of(2026, 10, 2)[:1]
        picked = suggest_weekly_off_dates(candidates, 4)
        self.assertEqual(len(picked), 4)
        self.assertEqual(picked, sorted(candidates))

    def test_fewer_candidates_than_remaining(self):
        self.assertEqual(len(suggest_weekly_off_dates([d("2026-10-04")], 4)), 1)
        self.assertEqual(suggest_weekly_off_dates([], 4), [])
        self.assertEqual(suggest_weekly_off_dates([d("2026-10-04")], 0), [])

    def test_company_weekdays_are_preferred_when_present_but_not_required(self):
        candidates = weekdays_of(2026, 10, 2)[:4] + weekdays_of(2026, 10, 5)[:3]  # 4 Wed, 3 Sat
        self.assertEqual(rank_weekdays(candidates)[0], 2)
        self.assertEqual(rank_weekdays(candidates, {5})[0], 5)
        self.assertEqual(rank_weekdays(candidates, {6})[0], 2)  # configured weekday has no candidates

    def test_tie_prefers_sunday_then_saturday(self):
        candidates = [d("2026-10-03"), d("2026-10-04")]
        self.assertEqual(rank_weekdays(candidates), [6, 5])

    def test_selection_validation(self):
        eligible = ["2026-10-04", "2026-10-11", "2026-10-18"]
        self.assertEqual(validate_weekly_off_selection(["2026-10-04"], eligible, 0), [])
        self.assertTrue(validate_weekly_off_selection([], eligible, 0))
        self.assertTrue(validate_weekly_off_selection(["2026-10-04", "2026-10-04"], eligible, 0))
        self.assertTrue(validate_weekly_off_selection(["2026-10-05"], eligible, 0))
        self.assertTrue(validate_weekly_off_selection(["2026-10-04", "2026-10-11"], eligible, 3))
        self.assertTrue(validate_weekly_off_selection(["2026-10-04"], eligible, 4))
        self.assertEqual(validate_weekly_off_selection(["2026-10-04"], eligible, 3), [])
        self.assertTrue(validate_weekly_off_selection(["2026-10-04"], eligible, 0, monthly_limit=0))


class WeeklyOffEndpointTests(unittest.TestCase):
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

    @classmethod
    def tearDownClass(cls):
        if db.name.endswith("_test"):
            client.drop_database(db.name)

    def setUp(self):
        for name in ["users", "attendance", "attendance_requests", "monthly_payroll_records", "leave_policies",
                     "leave_requests", "essl_raw_punches", "attendance_modification_history", "audit_logs",
                     "holidays", "employee_holiday_calendars"]:
            db[name].delete_many({})
        for patcher in (
            patch.object(att, "company_today", return_value=TODAY),
            patch.object(att, "_require_historical_attendance_permission", side_effect=lambda _h: self.actor),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.actor = {"_id": ObjectId(), "name": "HR Admin", "role": "HR Admin"}
        self.user_id = str(ObjectId())
        db["users"].insert_one({"_id": ObjectId(self.user_id), "name": "Asha", "employee_id": "E100", "is_active": True,
                                "joining_date": "2024-01-10", "date_of_joining": "2024-01-10"})
        db["leave_policies"].insert_one({"leave_type_id": "__company_policy__", "weekly_offs_per_month": 4})

    # -- helpers --------------------------------------------------------
    def mark(self, iso, status, **extra):
        db["attendance"].insert_one({"user_id": self.user_id, "attendance_date": iso, "status": status,
                                     "updated_at": datetime(2026, 10, 1, tzinfo=timezone.utc), **extra})

    def preview(self, year, month):
        return att.preview_historical_weekly_off(att.GenerateWeeklyOffPreviewPayload(
            user_id=self.user_id, year=year, month=month), "Bearer x")

    def approve(self, year, month, dates):
        return att.approve_historical_weekly_off(att.GenerateWeeklyOffApprovalPayload(
            user_id=self.user_id, year=year, month=month, attendance_dates=dates), "Bearer x")

    def weekly_offs(self, prefix):
        return sorted(r["attendance_date"] for r in db["attendance"].find(
            {"user_id": self.user_id, "status": "Weekly Off"}) if r["attendance_date"].startswith(prefix))

    # -- 1. restrictions removed ---------------------------------------
    def test_previous_month_without_company_weekly_off_config(self):
        result = self.preview(2026, 10)
        self.assertTrue(result["can_generate"])
        self.assertEqual(len(result["suggested_dates"]), 4)
        self.assertEqual(result["total_eligible"], 31)  # every Not Marked day of October
        self.assertIsNone(result["reason"])

    def test_current_month_without_company_weekly_off_config(self):
        result = self.preview(2026, 11)
        self.assertTrue(result["can_generate"])
        self.assertTrue(all(item["attendance_date"] <= TODAY.isoformat() for item in result["eligible_dates"]))
        self.assertEqual(result["total_eligible"], 7)

    def test_other_year(self):
        result = self.preview(2025, 3)
        self.assertEqual(len(result["suggested_dates"]), 4)
        self.assertTrue(all(day.startswith("2025-03") for day in result["suggested_dates"]))

    def test_suggestion_follows_not_marked_pattern(self):
        # Everything is marked Present except Wednesdays (4) and one Sunday.
        import calendar
        for n in range(1, calendar.monthrange(2026, 10)[1] + 1):
            day = date(2026, 10, n)
            if day.weekday() != 2 and day != d("2026-10-04"):
                self.mark(day.isoformat(), "Present", check_in_time="09:00")
        result = self.preview(2026, 10)
        self.assertEqual(result["suggested_dates"], [x.isoformat() for x in weekdays_of(2026, 10, 2)])
        self.assertEqual(result["suggested_weekdays"][0], "Wednesday")

    def test_configured_weekdays_are_only_a_preference(self):
        db["leave_policies"].update_one({"leave_type_id": "__company_policy__"}, {"$set": {"weekly_off_weekdays": ["sunday"]}})
        result = self.preview(2026, 10)
        self.assertEqual(result["suggested_dates"], [x.isoformat() for x in weekdays_of(2026, 10, 6)])

    # -- 2. protections --------------------------------------------------
    def test_only_not_marked_dates_are_candidates(self):
        for iso, status in [("2026-10-01", "Present"), ("2026-10-02", "Absent"), ("2026-10-03", "Leave"),
                            ("2026-10-04", "Holiday"), ("2026-10-05", "Weekly Off"), ("2026-10-06", "Half-Day Present"),
                            ("2026-10-07", "Medical Leave"), ("2026-10-08", "Permission")]:
            self.mark(iso, status)
        self.mark("2026-10-09", "Not Marked")
        eligible = {item["attendance_date"] for item in self.preview(2026, 10)["eligible_dates"]}
        for day in range(1, 9):
            self.assertNotIn(f"2026-10-{day:02d}", eligible)
        self.assertIn("2026-10-09", eligible)

    def test_not_marked_record_with_punch_is_not_candidate(self):
        self.mark("2026-10-09", "Not Marked", check_in_time="09:05")
        eligible = {item["attendance_date"] for item in self.preview(2026, 10)["eligible_dates"]}
        self.assertNotIn("2026-10-09", eligible)

    def test_approved_leave_and_pending_request_are_excluded(self):
        db["leave_requests"].insert_one({"user_id": self.user_id, "status": "approved", "from_date": "2026-10-12",
                                         "to_date": "2026-10-13", "day_part": "full_day"})
        db["attendance_requests"].insert_one({"user_id": self.user_id, "status": "pending",
                                              "dates": [{"attendance_date": "2026-10-14"}]})
        eligible = {item["attendance_date"] for item in self.preview(2026, 10)["eligible_dates"]}
        self.assertFalse({"2026-10-12", "2026-10-13", "2026-10-14"} & eligible)

    def test_holidays_are_excluded(self):
        with patch("app.leave._holiday_dates", return_value={date(2026, 10, 2), date(2026, 10, 20)}):
            eligible = {item["attendance_date"] for item in self.preview(2026, 10)["eligible_dates"]}
        self.assertFalse({"2026-10-02", "2026-10-20"} & eligible)
        self.assertEqual(len(eligible), 29)

    def test_raw_device_punches_without_attendance_record_are_excluded(self):
        db["essl_raw_punches"].insert_one({"user_id": self.user_id, "employee_id": "E100",
                                           "punch_at": datetime(2026, 10, 15, 3, 45, tzinfo=timezone.utc)})
        eligible = {item["attendance_date"] for item in self.preview(2026, 10)["eligible_dates"]}
        self.assertNotIn("2026-10-15", eligible)

    def test_dates_before_joining_are_excluded_and_earlier_month_rejected(self):
        db["users"].update_one({"_id": ObjectId(self.user_id)},
                               {"$set": {"joining_date": "2026-10-20", "date_of_joining": "2026-10-20"}})
        eligible = [item["attendance_date"] for item in self.preview(2026, 10)["eligible_dates"]]
        self.assertEqual(min(eligible), "2026-10-20")
        with self.assertRaises(HTTPException) as ctx:
            self.preview(2026, 9)
        self.assertEqual(ctx.exception.status_code, 400)
        with self.assertRaises(HTTPException):
            self.approve(2026, 10, ["2026-10-19"])

    def test_future_month_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            self.preview(2026, 12)
        self.assertEqual(ctx.exception.status_code, 400)

    # -- 3. monthly limit ------------------------------------------------
    def test_fewer_than_four_existing_allows_remaining_only(self):
        self.mark("2026-10-03", "Weekly Off")
        self.mark("2026-10-10", "Weekly Off")
        result = self.preview(2026, 10)
        self.assertEqual(result["existing_weekly_offs"], 2)
        self.assertEqual(result["remaining_limit"], 2)
        self.assertEqual(len(result["suggested_dates"]), 2)
        self.assertNotIn("2026-10-03", result["suggested_dates"])
        with self.assertRaises(HTTPException) as ctx:
            self.approve(2026, 10, ["2026-10-04", "2026-10-11", "2026-10-18"])
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(len(self.weekly_offs("2026-10")), 2)
        outcome = self.approve(2026, 10, result["suggested_dates"])
        self.assertEqual(outcome["total_generated"], 2)
        self.assertEqual(len(self.weekly_offs("2026-10")), 4)

    def test_four_existing_disables_generation(self):
        for iso in ("2026-10-03", "2026-10-10", "2026-10-17", "2026-10-24"):
            self.mark(iso, "Weekly Off")
        result = self.preview(2026, 10)
        self.assertTrue(result["limit_reached"])
        self.assertFalse(result["can_generate"])
        self.assertEqual(result["suggested_dates"], [])
        with self.assertRaises(HTTPException) as ctx:
            self.approve(2026, 10, ["2026-10-04"])
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(len(self.weekly_offs("2026-10")), 4)

    def test_policy_limit_below_four_is_respected(self):
        db["leave_policies"].update_one({"leave_type_id": "__company_policy__"}, {"$set": {"weekly_offs_per_month": 2}})
        self.assertEqual(len(self.preview(2026, 10)["suggested_dates"]), 2)
        db["leave_policies"].update_one({"leave_type_id": "__company_policy__"}, {"$set": {"weekly_offs_per_month": 9}})
        self.assertEqual(len(self.preview(2026, 10)["suggested_dates"]), 4)

    # -- 4. saving ------------------------------------------------------
    def test_approve_saves_records_history_and_audit(self):
        suggested = self.preview(2026, 10)["suggested_dates"]
        outcome = self.approve(2026, 10, suggested)
        self.assertEqual(outcome["generated_dates"], sorted(suggested))
        self.assertEqual(self.weekly_offs("2026-10"), sorted(suggested))
        row = db["attendance"].find_one({"user_id": self.user_id, "attendance_date": suggested[0]})
        self.assertEqual(row["attendance_source"], "MANUAL_WEEKLY_OFF")
        self.assertEqual(row["marked_by"], str(self.actor["_id"]))
        history = db["attendance_modification_history"].find({"user_id": self.user_id})
        self.assertEqual(len(history), 4)
        self.assertEqual(history[0]["changes"]["status"], {"before": "Not Marked", "after": "Weekly Off"})
        self.assertEqual(history[0]["modified_by"], str(self.actor["_id"]))
        self.assertEqual(db["audit_logs"].count_documents({"action": "attendance.weekly_off_manually_generated"}), 4)
        # Shown by the existing history endpoint.
        shown = att.get_historical_attendance_audit_history(self.user_id, suggested[0], "Bearer x")
        self.assertEqual(shown[0]["changes"]["status"]["after"], "Weekly Off")

    def test_admin_can_swap_to_another_not_marked_date(self):
        outcome = self.approve(2026, 10, ["2026-10-02", "2026-10-09", "2026-10-16", "2026-10-23"])
        self.assertEqual(outcome["total_generated"], 4)

    def test_existing_not_marked_record_is_updated_not_duplicated(self):
        self.mark("2026-10-04", "Not Marked")
        self.approve(2026, 10, ["2026-10-04"])
        rows = db["attendance"].find({"user_id": self.user_id, "attendance_date": "2026-10-04"})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "Weekly Off")

    def test_never_overwrites_marked_dates_or_duplicates(self):
        self.mark("2026-10-05", "Present", check_in_time="09:00")
        self.mark("2026-10-06", "Absent")
        self.mark("2026-10-07", "Leave", leave_request_id="L1")
        self.mark("2026-10-08", "Holiday")
        self.mark("2026-10-09", "Weekly Off")
        before = {r["attendance_date"]: r["status"] for r in db["attendance"].find({"user_id": self.user_id})}
        for iso in ("2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09"):
            with self.assertRaises(HTTPException) as ctx:
                self.approve(2026, 10, [iso])
            self.assertEqual(ctx.exception.status_code, 409)
        with self.assertRaises(HTTPException):
            self.approve(2026, 10, ["2026-10-04", "2026-10-04"])
        after = {r["attendance_date"]: r["status"] for r in db["attendance"].find({"user_id": self.user_id})}
        self.assertEqual(before, after)

    def test_dates_outside_month_future_or_malformed_rejected(self):
        for bad in ("2026-09-30", "2026-11-30", "not-a-date", "2026-10-4"):
            with self.assertRaises(HTTPException) as ctx:
                self.approve(2026, 10, [bad])
            self.assertEqual(ctx.exception.status_code, 400, bad)
        with self.assertRaises(HTTPException):
            self.approve(2026, 11, ["2026-11-08"])  # future date in the current month
        self.assertEqual(db["attendance"].count_documents({}), 0)

    def test_stale_preview_is_not_overwritten(self):
        self.mark("2026-10-04", "Not Marked")
        stale = {"2026-10-04": "2000-01-01T00:00:00+00:00"}
        outcome = att.approve_historical_weekly_off(att.GenerateWeeklyOffApprovalPayload(
            user_id=self.user_id, year=2026, month=10, attendance_dates=["2026-10-04"], expected_updated_at=stale), "Bearer x")
        self.assertEqual(outcome["total_generated"], 0)
        self.assertEqual(outcome["total_skipped"], 1)

    # -- 5. payroll locks ------------------------------------------------
    def test_payroll_finalized_month_is_protected(self):
        for payroll_status in ("Approved", "Processed", "Paid"):
            db["monthly_payroll_records"].delete_many({})
            db["monthly_payroll_records"].insert_one({"employee_id": "E100", "period": "2026-10", "status": payroll_status})
            result = self.preview(2026, 10)
            self.assertTrue(result["payroll_locked"])
            self.assertFalse(result["can_generate"])
            with self.assertRaises(HTTPException) as ctx:
                self.approve(2026, 10, ["2026-10-04"])
            self.assertEqual(ctx.exception.status_code, 409)
            self.assertEqual(db["attendance"].count_documents({}), 0)
        # Other months are unaffected.
        self.assertTrue(self.preview(2026, 9)["can_generate"])

    def test_calculated_payroll_is_flagged_outdated(self):
        db["monthly_payroll_records"].insert_one({"employee_id": "E100", "period": "2026-10", "status": "Calculated"})
        self.approve(2026, 10, ["2026-10-04"])
        record = db["monthly_payroll_records"].find_one({"employee_id": "E100", "period": "2026-10"})
        self.assertTrue(record["attendance_outdated"])
        self.assertIn("2026-10-04", record["attendance_outdated_dates"])


if __name__ == "__main__":
    unittest.main()
