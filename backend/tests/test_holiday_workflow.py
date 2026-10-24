import unittest
from datetime import date, timedelta
from unittest.mock import patch

from bson import ObjectId
from fastapi import HTTPException
from pydantic import ValidationError

from app.main import (
    EmployeeHolidayCalendarPayload,
    HolidayCalendarPayload,
    HolidayPayload,
    assign_employee_holiday_calendar,
    create_holiday,
    create_holiday_calendar,
    deactivate_holiday,
    get_employee_holiday_calendar,
    list_calendar_holidays,
    update_holiday,
)


class InsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class UpdateResult:
    def __init__(self, matched_count):
        self.matched_count = matched_count


class MemoryCursor:
    def __init__(self, records):
        self.records = records

    def sort(self, field, direction):
        self.records.sort(
            key=lambda record: record.get(field), reverse=direction < 0
        )
        return self

    def __iter__(self):
        return iter(self.records)


class MemoryCollection:
    def __init__(self, records=None):
        self.records = {record["_id"]: dict(record) for record in records or []}

    def insert_one(self, record):
        stored = dict(record)
        stored["_id"] = ObjectId()
        self.records[stored["_id"]] = stored
        return InsertResult(stored["_id"])

    def find_one(self, query):
        for record in self.records.values():
            if all(record.get(key) == value for key, value in query.items()):
                return dict(record)
        return None

    def find(self, query):
        records = [
            dict(record)
            for record in self.records.values()
            if all(record.get(key) == value for key, value in query.items())
        ]
        return MemoryCursor(records)

    def update_one(self, query, update):
        for record_id, record in self.records.items():
            if all(record.get(key) == value for key, value in query.items()):
                self.records[record_id].update(update.get("$set", {}))
                return UpdateResult(1)
        return UpdateResult(0)


class HolidayWorkflowTests(unittest.TestCase):
    company_id = "507f1f77bcf86cd799439011"

    def setUp(self):
        self.holiday_date = date.today() + timedelta(days=10)
        self.calendar_year = self.holiday_date.year
        employee_one_id = ObjectId("507f1f77bcf86cd799439012")
        employee_two_id = ObjectId("507f1f77bcf86cd799439013")
        self.employee_one_id = str(employee_one_id)
        self.employee_two_id = str(employee_two_id)
        self.companies = MemoryCollection([
            {
                "_id": ObjectId(self.company_id),
                "company": "Acme HR Services",
                "location": "Chennai",
                "is_active": True,
            }
        ])
        self.employees = MemoryCollection([
            {"_id": employee_one_id, "employee_name": "Anand Kumar", "is_active": True},
            {"_id": employee_two_id, "employee_name": "Divya Rao", "is_active": True},
        ])
        self.calendars = MemoryCollection()
        self.holidays = MemoryCollection()
        self.assignments = MemoryCollection()
        self.patch = patch.multiple(
            "app.main",
            companies_collection=self.companies,
            employees_collection=self.employees,
            holiday_calendars_collection=self.calendars,
            holidays_collection=self.holidays,
            employee_holiday_calendars_collection=self.assignments,
            _record_organization_audit=lambda *args, **kwargs: None,
        )
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_calendar_holiday_edit_remove_and_employee_retrieval_workflow(self):
        calendar = create_holiday_calendar(
            HolidayCalendarPayload(
                company_id=self.company_id,
                location="Chennai",
                calendar_name=f"India Holidays {self.calendar_year}",
                calendar_year=self.calendar_year,
            )
        )
        calendar_id = calendar["id"]

        republic_day = create_holiday(
            calendar_id,
            HolidayPayload(
                calendar_id=calendar_id,
                holiday_date=self.holiday_date,
                holiday_name="Republic Day",
                holiday_type="Public",
            ),
        )
        independence_day = create_holiday(
            calendar_id,
            HolidayPayload(
                calendar_id=calendar_id,
                holiday_date=self.holiday_date + timedelta(days=1),
                holiday_name="Independence Day",
                holiday_type="Public",
            ),
        )
        persisted = list_calendar_holidays(calendar_id)
        self.assertEqual(len(persisted), 2)
        self.assertEqual(persisted[0]["holiday_date"], self.holiday_date)
        self.assertEqual(persisted[1]["holiday_name"], "Independence Day")

        updated = update_holiday(
            republic_day["id"],
            HolidayPayload(
                calendar_id=calendar_id,
                holiday_date=self.holiday_date + timedelta(days=2),
                holiday_name="Republic Day Observed",
                holiday_type="Company",
                description="Observed holiday",
            ),
        )
        self.assertEqual(updated["holiday_date"], self.holiday_date + timedelta(days=2))
        self.assertEqual(updated["holiday_name"], "Republic Day Observed")

        removal = deactivate_holiday(independence_day["id"])
        self.assertEqual(removal["id"], independence_day["id"])
        remaining = list_calendar_holidays(calendar_id)
        self.assertEqual(len(remaining), 2)
        surviving = next(item for item in remaining if item["status"] != "Cancelled")
        cancelled = next(item for item in remaining if item["status"] == "Cancelled")
        self.assertEqual(surviving["holiday_name"], "Republic Day Observed")
        self.assertEqual(cancelled["holiday_name"], "Independence Day")

        assign_employee_holiday_calendar(
            EmployeeHolidayCalendarPayload(
                employee_id=self.employee_one_id,
                calendar_id=calendar_id,
            )
        )
        assign_employee_holiday_calendar(
            EmployeeHolidayCalendarPayload(
                employee_id=self.employee_two_id,
                calendar_id=calendar_id,
            )
        )
        employee_calendar = get_employee_holiday_calendar(self.employee_one_id)
        self.assertEqual(employee_calendar["id"], calendar_id)
        self.assertEqual(employee_calendar["location"], "Chennai")

    def test_invalid_inputs_do_not_create_records(self):
        with self.assertRaises(HTTPException) as invalid_scope:
            create_holiday_calendar(
                HolidayCalendarPayload(
                    company_id=self.company_id,
                    location="Bengaluru",
                    calendar_name="Invalid Scope",
                    calendar_year=self.calendar_year,
                )
            )
        self.assertEqual(invalid_scope.exception.status_code, 400)
        self.assertEqual(self.calendars.records, {})

        calendar = create_holiday_calendar(
            HolidayCalendarPayload(
                company_id=self.company_id,
                location="Chennai",
                calendar_name=f"India Holidays {self.calendar_year}",
                calendar_year=self.calendar_year,
            )
        )
        with self.assertRaises(HTTPException) as invalid_date:
            create_holiday(
                calendar["id"],
                HolidayPayload(
                    calendar_id=calendar["id"],
                    holiday_date=date(self.calendar_year + 1, 1, 1),
                    holiday_name="Wrong Year",
                ),
            )
        self.assertEqual(invalid_date.exception.status_code, 400)
        self.assertEqual(self.holidays.records, {})

        with self.assertRaises(ValidationError):
            HolidayPayload(
                calendar_id=calendar["id"],
                holiday_date=self.holiday_date,
                holiday_name=" ",
            )

        with self.assertRaises(HTTPException) as missing_employee:
            assign_employee_holiday_calendar(
                EmployeeHolidayCalendarPayload(
                    employee_id=str(ObjectId()),
                    calendar_id=calendar["id"],
                )
            )
        self.assertEqual(missing_employee.exception.status_code, 404)
        self.assertEqual(self.assignments.records, {})

    def test_invalid_calendar_or_employee_ids_return_client_errors(self):
        with self.assertRaises(HTTPException) as invalid_calendar:
            list_calendar_holidays("not-an-object-id")
        self.assertEqual(invalid_calendar.exception.status_code, 400)

        with self.assertRaises(HTTPException) as invalid_employee:
            get_employee_holiday_calendar("not-an-object-id")
        self.assertEqual(invalid_employee.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()