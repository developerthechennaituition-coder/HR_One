import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from bson import ObjectId
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from app import attendance, leave
from app.attendance import COMPANY_TZ, WFH_LATE_CHECK_IN_REQUEST_TYPE


def at(hour, minute=0):
    return datetime(2026, 10, 27, hour, minute, tzinfo=COMPANY_TZ)


class WfhLateCheckInPermissionTests(unittest.TestCase):
    def setUp(self):
        self.manager_id = ObjectId()
        self.user = {
            "_id": ObjectId(),
            "name": "Asha Kumar",
            "employee_id": "E-101",
            "department": "Finance",
            "reporting_manager_id": str(self.manager_id),
            "wfh_permitted_from": "2026-10-01",
            "wfh_permitted_to": "2026-10-31",
            "wfh_permitted_start_time": "09:00",
            "wfh_permitted_end_time": "18:00",
        }
        self.manager = {"_id": self.manager_id, "name": "Ravi Manager", "is_active": True}
        for target, name, value in [
            (attendance, "_today_wfh_attendance", None),
            (attendance, "_approved_leaves_for_date", []),
        ]:
            patcher = patch.object(target, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.requests = MagicMock()
        self.requests.find_one.return_value = None
        self.requests.insert_one.return_value = MagicMock(inserted_id=ObjectId())
        self.users = MagicMock()
        self.users.find_one.return_value = self.manager
        self.notify = MagicMock()
        self.audit = MagicMock()
        for target, name, value in [
            (attendance, "permission_requests_collection", self.requests),
            (attendance, "users_collection", self.users),
            (attendance, "db", MagicMock()),
            (leave, "_permission_notify", self.notify),
            (leave, "_audit", self.audit),
        ]:
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def check_in_at(self, moment):
        with patch.object(attendance, "company_now", return_value=moment):
            attendance._assert_wfh_action_allowed(self.user, "check_in")

    def assert_blocked(self, moment, status_code=403):
        with self.assertRaises(HTTPException) as error:
            self.check_in_at(moment)
        self.assertEqual(error.exception.status_code, status_code)
        return error.exception.detail

    def test_check_in_within_normal_window_is_unchanged(self):
        self.check_in_at(at(9, 30))
        self.check_in_at(at(10, 0))
        self.requests.find_one.assert_not_called()
        self.requests.insert_one.assert_not_called()
        self.notify.assert_not_called()

    def test_early_check_in_rule_is_preserved(self):
        detail = self.assert_blocked(at(8, 30))
        self.assertIn("opens 15 minutes before", detail)
        self.requests.insert_one.assert_not_called()

    def test_first_late_attempt_creates_request_and_notifies_reporting_manager(self):
        detail = self.assert_blocked(at(10, 25))

        self.assertIn("sent to your reporting manager", detail)
        self.assertIn("12:00", detail)
        record = self.requests.insert_one.call_args.args[0]
        self.assertEqual(record["request_type"], WFH_LATE_CHECK_IN_REQUEST_TYPE)
        self.assertEqual(record["user_id"], str(self.user["_id"]))
        self.assertEqual(record["reporting_manager_id"], str(self.manager_id))
        self.assertEqual(record["permission_date"], "2026-10-27")
        self.assertEqual((record["start_time"], record["end_time"]), ("10:00", "12:00"))
        self.assertEqual(record["status"], "pending")

        self.notify.assert_called_once()
        recipient, title, message, request_id = self.notify.call_args.args
        self.assertEqual(recipient, str(self.manager_id))
        self.assertEqual(title, "WFH Late Check-In Approval Required")
        for expected in ["Asha Kumar (E-101)", "Finance", "2026-10-27", "08:45–10:00", "10:25", "2h 00m", "until 12:00", "Permission Approval Queue"]:
            self.assertIn(expected, message)
        self.assertEqual(request_id, str(self.requests.insert_one.return_value.inserted_id))

    def test_repeated_attempt_with_pending_request_does_not_duplicate(self):
        self.requests.find_one.return_value = {"_id": ObjectId(), "status": "pending", "end_time": "12:00"}

        detail = self.assert_blocked(at(10, 40))

        self.assertIn("awaiting reporting-manager approval", detail)
        self.requests.insert_one.assert_not_called()
        self.notify.assert_not_called()

    def test_concurrent_attempt_hitting_unique_index_does_not_notify_twice(self):
        self.requests.insert_one.side_effect = DuplicateKeyError("duplicate")

        detail = self.assert_blocked(at(10, 25))

        self.assertIn("sent to your reporting manager", detail)
        self.notify.assert_not_called()

    def test_approved_request_allows_check_in_until_extended_deadline(self):
        self.requests.find_one.return_value = {"_id": ObjectId(), "status": "approved", "end_time": "12:00"}

        self.check_in_at(at(11, 59))
        self.check_in_at(at(12, 0))
        detail = self.assert_blocked(at(12, 1))

        self.assertIn("expired at 12:00", detail)
        self.requests.insert_one.assert_not_called()

    def test_rejected_request_keeps_check_in_blocked(self):
        self.requests.find_one.return_value = {"_id": ObjectId(), "status": "rejected", "end_time": "12:00"}

        detail = self.assert_blocked(at(10, 30))

        self.assertIn("rejected", detail)
        self.requests.insert_one.assert_not_called()

    def test_approval_for_another_date_is_not_accepted(self):
        def find_one(query):
            if query.get("permission_date") == "2026-10-26":
                return {"_id": ObjectId(), "status": "approved", "end_time": "12:00"}
            return None

        self.requests.find_one.side_effect = find_one

        self.assert_blocked(at(10, 30))

        lookup = self.requests.find_one.call_args.args[0]
        self.assertEqual(lookup["permission_date"], "2026-10-27")
        self.assertEqual(lookup["user_id"], str(self.user["_id"]))
        self.assertEqual(lookup["request_type"], WFH_LATE_CHECK_IN_REQUEST_TYPE)

    def test_missing_reporting_manager_is_reported_without_creating_request(self):
        self.user.pop("reporting_manager_id")
        with patch.object(leave, "_get_reporting_manager_id", return_value=None):
            detail = self.assert_blocked(at(10, 30), status_code=422)

        self.assertIn("no active reporting manager", detail)
        self.requests.insert_one.assert_not_called()
        self.notify.assert_not_called()

    def test_inactive_reporting_manager_is_treated_as_missing(self):
        self.users.find_one.return_value = None

        self.assert_blocked(at(10, 30), status_code=422)

        self.requests.insert_one.assert_not_called()

    def test_notification_failure_removes_request_and_reports_error(self):
        self.notify.side_effect = RuntimeError("database unavailable")

        detail = self.assert_blocked(at(10, 30), status_code=503)

        self.assertIn("could not be sent", detail)
        self.requests.delete_one.assert_called_once_with(
            {"_id": self.requests.insert_one.return_value.inserted_id, "status": "pending"}
        )
        self.audit.assert_not_called()

    def test_attempt_after_maximum_extension_does_not_create_request(self):
        detail = self.assert_blocked(at(12, 30))

        self.assertIn("permission period ended at 12:00", detail)
        self.requests.insert_one.assert_not_called()

    def test_already_checked_in_employee_gets_existing_error_not_a_request(self):
        with patch.object(
            attendance,
            "_today_wfh_attendance",
            return_value={"attendance_source": "WFH_FACE_GPS", "check_in_time": "09:10"},
        ):
            detail = self.assert_blocked(at(10, 30), status_code=409)

        self.assertIn("already checked in", detail)
        self.requests.insert_one.assert_not_called()

    def test_extension_is_from_configured_deadline_and_capped_at_scheduled_end(self):
        self.user["wfh_permitted_start_time"] = "13:30"
        self.user["wfh_permitted_end_time"] = "22:00"
        _, normal, extended = attendance._wfh_check_in_deadlines(self.user, at(15, 0))
        self.assertEqual((normal.strftime("%H:%M"), extended.strftime("%H:%M")), ("14:30", "16:30"))

        self.user["wfh_permitted_end_time"] = "15:30"
        _, _, capped = attendance._wfh_check_in_deadlines(self.user, at(15, 0))
        self.assertEqual(capped.strftime("%H:%M"), "15:30")

    def test_status_reports_pending_request_for_employee_page(self):
        self.requests.find_one.return_value = {"_id": ObjectId(), "status": "pending", "end_time": "12:00"}
        with patch.object(attendance, "company_now", return_value=at(10, 30)):
            state = attendance._wfh_late_check_in_status(self.user)

        self.assertEqual(state["status"], "pending")
        self.assertEqual((state["normal_deadline"], state["extended_deadline"]), ("10:00", "12:00"))
        self.assertTrue(state["is_late"])
        self.assertFalse(state["is_expired"])


class WfhLateCheckInDecisionTests(unittest.TestCase):
    def setUp(self):
        self.manager_id = ObjectId()
        self.request_id = ObjectId()
        self.record = {
            "_id": self.request_id,
            "user_id": str(ObjectId()),
            "reporting_manager_id": str(self.manager_id),
            "request_type": WFH_LATE_CHECK_IN_REQUEST_TYPE,
            "permission_date": "2026-10-27",
            "start_time": "10:00",
            "end_time": "12:00",
            "reason": "WFH late check-in attempted at 10:25.",
            "status": "pending",
        }
        self.requests = MagicMock()
        self.requests.find_one.return_value = self.record
        for name, value in [
            ("permission_requests_collection", self.requests),
            ("users_collection", MagicMock()),
            ("db", MagicMock()),
            ("_permission_notify", MagicMock()),
            ("_audit", MagicMock()),
        ]:
            patcher = patch.object(leave, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_unrelated_manager_cannot_approve(self):
        other_manager = {"_id": ObjectId(), "role": "Manager"}
        with self.assertRaises(HTTPException) as error:
            leave._decide_permission(str(self.request_id), True, other_manager, None)

        self.assertEqual(error.exception.status_code, 403)
        self.requests.update_one.assert_not_called()

    def test_assigned_manager_approval_records_approver_and_timestamp(self):
        self.requests.update_one.return_value = MagicMock(modified_count=1)

        leave._decide_permission(str(self.request_id), True, {"_id": self.manager_id, "role": "Manager"}, None)

        query, update = self.requests.update_one.call_args.args
        self.assertEqual(query, {"_id": self.request_id, "status": "pending"})
        self.assertEqual(update["$set"]["status"], "approved")
        self.assertEqual(update["$set"]["decided_by"], str(self.manager_id))
        self.assertIn("decided_at", update["$set"])

    def test_concurrent_or_repeated_decision_is_rejected(self):
        self.requests.update_one.return_value = MagicMock(modified_count=0)
        with self.assertRaises(HTTPException) as error:
            leave._decide_permission(str(self.request_id), True, {"_id": self.manager_id, "role": "Manager"}, None)
        self.assertEqual(error.exception.status_code, 409)

        self.record["status"] = "approved"
        with self.assertRaises(HTTPException) as error:
            leave._decide_permission(str(self.request_id), False, {"_id": self.manager_id, "role": "Manager"}, None)
        self.assertEqual(error.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
