import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from bson import ObjectId
from fastapi import HTTPException

from app.main import (
    CompanyPayload,
    EmployeeHolidayCalendarPayload,
    HolidayCalendarPayload,
    HolidayPayload,
    app,
    assign_employee_holiday_calendar,
    create_holiday,
    create_company,
    validate_holiday_date,
    _emit_holiday_calendar_reminders,
    _organization_admin,
)


class InsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class HolidayApiTests(unittest.TestCase):
    def test_f005_routes_are_registered(self):
        routes = {
            (route.path, method)
            for route in app.routes
            for method in (getattr(route, "methods", None) or set())
        }
        expected_routes = {
            ("/api/holiday-calendars", "GET"),
            ("/api/holiday-calendars", "POST"),
            ("/api/holiday-calendars/{calendar_id}", "GET"),
            ("/api/holiday-calendars/{calendar_id}", "PUT"),
            ("/api/holiday-calendars/{calendar_id}", "DELETE"),
            ("/api/holiday-calendars/{calendar_id}/holidays", "GET"),
            ("/api/holiday-calendars/{calendar_id}/holidays", "POST"),
            ("/api/holidays/{holiday_id}", "PUT"),
            ("/api/holidays/{holiday_id}", "DELETE"),
            ("/api/employee-holiday-calendars", "GET"),
            ("/api/employee-holiday-calendars", "POST"),
            ("/api/employees/{employee_id}/holiday-calendar", "GET"),
        }
        self.assertTrue(expected_routes.issubset(routes))

    @patch("app.main.holidays_collection")
    @patch("app.main.get_holiday_calendar_reference")
    def test_holiday_date_must_match_calendar_year(self, get_calendar, holidays):
        calendar_id = str(ObjectId())
        get_calendar.return_value = {"calendar_year": 2026}
        payload = HolidayPayload(
            calendar_id=calendar_id,
            holiday_date=date(2027, 1, 1),
            holiday_name="New Year",
        )

        with self.assertRaises(HTTPException) as context:
            create_holiday(calendar_id, payload)

        self.assertEqual(context.exception.status_code, 400)
        holidays.insert_one.assert_not_called()

    @patch("app.main.employee_holiday_calendars_collection")
    @patch("app.main.get_holiday_calendar_reference")
    @patch("app.main.validate_employee_reference")
    def test_employee_assignment_rejects_duplicate_active_assignment(
        self, validate_employee, get_calendar, assignments
    ):
        assignments.find_one.return_value = {"_id": ObjectId()}
        payload = EmployeeHolidayCalendarPayload(
            employee_id=str(ObjectId()),
            calendar_id=str(ObjectId()),
        )

        with self.assertRaises(HTTPException) as context:
            assign_employee_holiday_calendar(payload)

        self.assertEqual(context.exception.status_code, 409)
        assignments.insert_one.assert_not_called()

    def test_calendar_payload_contains_required_context(self):
        payload = HolidayCalendarPayload(
            company_id=str(ObjectId()),
            location="Chennai",
            calendar_name="2026 Public Holidays",
            calendar_year=2026,
        )
        self.assertEqual(payload.location, "Chennai")
        self.assertEqual(payload.calendar_year, 2026)

    @patch("app.main.companies_collection")
    def test_company_creation_rejects_an_existing_company(self, companies):
        companies.count_documents.return_value = 1
        payload = CompanyPayload(
            company="Example",
            legal_entity="Example LLC",
            branch="Head Office",
            location="Chennai",
        )
        with self.assertRaises(HTTPException) as context:
            create_company(payload)
        self.assertEqual(context.exception.status_code, 409)
        companies.insert_one.assert_not_called()

    def test_organization_mutations_require_an_authorized_role(self):
        with self.assertRaises(HTTPException) as context:
            _organization_admin({"role": "Employee"})
        self.assertEqual(context.exception.status_code, 403)
        self.assertEqual(_organization_admin({"role": "HR Admin"})["role"], "HR Admin")

    def test_holiday_dates_in_the_past_are_rejected(self):
        yesterday = date.today() - timedelta(days=1)
        with self.assertRaises(HTTPException) as context:
            validate_holiday_date({"calendar_year": yesterday.year}, yesterday)
        self.assertEqual(context.exception.status_code, 400)

    @patch("app.main.holiday_calendar_reminders_collection")
    @patch("app.main.holiday_calendars_collection")
    @patch("app.main.create_system_notification")
    def test_calendar_reminder_targets_next_year_hr_admins(
        self, create_notification, calendars, reminders
    ):
        calendars.find_one.return_value = None
        reminders.find_one.return_value = None
        create_notification.return_value = "notification-id"
        _emit_holiday_calendar_reminders(datetime(2026, 12, 1, tzinfo=timezone.utc))
        call = create_notification.call_args.kwargs
        self.assertIn("2027", call["title"])
        self.assertEqual(call["validity"], timedelta(days=15))
        self.assertEqual(call["roles"], {"HR Admin", "hr admin"})
        reminders.insert_one.assert_called_once()


if __name__ == "__main__":
    unittest.main()