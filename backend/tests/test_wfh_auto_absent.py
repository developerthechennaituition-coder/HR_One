import unittest
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from app import attendance


class WfhAutomaticAbsentTests(unittest.TestCase):
    def setUp(self):
        self.user_id = "64f000000000000000000001"
        self.employee = {
            "_id": self.user_id,
            "employee_id": "E-101",
            "is_active": True,
            "is_wfh_enabled": True,
            "wfh_permitted_from": "2026-10-27",
            "wfh_permitted_to": "2026-10-27",
            "wfh_permitted_end_time": "18:00",
            "wfh_permitted_weekdays": ["tuesday"],
        }
        self.attendance = MagicMock()
        self.attendance.update_one.return_value = SimpleNamespace(upserted_id="new-record")
        self.db = MagicMock()
        self.db.__getitem__.return_value.find_one.return_value = None
        self.patches = [
            patch.object(attendance, "users_collection", MagicMock(find=MagicMock(return_value=[self.employee]))),
            patch.object(attendance, "attendance_collection", self.attendance),
            patch.object(attendance, "db", self.db),
            patch.object(attendance, "employee_joining_date", return_value=date(2026, 10, 27)),
            patch.object(attendance, "company_weekly_off_weekdays", return_value=set()),
            patch.object(attendance, "_approved_leaves_for_date", return_value=[]),
            patch.object(attendance, "_wfh_late_check_in_request", return_value=None),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.tz = ZoneInfo("Asia/Kolkata")

    def run_at(self, hour, minute=0):
        now = datetime(2026, 10, 27, hour, minute, tzinfo=self.tz)
        return attendance.generate_due_wfh_absences(now)

    def inserted_record(self):
        return self.attendance.update_one.call_args.args[1]["$setOnInsert"]

    def test_does_not_mark_absent_before_configured_end_time(self):
        result = self.run_at(17, 59)
        self.assertEqual(result["created"], 0)
        self.attendance.update_one.assert_not_called()

    def test_marks_absent_at_configured_end_time_when_no_extension_exists(self):
        result = self.run_at(18, 0)
        self.assertEqual(result["created"], 1)
        record = self.inserted_record()
        self.assertEqual(record["status"], "Absent")
        self.assertEqual(record["attendance_source"], "WFH_AUTO_ABSENT")
        self.assertEqual(record["wfh_scheduled_end_time"], "18:00")

    def test_uses_date_specific_approved_permission_end_time(self):
        with patch.object(
            attendance,
            "_wfh_late_check_in_request",
            return_value={"status": "approved", "permission_date": "2026-10-27", "end_time": "12:00"},
        ):
            result = self.run_at(12, 0)
        self.assertEqual(result["created"], 1)
        self.assertEqual(self.inserted_record()["wfh_scheduled_end_time"], "12:00")

    def test_waits_until_approved_permission_end_time_even_after_configured_end(self):
        with patch.object(
            attendance,
            "_wfh_late_check_in_request",
            return_value={"status": "approved", "permission_date": "2026-10-27", "end_time": "19:00"},
        ):
            result = self.run_at(18, 30)
        self.assertEqual(result["created"], 0)
        self.attendance.update_one.assert_not_called()

    def test_pending_permission_does_not_override_configured_end_time(self):
        with patch.object(
            attendance,
            "_wfh_late_check_in_request",
            return_value={"status": "pending", "end_time": "20:00"},
        ):
            result = self.run_at(18, 0)
        self.assertEqual(result["created"], 1)
        self.assertEqual(self.inserted_record()["wfh_scheduled_end_time"], "18:00")

    def test_existing_attendance_is_never_overwritten(self):
        self.attendance.update_one.return_value = SimpleNamespace(upserted_id=None)
        result = self.run_at(18, 0)
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["skipped"], 0)
        self.attendance.update_one.assert_called_once()
        self.assertIn("$setOnInsert", self.attendance.update_one.call_args.args[1])

    def test_approved_payroll_period_is_not_modified(self):
        self.db.__getitem__.return_value.find_one.return_value = {"status": "aPpRoVeD"}
        result = self.run_at(18, 0)
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["skipped"], 1)
        self.attendance.update_one.assert_not_called()

    def test_invalid_approved_permission_end_time_is_not_assumed_expired(self):
        with patch.object(
            attendance,
            "_wfh_late_check_in_request",
            return_value={"status": "approved", "end_time": "not-a-time"},
        ):
            result = self.run_at(23, 0)
        self.assertEqual(result["created"], 0)
        self.attendance.update_one.assert_not_called()


if __name__ == "__main__":
    unittest.main()
