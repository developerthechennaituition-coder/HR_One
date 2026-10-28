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

    def test_manager_queue_finds_employees_assigned_in_employee_details(self):
        manager_id = ObjectId()
        employee_id = ObjectId()
        request = {
            "_id": ObjectId(),
            "user_id": str(employee_id),
            "reporting_manager_id": str(manager_id),
            "status": "pending",
            "approval_stage": "reporting_manager",
        }
        users = UserCollection([
            {"_id": manager_id, "is_active": True},
            {"_id": employee_id, "is_active": True},
        ])
        with (
            patch.object(leave, "_current_user", return_value={"_id": manager_id}),
            patch.object(leave, "users_collection", users),
            patch.object(
                leave,
                "employee_details_collection",
                EmployeeDetailsCollection([{
                    "user_id": str(employee_id),
                    "reporting_manager_id": str(manager_id),
                }]),
            ),
            patch.object(leave, "leave_requests_collection", LeaveRequestCollection(request)),
            patch.object(leave, "_request_response", side_effect=lambda record, _viewer: record),
        ):
            result = leave.pending_leave_requests()

        self.assertTrue(result["can_approve"])
        self.assertEqual(result["approval_role"], "reporting_manager")
        self.assertEqual(len(result["requests"]), 1)

    def test_legacy_hr_pending_request_is_returned_to_manager_stage(self):
        manager_id = ObjectId()
        employee_id = ObjectId()
        request_id = ObjectId()
        collection = LeaveRequestCollection({
            "_id": request_id,
            "user_id": str(employee_id),
            "status": "pending_hr_admin",
            "approval_stage": "hr_admin",
        })
        users = UserCollection([
            {"_id": manager_id, "is_active": True},
            {"_id": employee_id, "is_active": True, "name": "Employee"},
        ])
        with (
            patch.object(leave, "leave_requests_collection", collection),
            patch.object(leave, "users_collection", users),
            patch.object(leave, "_get_reporting_manager_id", return_value=str(manager_id)),
            patch.object(leave, "_notify") as notify_manager,
        ):
            leave._route_legacy_requests_to_reporting_manager()

        self.assertEqual(collection.record["status"], "pending")
        self.assertEqual(collection.record["approval_stage"], "reporting_manager")
        self.assertEqual(collection.record["reporting_manager_id"], str(manager_id))
        self.assertEqual(notify_manager.call_args.args[0], str(manager_id))

    def test_two_day_leave_requires_reporting_manager_then_hr_admin(self):
        manager_id = ObjectId()
        request_id = ObjectId()
        request = {
            "_id": request_id,
            "user_id": str(ObjectId()),
            "leave_type_id": str(ObjectId()),
            "from_date": "2026-10-05",
            "to_date": "2026-10-06",
            "days": 2.0,
            "status": "pending",
            "approval_stage": "reporting_manager",
        }
        collection = LeaveRequestCollection(request)
        with (
            patch.object(leave, "_current_user", return_value={"_id": manager_id, "name": "Manager"}),
            patch.object(leave, "leave_requests_collection", collection),
            patch.object(leave, "_is_reporting_manager_for", return_value=True),
            patch.object(leave, "_overlap_exists", return_value=False),
            patch.object(leave, "_notify_user_role") as notify_hr,
            patch.object(leave, "_audit"),
            patch.object(leave, "_request_response", side_effect=lambda record, _viewer: record),
        ):
            result = leave.approve_leave(str(request_id), leave.LeaveDecisionPayload())

        self.assertEqual(result["status"], "pending_hr_admin")
        self.assertEqual(result["approval_stage"], "hr_admin")
        notify_hr.assert_called_once()
        self.assertEqual(notify_hr.call_args.args[0], leave.HR_ADMIN_ROLE)

    def test_two_day_application_enters_reporting_manager_queue(self):
        employee_id = ObjectId()
        manager_id = ObjectId()
        request_collection = LeaveRequestCollection()
        employee = {"_id": employee_id, "name": "Employee", "is_active": True}
        with (
            patch.object(leave, "_current_user", return_value=employee),
            patch.object(leave, "_get_leave_type", return_value={"_id": ObjectId(), "name": "Annual Leave", "is_paid": True}),
            patch.object(leave, "_company_policy", return_value=leave.LeaveCompanyPolicy().model_dump()),
            patch.object(leave, "_is_medical_leave_type", return_value=False),
            patch.object(
                leave,
                "_working_dates",
                return_value=[date(2026, 10, 5), date(2026, 10, 6)],
            ),
            patch.object(leave, "_overlap_exists", return_value=False),
            patch.object(leave, "_ensure_balance", return_value={"used": 0, "pending": 0}),
            patch.object(leave, "_effective_entitled", return_value=10),
            patch.object(leave, "_get_policy", return_value=None),
            patch.object(leave, "_get_reporting_manager_id", return_value=str(manager_id)),
            patch.object(leave, "users_collection", UserCollection([
                employee,
                {"_id": manager_id, "is_active": True},
            ])),
            patch.object(leave, "leave_requests_collection", request_collection),
            patch.object(leave, "_update_balance"),
            patch.object(leave, "_notify_reporting_manager") as notify_manager,
            patch.object(leave, "_notify_user_role") as notify_hr,
            patch.object(leave, "_audit"),
            patch.object(leave, "_request_response", side_effect=lambda record, _viewer: record),
        ):
            result = leave.apply_leave(
                leave.LeaveApplicationCreate(
                    leave_type_id=str(ObjectId()),
                    from_date="2026-10-05",
                    to_date="2026-10-06",
                    reason="Personal leave",
                )
            )

        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["approval_stage"], "reporting_manager")
        self.assertEqual(result["days"], 2)
        notify_manager.assert_called_once()
        notify_hr.assert_not_called()

    def test_one_day_leave_completes_after_reporting_manager_approval(self):
        manager_id = ObjectId()
        request_id = ObjectId()
        employee_id = str(ObjectId())
        request = {
            "_id": request_id,
            "user_id": employee_id,
            "leave_type_id": str(ObjectId()),
            "from_date": "2026-10-05",
            "to_date": "2026-10-05",
            "days": 1.0,
            "status": "pending",
            "approval_stage": "reporting_manager",
        }
        collection = LeaveRequestCollection(request)
        with (
            patch.object(leave, "_current_user", return_value={"_id": manager_id, "name": "Manager"}),
            patch.object(leave, "leave_requests_collection", collection),
            patch.object(leave, "_is_reporting_manager_for", return_value=True),
            patch.object(leave, "_overlap_exists", return_value=False),
            patch.object(leave, "_update_balance"),
            patch.object(leave, "sync_leave_for_today"),
            patch.object(leave, "_notify") as notify_employee,
            patch.object(leave, "_audit"),
            patch.object(leave, "_request_response", side_effect=lambda record, _viewer: record),
        ):
            result = leave.approve_leave(str(request_id), leave.LeaveDecisionPayload())

        self.assertEqual(result["status"], "approved")
        self.assertEqual(result["approval_stage"], "completed")
        notify_employee.assert_called_once()
        self.assertEqual(notify_employee.call_args.args[0], employee_id)
        self.assertEqual(notify_employee.call_args.args[1], "Leave Approved")

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

    def test_cancel_during_hr_approval_notifies_manager_and_hr(self):
        employee_id = ObjectId()
        manager_id = ObjectId()
        hr_admin_id = ObjectId()
        users = UserCollection([
            {"_id": employee_id, "is_active": True, "name": "Employee", "employee_id": "EMP001"},
            {"_id": manager_id, "is_active": True, "role": "Manager"},
            {"_id": hr_admin_id, "is_active": True, "role": "HR Admin"},
        ])
        with (
            patch.object(leave, "users_collection", users),
            patch.object(leave, "_notify") as notify,
        ):
            leave._notify_approvers_of_cancellation(
                {
                    "user_id": str(employee_id),
                    "reporting_manager_id": str(manager_id),
                    "days": 2,
                    "from_date": "2026-10-05",
                },
                "pending_hr_admin",
                str(ObjectId()),
            )

        self.assertEqual(
            {call.args[0] for call in notify.call_args_list},
            {str(manager_id), str(hr_admin_id)},
        )
        for notification_call in notify.call_args_list:
            self.assertIn("EMP001", notification_call.args[2])
            self.assertIn("Employee", notification_call.args[2])

if __name__ == "__main__":
    unittest.main()
