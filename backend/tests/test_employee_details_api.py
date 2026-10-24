import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from bson import ObjectId

from app.employee_details import (
    EmployeeExtraDetailsPayload,
    save_employee_details,
    serialize_datetime,
    serialize_user,
)
from app.reporting_manager import (
    _serialize_team_member,
    get_team_member_attendance,
)


class EmployeeDetailsApiTests(unittest.TestCase):
    def setUp(self):
        self.user_id = ObjectId("507f1f77bcf86cd799439011")

    def test_employee_master_fields_are_saved(self):
        payload = EmployeeExtraDetailsPayload(
            gender="Female",
            marital_status="Married",
            department="Engineering",
            designation="Developer",
            city="Chennai",
            state="Tamil Nadu",
            country="India",
        )
        user = {"_id": self.user_id, "role": "Employee"}
        saved_details = {
            "gender": "Female",
            "marital_status": "Married",
            "department": "Engineering",
            "designation": "Developer",
            "city": "Chennai",
            "state": "Tamil Nadu",
            "country": "India",
        }

        with (
            patch("app.employee_details.get_authenticated_user", return_value={"role": "HR Admin"}),
            patch("app.employee_details.get_user_by_id", return_value=user),
            patch("app.employee_details.employee_details_collection") as collection,
        ):
            collection.find_one.return_value = saved_details
            response = save_employee_details(str(self.user_id), payload)

        saved_values = collection.update_one.call_args.args[1]["$set"]
        for field, expected in saved_details.items():
            self.assertEqual(saved_values[field], expected)
            self.assertEqual(response["details"][field], expected)

    def test_user_account_timestamps_keep_time_and_login_count(self):
        created_at = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
        last_login = datetime(2026, 10, 2, 0, 0, tzinfo=timezone.utc)

        serialized = serialize_user({
            "_id": self.user_id,
            "created_at": created_at,
            "last_login": last_login,
            "login_count": 4,
        })

        self.assertEqual(serialized["created_at"], created_at.isoformat())
        self.assertEqual(serialized["last_login"], last_login.isoformat())
        self.assertEqual(serialized["login_count"], 4)
        self.assertEqual(
            serialize_datetime(datetime(2026, 10, 2, 11, 0)),
            "2026-10-02T11:00:00+00:00",
        )

    def test_team_member_serializer_includes_master_fields_and_manager_name(self):
        serialized = _serialize_team_member(
            {
                "_id": self.user_id,
                "name": "Asha",
                "reporting_manager_id": str(self.user_id),
                "country": "India",
                "country_code": "+91",
            },
            {
                "gender": "Female",
                "marital_status": "Single",
                "department": "Engineering",
                "designation": "Developer",
                "city": "Chennai",
                "state": "Tamil Nadu",
                "country": "India",
            },
            {
                "_id": self.user_id,
                "name": "Manager Name",
            },
        )

        self.assertEqual(serialized["gender"], "Female")
        self.assertEqual(serialized["department"], "Engineering")
        self.assertEqual(serialized["designation"], "Developer")
        self.assertEqual(serialized["city"], "Chennai")
        self.assertEqual(serialized["state"], "Tamil Nadu")
        self.assertEqual(serialized["country_code"], "+91")
        self.assertEqual(
            serialized["reporting_manager"]["name"],
            "Manager Name",
        )

    def test_team_attendance_includes_approved_leaves_for_month(self):
        leave_type_id = ObjectId("507f1f77bcf86cd799439012")
        leave = {
            "_id": ObjectId("507f1f77bcf86cd799439013"),
            "user_id": str(self.user_id),
            "leave_type_id": str(leave_type_id),
            "from_date": "2026-10-30",
            "to_date": "2026-11-02",
            "day_part": "full_day",
            "status": "approved",
        }

        with (
            patch("app.reporting_manager._current_user", return_value={"_id": ObjectId("507f1f77bcf86cd799439014")}),
            patch("app.reporting_manager._require_reporting_manager"),
            patch("app.reporting_manager.users_collection") as users,
            patch("app.reporting_manager.attendance_collection") as attendance,
            patch("app.reporting_manager.leave_requests_collection") as requests,
            patch("app.reporting_manager.leave_types_collection") as leave_types,
        ):
            users.find_one.return_value = {"_id": self.user_id, "name": "Asha"}
            attendance.find.return_value.sort.return_value = []
            requests.find.return_value.sort.return_value = [leave]
            leave_types.find_one.return_value = {
                "code": "AL",
                "name": "Annual Leave",
            }

            response = get_team_member_attendance(
                str(self.user_id),
                year=2026,
                month=10,
                authorization="Bearer test-token",
            )

        self.assertEqual(len(response["approved_leaves"]), 1)
        self.assertEqual(response["approved_leaves"][0]["leave_type_code"], "AL")
        self.assertEqual(
            requests.find.call_args.args[0]["from_date"],
            {"$lt": "2026-11-01"},
        )


if __name__ == "__main__":
    unittest.main()