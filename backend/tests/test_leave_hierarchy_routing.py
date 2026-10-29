"""Integration tests for HOD / Reporting Manager / HR Admin leave routing.

Run against a throwaway MongoDB database, for example:
    MONGODB_DATABASE=hrms_leave_hierarchy_test python -m unittest tests.test_leave_hierarchy_routing
The tests skip unless the database name ends with "_test", and drop it afterwards.
"""

import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

os.environ.setdefault("MONGODB_DATABASE", "hrms_leave_hierarchy_test")

from bson import ObjectId  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from pymongo.errors import PyMongoError  # noqa: E402

from app import core_routes, leave, reporting_manager as rm  # noqa: E402
from app.db.mongodb import client, db  # noqa: E402
from app.session import active_sessions  # noqa: E402

# Monday 4 January 2027 onward: future working days, no weekly offs configured.
DAY = ["2027-01-04", "2027-01-05", "2027-01-06", "2027-01-07", "2027-01-08"]


class LeaveHierarchyRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not db.name.endswith("_test"):
            raise unittest.SkipTest(f"Refusing to run against database {db.name!r}; use a *_test database.")
        try:
            client.admin.command("ping")
        except PyMongoError as exc:
            raise unittest.SkipTest(f"MongoDB is not reachable: {exc}")
        client.drop_database(db.name)

    @classmethod
    def tearDownClass(cls):
        client.drop_database(db.name)

    def setUp(self):
        for name in ["users", "employee_details", "leave_requests", "leave_types", "leave_policies",
                     "leave_balances", "leave_notifications", "notification_sequences", "audit_logs", "permission_requests"]:
            db[name].delete_many({})
        # Fixed "today" so the leave date window (today .. today + 89) stays valid.
        clock = patch.object(leave, "company_now", return_value=datetime(2027, 1, 1, 9, 0, tzinfo=timezone.utc))
        clock.start()
        self.addCleanup(clock.stop)
        self.employee = self.make_user("Asha", "Employee")
        self.manager = self.make_user("Mani", "Employee")       # becomes a Reporting Manager
        self.hod = self.make_user("Hari", "HR Manager")          # displayed as HOD
        self.hod2 = self.make_user("Hema", "HR Manager")
        self.hr_a = self.make_user("Arun", "HR Admin")
        self.hr_b = self.make_user("Bala", "HR Admin")
        self.assign(self.employee, reporting_manager_id=self.manager, hod_id=self.hod, hr_admin_id=self.hr_a)
        self.assign(self.manager, hod_id=self.hod, hr_admin_id=self.hr_b)
        self.assign(self.hr_a, hr_admin_id=self.hr_b)
        self.assign(self.hr_b, hr_admin_id=self.hr_a)
        self.leave_type = str(db["leave_types"].insert_one({"code": "CL", "name": "Casual Leave", "is_active": True, "is_paid": True}).inserted_id)
        db["leave_policies"].insert_one({"leave_type_id": self.leave_type, "annual_entitlement": 20, "is_active": True})

    # -- helpers ---------------------------------------------------------

    def make_user(self, name, role):
        user = {"_id": ObjectId(), "name": name, "employee_id": f"E-{name}", "role": role, "is_active": True,
                "created_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc)}
        db["users"].insert_one(user)
        token = f"token-{user['_id']}"
        active_sessions[token] = str(user["_id"])
        self.addCleanup(active_sessions.pop, token, None)
        user["auth"] = f"Bearer {token}"
        return user

    def assign(self, user, actor=None, **fields):
        payload = rm.ReportingManagerPayload(**{key: str(value["_id"]) if value else None for key, value in fields.items()})
        return rm.set_reporting_manager(str(user["_id"]), payload, (actor or self.hr_a)["auth"])

    def apply(self, user, days, start=0, day_part="full_day"):
        return leave.apply_leave(leave.LeaveApplicationCreate(
            leave_type_id=self.leave_type, from_date=DAY[start], to_date=DAY[start + days - 1],
            day_part=day_part, reason="Personal",
        ), user["auth"])

    def assert_http(self, status_code, func, *args):
        with self.assertRaises(HTTPException) as error:
            func(*args)
        self.assertEqual(error.exception.status_code, status_code, error.exception.detail)
        return error.exception.detail

    def notified(self, user):
        return [n["title"] for n in db["leave_notifications"].find({"recipient_user_id": str(user["_id"])})]

    # -- routing ---------------------------------------------------------

    def test_employee_routing_by_duration(self):
        one = self.apply(self.employee, 1, start=0)
        two = self.apply(self.employee, 2, start=1)
        db["leave_requests"].delete_many({})  # free the dates for the 3-day request
        three = self.apply(self.employee, 3, start=0)
        self.assertEqual((one["approval_stage"], one["approver_id"]), ("reporting_manager", str(self.manager["_id"])))
        self.assertEqual((two["approval_stage"], two["approver_id"]), ("hod", str(self.hod["_id"])))
        self.assertEqual((three["approval_stage"], three["approver_id"]), ("hr_admin", str(self.hr_a["_id"])))
        self.assertIn("New Leave Request", self.notified(self.hr_a))
        self.assertNotIn("New Leave Request", self.notified(self.hr_b))  # only the assigned HR Admin

    def test_half_day_counts_as_one_leave_day(self):
        request = self.apply(self.employee, 1, day_part="first_half")
        self.assertEqual((request["days"], request["approver_id"]), (0.5, str(self.manager["_id"])))

    def test_reporting_manager_routing(self):
        one = self.apply(self.manager, 1, start=0)
        two = self.apply(self.manager, 2, start=1)
        self.assertEqual((one["approval_stage"], one["approver_id"]), ("hod", str(self.hod["_id"])))
        self.assertEqual((two["approval_stage"], two["approver_id"]), ("hr_admin", str(self.hr_b["_id"])))
        db["leave_requests"].delete_many({})
        three = self.apply(self.manager, 3)
        self.assertEqual(three["approver_id"], str(self.hr_b["_id"]))
        # Never routed to the manager's own direct reports.
        self.assertEqual(self.notified(self.employee), [])
        self.assert_http(403, leave.approve_leave, three["id"], leave.LeaveDecisionPayload(), self.manager["auth"])

    def test_hr_admin_routing_and_missing_approver(self):
        for days, start in [(1, 0), (2, 1)]:
            request = self.apply(self.hr_a, days, start=start)
            self.assertEqual((request["approval_stage"], request["approver_id"]), ("hr_admin", str(self.hr_b["_id"])))
            self.assert_http(403, leave.approve_leave, request["id"], leave.LeaveDecisionPayload(), self.hr_a["auth"])
        self.assign(self.hr_a, actor=self.hr_b, hr_admin_id=None)
        detail = self.assert_http(400, self.apply, self.hr_a, 1, 4)
        self.assertIn("No HR Admin is assigned", detail)
        self.assertEqual(db["leave_requests"].count_documents({"user_id": str(self.hr_a["_id"])}), 2)

    def test_preview_matches_submission_routing(self):
        from datetime import date
        preview = leave.leave_approval_preview(date(2027, 1, 4), date(2027, 1, 5), "full_day", self.employee["auth"])
        self.assertEqual((preview["days"], preview["approval_stage"], preview["approver_name"], preview["problem"]), (2.0, "hod", "Hari", None))
        submitted = self.apply(self.employee, 2)
        self.assertEqual((submitted["approval_level"], submitted["assigned_approver_name"]), ("HOD", "Hari"))
        self.assign(self.employee, hod_id=None)
        problem = leave.leave_approval_preview(date(2027, 1, 6), date(2027, 1, 7), "full_day", self.employee["auth"])["problem"]
        self.assertIn("No HOD is assigned", problem)

    def test_leave_and_permission_date_windows(self):
        from datetime import date, timedelta
        from unittest.mock import patch
        today = date(2027, 1, 4)
        now = datetime(2027, 1, 4, 10, 0, tzinfo=timezone.utc)
        with patch.object(leave, "company_now", return_value=now):
            # Leave: today .. today + 89 only.
            past = leave.LeaveApplicationCreate(leave_type_id=self.leave_type, from_date=today - timedelta(days=1), to_date=today, reason="x")
            self.assertIn("past date", self.assert_http(400, leave.apply_leave, past, self.employee["auth"]))
            beyond = leave.LeaveApplicationCreate(leave_type_id=self.leave_type, from_date=date(2027, 4, 2), to_date=date(2027, 4, 5), reason="x")
            self.assertIn("2027-04-03", self.assert_http(400, leave.apply_leave, beyond, self.employee["auth"]))
            last = leave.apply_leave(leave.LeaveApplicationCreate(leave_type_id=self.leave_type, from_date=date(2027, 4, 2), to_date=date(2027, 4, 2), reason="x"), self.employee["auth"])
            self.assertEqual(last["to_date"], "2027-04-02")  # day 89 after today is allowed
            self.assertIn("past date", leave.leave_approval_preview(today - timedelta(days=1), today, "full_day", self.employee["auth"])["problem"])
            # Permission: today .. today + 14, approved only by the Reporting Manager.
            def permission(day):
                return leave.PermissionApplicationCreate(permission_date=day, start_time="10:00", end_time="11:00", reason="Bank")
            self.assert_http(422, leave.apply_permission, permission(today - timedelta(days=1)), self.employee["auth"])
            self.assert_http(422, leave.apply_permission, permission(today + timedelta(days=15)), self.employee["auth"])
            created = leave.apply_permission(permission(today + timedelta(days=14)), self.employee["auth"])
        self.assertEqual(created["reporting_manager_id"], str(self.manager["_id"]))
        self.assertEqual([r["id"] for r in leave.pending_permission_requests(self.manager["auth"])["requests"]], [created["id"]])
        for other in (self.hr_a, self.hod, self.employee):
            self.assertEqual(leave.pending_permission_requests(other["auth"])["requests"], [])
            self.assert_http(403, leave.approve_permission, created["id"], leave.LeaveDecisionPayload(), other["auth"])
        self.assertEqual(leave.approve_permission(created["id"], leave.LeaveDecisionPayload(), self.manager["auth"])["status"], "approved")

    # -- approval security ------------------------------------------------

    def test_only_the_assigned_approver_can_decide_once(self):
        request = self.apply(self.employee, 2)  # -> HOD
        for intruder in (self.employee, self.manager, self.hr_a, self.hod2):
            self.assert_http(403, leave.approve_leave, request["id"], leave.LeaveDecisionPayload(), intruder["auth"])
            self.assert_http(403, leave.reject_leave, request["id"], leave.LeaveDecisionPayload(), intruder["auth"])
        self.assertEqual(leave.pending_leave_requests(self.hr_a["auth"])["requests"], [])
        self.assertEqual([r["id"] for r in leave.pending_leave_requests(self.hod["auth"])["requests"]], [request["id"]])
        approved = leave.approve_leave(request["id"], leave.LeaveDecisionPayload(remarks="OK"), self.hod["auth"])
        self.assertEqual((approved["status"], approved["approved_by"], approved["approved_stage"]), ("approved", str(self.hod["_id"]), "hod"))
        self.assert_http(409, leave.approve_leave, request["id"], leave.LeaveDecisionPayload(), self.hod["auth"])
        self.assert_http(409, leave.reject_leave, request["id"], leave.LeaveDecisionPayload(), self.hod["auth"])
        self.assertIn("Leave Approved", self.notified(self.employee))
        balance = db["leave_balances"].find_one({"user_id": str(self.employee["_id"])})
        self.assertEqual((balance["used"], balance["pending"]), (2, 0))
        actions = [entry["action"] for entry in db["leave_requests"].find_one({"_id": ObjectId(request["id"])})["approval_history"]]
        self.assertEqual(actions, ["assigned", "approved"])

    def test_rejection_and_cancellation_notify_only_the_assigned_users(self):
        request = self.apply(self.employee, 1)
        rejected = leave.reject_leave(request["id"], leave.LeaveDecisionPayload(remarks="Busy week"), self.manager["auth"])
        self.assertEqual(rejected["status"], "rejected")
        self.assertIn("Leave Rejected", self.notified(self.employee))
        second = self.apply(self.employee, 3, start=1)  # -> HR Admin A
        leave.cancel_leave(second["id"], self.employee["auth"])
        self.assertIn("Leave Request Cancelled", self.notified(self.hr_a))
        self.assertNotIn("Leave Request Cancelled", self.notified(self.hr_b))

    # -- reassignment -----------------------------------------------------

    def test_assignment_change_reroutes_pending_but_not_completed(self):
        pending = self.apply(self.employee, 2, start=0)
        done = self.apply(self.employee, 2, start=2)
        leave.approve_leave(done["id"], leave.LeaveDecisionPayload(), self.hod["auth"])
        self.assign(self.employee, hod_id=self.hod2)
        moved = db["leave_requests"].find_one({"_id": ObjectId(pending["id"])})
        self.assertEqual(moved["approver_id"], str(self.hod2["_id"]))
        self.assertEqual(moved["approval_history"][-1]["previous_approver_id"], str(self.hod["_id"]))
        self.assertIn("Leave Request Assigned for Approval", self.notified(self.hod2))
        self.assertEqual(db["leave_requests"].find_one({"_id": ObjectId(done["id"])})["approved_by"], str(self.hod["_id"]))
        self.assert_http(403, leave.approve_leave, pending["id"], leave.LeaveDecisionPayload(), self.hod["auth"])
        # Removing the HOD leaves the request pending without an approver (never auto-decided).
        self.assign(self.employee, hod_id=None)
        orphan = db["leave_requests"].find_one({"_id": ObjectId(pending["id"])})
        self.assertEqual((orphan["status"], orphan["approver_id"]), ("pending", None))
        self.assertIn("No HOD is assigned", orphan["routing_problem"])
        # Deactivating an approver moves their pending work once a valid one exists.
        self.assign(self.employee, hod_id=self.hod)
        core_routes.update_user_status(str(self.hod["_id"]), core_routes.UserActivationPayload(is_active=False))
        self.assertIsNone(db["leave_requests"].find_one({"_id": ObjectId(pending["id"])})["approver_id"])

    def test_legacy_pending_requests_are_reconciled(self):
        legacy = db["leave_requests"].insert_one({
            "user_id": str(self.employee["_id"]), "leave_type_id": self.leave_type, "from_date": DAY[0], "to_date": DAY[2],
            "days": 3.0, "status": "pending_hr_admin", "approval_stage": "hr_admin", "reporting_manager_approved_by": str(self.manager["_id"]),
        }).inserted_id
        leave._reconcile_pending_leave_routing()
        record = db["leave_requests"].find_one({"_id": legacy})
        self.assertEqual((record["status"], record["approval_stage"], record["approver_id"]), ("pending", "hr_admin", str(self.hr_a["_id"])))
        self.assertEqual(record["reporting_manager_approved_by"], str(self.manager["_id"]))  # history kept

    # -- assignments and role ------------------------------------------------

    def test_assignment_validation_and_permissions(self):
        cases = [
            ({"hod_id": self.hr_a}, 400),          # not an HOD
            ({"hr_admin_id": self.hod}, 400),      # not an HR Admin
            ({"hr_admin_id": self.employee}, 400),  # not an HR Admin
        ]
        for fields, code in cases:
            with self.assertRaises(HTTPException) as error:
                self.assign(self.employee, **fields)
            self.assertEqual(error.exception.status_code, code, fields)
        with self.assertRaises(HTTPException) as error:
            self.assign(self.hr_a, hr_admin_id=self.hr_a)
        self.assertEqual(error.exception.status_code, 400)  # self-approval
        db["users"].update_one({"_id": self.hod2["_id"]}, {"$set": {"is_active": False}})
        with self.assertRaises(HTTPException) as error:
            self.assign(self.employee, hod_id=self.hod2)
        self.assertEqual(error.exception.status_code, 404)  # inactive
        for actor in (self.employee, self.hod, self.manager):  # only HR Admin can assign
            with self.assertRaises(HTTPException) as error:
                self.assign(self.employee, actor=actor, hod_id=self.hod)
            self.assertEqual(error.exception.status_code, 403)
        # Sending only the HOD keeps the existing Reporting Manager.
        result = self.assign(self.employee, hod_id=self.hod)
        self.assertEqual(result["reporting_manager_id"], str(self.manager["_id"]))
        details = rm.get_reporting_manager(str(self.employee["_id"]), self.hr_a["auth"])
        self.assertEqual((details["hod_id"], details["hr_admin_id"], details["hod"]["name"]), (str(self.hod["_id"]), str(self.hr_a["_id"]), "Hari"))

    def test_hod_is_a_display_name_for_the_stored_hr_manager_role(self):
        payload = core_routes.UserPayload(name="New Head", work_email="head@example.com", mobile="9000000009",
                                          country="India", country_code="+91", role="HOD")
        self.assertEqual(payload.role, "HR Manager")
        self.assertIn("hr manager", leave.APPROVER_ROLES)  # HOD keeps the former HR Manager access
