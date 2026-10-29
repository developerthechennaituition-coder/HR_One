import unittest
from datetime import date, datetime
from unittest.mock import patch

from bson import ObjectId

from app import leave


class Cursor:
    def __init__(self, records):
        self.records = records

    def sort(self, *_args):
        return self

    def __iter__(self):
        return iter(self.records)


class LeaveRequestCollection:
    def __init__(self, record=None):
        self.record = dict(record) if record else None
        self.inserted_record = None

    def insert_one(self, record):
        self.inserted_record = dict(record)
        self.inserted_record["_id"] = ObjectId()
        return type("InsertResult", (), {"inserted_id": self.inserted_record["_id"]})()

    def find_one(self, _query):
        return dict(self.record) if self.record else None

    def find(self, _query):
        return Cursor([dict(self.record)] if self.record else [])

    def update_one(self, _query, update):
        self.record.update(update["$set"])
        return type("UpdateResult", (), {"matched_count": 1})()


class UserCollection:
    def __init__(self, users):
        self.users = users

    def find(self, query, _projection=None):
        if "reporting_manager_id" in query:
            manager_filter = query["reporting_manager_id"]["$in"]
            return Cursor([
                user for user in self.users
                if user.get("is_active") and user.get("reporting_manager_id") in manager_filter
            ])
        role_pattern = query["role"]["$regex"].strip("^$")
        return Cursor([
            user for user in self.users
            if user.get("is_active") and user.get("role", "").lower() == role_pattern.lower()
        ])

    def find_one(self, query, _projection=None):
        return next(
            (
                user for user in self.users
                if user["_id"] == query.get("_id") and user.get("is_active")
            ),
            None,
        )


class EmployeeDetailsCollection:
    def __init__(self, records):
        self.records = records

    def find(self, query, _projection=None):
        manager_filter = query["reporting_manager_id"]["$in"]
        return Cursor([
            record for record in self.records
            if record.get("reporting_manager_id") in manager_filter
        ])


class LeaveApprovalRoutingTests(unittest.TestCase):
    def test_leave_api_serializes_naive_mongo_datetimes_as_utc(self):
        result = leave._serialize({
            "_id": ObjectId(),
            "created_at": datetime(2026, 10, 3, 12, 0),
        })

        self.assertEqual(result["created_at"], "2026-10-03T12:00:00+00:00")

    def test_self_cancel_sends_leave_notification_to_applicant(self):
        employee_id = ObjectId()
        request_id = ObjectId()
        request = {
            "_id": request_id,
            "user_id": str(employee_id),
            "leave_type_id": str(ObjectId()),
            "from_date": "2026-10-05",
            "days": 1.0,
            "status": "pending",
        }
        with (
            patch.object(leave, "_current_user", return_value={"_id": employee_id, "name": "Employee"}),
            patch.object(leave, "leave_requests_collection", LeaveRequestCollection(request)),
            patch.object(leave, "_can_cancel", return_value=True),
            patch.object(leave, "_update_balance"),
            patch.object(leave, "_notify") as notify_employee,
            patch.object(leave, "_notify_approvers_of_cancellation") as notify_approvers,
            patch.object(leave, "_audit"),
            patch.object(leave, "_request_response", side_effect=lambda record, _viewer: record),
        ):
            result = leave.cancel_leave(str(request_id))

        self.assertEqual(result["status"], "cancelled")
        notify_employee.assert_called_once()
        self.assertEqual(notify_employee.call_args.args[0], str(employee_id))
        self.assertIn("cancelled by you", notify_employee.call_args.args[2])
        notify_approvers.assert_called_once_with(request, "pending", str(request_id))

    def test_applicant_cancellation_notifies_reporting_manager(self):
        employee_id = ObjectId()
        manager_id = ObjectId()
        users = UserCollection([
            {"_id": employee_id, "is_active": True, "name": "Employee", "employee_id": "EMP001"},
            {"_id": manager_id, "is_active": True},
        ])
        with (
            patch.object(leave, "users_collection", users),
            patch.object(leave, "_notify") as notify,
        ):
            leave._notify_approvers_of_cancellation(
                {
                    "user_id": str(employee_id),
                    "reporting_manager_id": str(manager_id),
                    "days": 1,
                    "from_date": "2026-10-05",
                },
                "pending",
                str(ObjectId()),
            )

        notify.assert_called_once()
        self.assertEqual(notify.call_args.args[0], str(manager_id))
        self.assertEqual(notify.call_args.args[1], "Leave Request Cancelled")
        self.assertIn("EMP001", notify.call_args.args[2])
        self.assertIn("Employee", notify.call_args.args[2])


if __name__ == "__main__":
    unittest.main()
