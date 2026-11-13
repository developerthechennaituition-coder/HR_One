import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from app.attendance_calculations import (
    ATTENDANCE_ISSUE_STATUS,
    effective_attendance_status,
    half_day_status_override,
    missing_checkout_issue,
)

TZ = ZoneInfo("Asia/Kolkata")


def at(day, hour=12, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=TZ)


class AttendanceIssueTests(unittest.TestCase):
    def test_past_day_with_check_in_only_is_issue(self):
        status = effective_attendance_status(
            "Present", check_in="09:05", check_out=None, attendance_date="2026-11-12", now=at(13)
        )
        self.assertEqual(status, ATTENDANCE_ISSUE_STATUS)

    def test_open_current_day_is_not_issue(self):
        status = effective_attendance_status(
            "Present", check_in="09:05", check_out=None, attendance_date="2026-11-13", now=at(13, 23, 59)
        )
        self.assertEqual(status, "Present")

    def test_completed_day_stays_present(self):
        status = effective_attendance_status(
            "Present", check_in="09:05", check_out="18:00", attendance_date="2026-11-12", now=at(13)
        )
        self.assertEqual(status, "Present")

    def test_no_check_in_is_never_issue(self):
        for stored in ("Present", "Absent", "Not Marked"):
            self.assertEqual(
                effective_attendance_status(stored, check_in=None, check_out=None, attendance_date="2026-11-12", now=at(13)),
                stored,
            )

    def test_protected_statuses_never_become_issue(self):
        for stored in ("Leave", "Holiday", "Weekly Off", "Half-Day Present", "Absent", "Medical Leave"):
            self.assertEqual(
                effective_attendance_status(stored, check_in="09:00", check_out=None, attendance_date="2026-11-12", now=at(13)),
                stored,
            )

    def test_stored_issue_reverts_when_check_out_exists(self):
        status = effective_attendance_status(
            ATTENDANCE_ISSUE_STATUS, check_in="09:00", check_out="18:30", attendance_date="2026-11-12", now=at(13)
        )
        self.assertEqual(status, "Present")

    def test_overnight_shift_stays_open_until_four_hours_after_end(self):
        kwargs = dict(status="Present", check_in="22:00", check_out=None, attendance_date="2026-11-12",
                      scheduled_start="22:00", scheduled_end="06:00")
        self.assertFalse(missing_checkout_issue(now=at(13, 9, 59), **kwargs))
        self.assertTrue(missing_checkout_issue(now=at(13, 10, 0), **kwargs))

    def test_issue_is_not_overridden_to_present_or_absent_by_half_day_rules(self):
        override = half_day_status_override(
            {"half_day_present": "No"}, total_duration_minutes=0, half_day_threshold_minutes=240,
            check_in="09:00", check_out=None, day_completed=True,
            current_status=ATTENDANCE_ISSUE_STATUS, recalculated=True,
        )
        self.assertIsNone(override)

    def test_resolved_issue_recalculates_to_present_for_full_day(self):
        override = half_day_status_override(
            {"half_day_present": "No"}, total_duration_minutes=540, half_day_threshold_minutes=240,
            check_in="09:00", check_out="18:00", day_completed=True,
            current_status=ATTENDANCE_ISSUE_STATUS, recalculated=True,
        )
        self.assertEqual(override, "Present")


if __name__ == "__main__":
    unittest.main()


class IssueExportTests(unittest.TestCase):
    def test_workbook_lists_issue_dates_and_issue_count_for_selected_employees_only(self):
        from openpyxl import load_workbook
        from app.attendance_export import build_multi_employee_attendance_workbook

        def row(day, status, **extra):
            return {"attendance_date": f"2026-11-{day:02d}", "day": "Day", "status": status,
                    "first_half_status": status, "second_half_status": status, **extra}

        reports = [
            {"info": {"name": "Asha", "employee_id": "E1"}, "rows": [
                row(10, "Present"), row(11, "Attendance Issue", check_in_text="09:10"),
                row(12, "Absent", attendance_source="WFH_AUTO_ABSENT")]},
            {"info": {"name": "Ravi", "employee_id": "E2"}, "rows": [row(10, "Present")]},
        ]
        output = build_multi_employee_attendance_workbook(
            reports, company_name="Acme", month=11, year=2026, generated_on="13-Nov-2026")
        ws = load_workbook(output).active
        text = [str(c.value) for r in ws.iter_rows() for c in r if c.value is not None]
        self.assertIn("ATTENDANCE ISSUE DETAILS - Asha  (Unresolved issue dates: 1)", text)
        self.assertIn("ATTENDANCE ISSUE DETAILS - Ravi  (Unresolved issue dates: 0)", text)
        self.assertIn("2026-11-11", text)
        self.assertTrue(any("EMPLOYEE 2: Ravi" == t for t in text))
        self.assertNotIn("EMPLOYEE 3", " ".join(text))
        # Issue Count label is present and the issue day is not counted as Present.
        labels = [(c.value, ws.cell(c.row, c.column + 2).value) for r in ws.iter_rows() for c in r if c.value == "Issue Count"]
        self.assertEqual(labels[0][1], 1)
        present = [(ws.cell(c.row, c.column + 2).value) for r in ws.iter_rows() for c in r if c.value == "Present Days"]
        self.assertEqual(present[0], 1)
