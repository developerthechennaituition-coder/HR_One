import unittest
from datetime import date, datetime

from pydantic import ValidationError

from app.main import (
    EmployeeHolidayCalendarPayload,
    EmployeeHolidayCalendarResponse,
    HolidayCalendarPayload,
    HolidayCalendarResponse,
    HolidayPayload,
    HolidayResponse,
)


class HolidayModelTests(unittest.TestCase):
    company_id = "507f1f77bcf86cd799439011"
    calendar_id = "507f1f77bcf86cd799439012"
    employee_id = "507f1f77bcf86cd799439013"

    def test_calendar_contains_company_and_location_context(self):
        calendar = HolidayCalendarPayload(
            company_id=self.company_id,
            location="Chennai",
            calendar_name="India Public Holidays",
            calendar_year=2026,
            description="Company holiday calendar",
        )

        self.assertEqual(calendar.company_id, self.company_id)
        self.assertEqual(calendar.location, "Chennai")
        self.assertEqual(calendar.calendar_year, 2026)

    def test_holiday_entry_references_calendar_and_keeps_date_details(self):
        holiday = HolidayPayload(
            calendar_id=self.calendar_id,
            holiday_date=date(2026, 1, 26),
            holiday_name="Republic Day",
            holiday_type="Public",
            is_optional=False,
        )

        self.assertEqual(holiday.calendar_id, self.calendar_id)
        self.assertEqual(holiday.holiday_date, date(2026, 1, 26))
        self.assertFalse(holiday.is_optional)

    def test_employee_assignment_reuses_employee_and_calendar_references(self):
        assignment = EmployeeHolidayCalendarPayload(
            employee_id=self.employee_id,
            calendar_id=self.calendar_id,
        )

        self.assertEqual(assignment.employee_id, self.employee_id)
        self.assertEqual(assignment.calendar_id, self.calendar_id)

    def test_missing_required_context_is_rejected(self):
        with self.assertRaises(ValidationError):
            HolidayCalendarPayload(
                company_id="",
                location="Chennai",
                calendar_name="Calendar",
                calendar_year=2026,
            )

        with self.assertRaises(ValidationError):
            HolidayPayload(
                calendar_id=self.calendar_id,
                holiday_date=date(2026, 1, 26),
                holiday_name=" ",
            )

        with self.assertRaises(ValidationError):
            HolidayCalendarPayload(
                company_id=self.company_id,
                location="Chennai",
                calendar_name="Calendar",
                calendar_year=1999,
            )

    def test_response_models_include_identity_and_status_fields(self):
        calendar = HolidayCalendarResponse(
            id=self.calendar_id,
            company_id=self.company_id,
            location="Chennai",
            calendar_name="Calendar",
            calendar_year=2026,
            is_active=True,
        )
        holiday = HolidayResponse(
            id="507f1f77bcf86cd799439014",
            calendar_id=self.calendar_id,
            holiday_date=date(2026, 1, 26),
            holiday_name="Republic Day",
            is_active=True,
        )
        assignment = EmployeeHolidayCalendarResponse(
            id="507f1f77bcf86cd799439015",
            employee_id=self.employee_id,
            calendar_id=self.calendar_id,
            assigned_at=datetime.now(),
            is_active=True,
        )

        self.assertTrue(calendar.is_active)
        self.assertTrue(holiday.is_active)
        self.assertTrue(assignment.is_active)


if __name__ == "__main__":
    unittest.main()