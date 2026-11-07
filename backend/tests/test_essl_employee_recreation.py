"""ESSL file import after an employee is deleted and re-created with the same Employee ID / ESSL ID.

Regression: deleting an employee leaves that employee's raw punches in ``essl_raw_punches``
under the old ObjectId. A raw punch is keyed by ESSL ID + timestamp, so re-importing the same
ESSL ID matched the stale rows and left them owned by the deleted user; the new employee's
punches could not be reloaded and every day was reported as EMPTY_PUNCHES.

Runs against MongoDB like the other integration tests; skips unless the database name ends
with "_test":
    MONGODB_DATABASE=hrms_essl_recreate_test python -m unittest tests.test_essl_employee_recreation
"""

import asyncio
import json
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

os.environ.setdefault("MONGODB_DATABASE", "hrms_essl_recreate_test")

from bson import ObjectId  # noqa: E402

from app import attendance as att  # noqa: E402
from app import essl  # noqa: E402
from app.db.mongodb import client, db  # noqa: E402

DAY1, DAY2, DAY3 = "2026-10-05", "2026-10-06", "2026-10-07"


class FakeUpload:
    def __init__(self, text, filename="attendance.csv"):
        self.filename, self._content = filename, text.encode("utf-8")

    async def read(self):
        return self._content


def csv_file(rows):
    return FakeUpload("Employee Code,Punch Time\n" + "\n".join(f"{code},{stamp}" for code, stamp in rows) + "\n")


FILE_ROWS = [
    ("4", f"{DAY1} 09:03:00"), ("4", f"{DAY1} 13:00:00"), ("4", f"{DAY1} 13:45:00"), ("4", f"{DAY1} 18:10:00"),
    ("4", f"{DAY2} 09:10:00"), ("4", f"{DAY2} 18:00:00"),
    ("4", f"{DAY3} 09:05:00"),
    ("5", f"{DAY1} 09:30:00"), ("5", f"{DAY1} 17:30:00"),
    ("5", f"{DAY2} 09:31:00"), ("5", f"{DAY2} 17:31:00"),
]


class EsslEmployeeRecreationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not db.name.endswith("_test"):
            raise unittest.SkipTest(f"Refusing to run against database {db.name!r}; use a *_test database.")
        try:
            client.admin.command("ping")
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"MongoDB is not reachable: {exc}")
        client.drop_database(db.name)
        db["attendance"].create_index([("user_id", 1), ("attendance_date", 1)], unique=True)
        db["essl_raw_punches"].create_index("punch_key", unique=True)

    @classmethod
    def tearDownClass(cls):
        if db.name.endswith("_test"):
            client.drop_database(db.name)

    def setUp(self):
        for name in ["users", "employee_details", "attendance", "essl_raw_punches", "essl_punch_issues",
                     "essl_sync_history", "monthly_payroll_records", "attendance_modification_history",
                     "audit_logs", "leave_requests", "leave_policies", "holidays"]:
            db[name].delete_many({})
        self.admin = {"_id": ObjectId(), "name": "HR Admin", "role": "HR Admin"}
        patcher = patch.object(essl, "_require_essl_admin", return_value=self.admin)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.old_id = self.add_user("EMP004", "Karthik", "4")
        self.other_id = self.add_user("EMP005", "Meena", "5")

    # -- helpers --------------------------------------------------------
    def add_user(self, employee_id, name, essl_id):
        oid = ObjectId()
        db["users"].insert_one({
            "_id": oid, "employee_id": employee_id, "name": name, "is_active": True,
            "joining_date": "2026-01-01", "date_of_joining": "2026-01-01",
            "essl_employee_id": essl_id, "essl_employee_id_normalized": essl._normalise_employee_id(essl_id),
        })
        return str(oid)

    def run_import(self, selected, rows=None):
        return asyncio.run(essl.import_essl_attendance_file(
            file=csv_file(rows or FILE_ROWS), selected_employee_ids=json.dumps(list(selected)), authorization="Bearer x"))

    def delete_and_recreate_emp004(self):
        """Same as DELETE /api/users/{id}: hard delete of the user and employee_details only."""
        db["users"].delete_one({"_id": ObjectId(self.old_id)})
        db["employee_details"].delete_one({"user_id": self.old_id})
        return self.add_user("EMP004", "Karthik", "4")

    def attendance(self, user_id, day):
        return db["attendance"].find_one({"user_id": user_id, "attendance_date": day})

    # -- the reported scenario ----------------------------------------
    def test_reimport_after_delete_and_recreate_imports_into_new_employee(self):
        first = self.run_import([self.old_id])
        self.assertEqual(first["attendance_records_created"], 3)
        new_id = self.delete_and_recreate_emp004()
        self.assertNotEqual(new_id, self.old_id)
        meena_before = {d: self.attendance(self.other_id, d) for d in (DAY1, DAY2)}

        result = self.run_import([new_id])
        self.assertEqual(result["empty_punch_records"], 0)
        self.assertEqual(result["failed_records"], 0)
        self.assertEqual(result["punches_imported"], 7)
        self.assertEqual(result["valid_punch_records"], 7)
        self.assertEqual(result["attendance_records_created"], 3)
        self.assertFalse([r for r in result["row_results"] if r["result"] == "EMPTY_PUNCHES"])

        day1 = self.attendance(new_id, DAY1)
        self.assertEqual((day1["employee_id"], day1["status"], day1["attendance_source"]), ("EMP004", "Present", "ESSL"))
        self.assertEqual((day1["check_in_time"], day1["check_out_time"]), ("09:03", "18:10"))
        self.assertEqual(self.attendance(new_id, DAY3)["check_out_time"], None)

        # Punches now belong to the new employee only.
        owners = {p["user_id"] for p in db["essl_raw_punches"].find({"essl_employee_id": "4"})}
        self.assertEqual(owners, {new_id})
        self.assertEqual(db["essl_raw_punches"].count_documents({"user_id": self.old_id}), 0)

        # Calculation uses the new employee's punches.
        user = db["users"].find_one({"_id": ObjectId(new_id)})
        with patch.object(att, "company_today", return_value=__import__("datetime").date(2026, 10, 20)):
            details = att._calculate_daily_attendance(new_id, __import__("datetime").date(2026, 10, 5), user)
        self.assertTrue(details["first_check_in"].startswith("2026-10-05T09:03"))
        self.assertTrue(details["last_check_out"].startswith("2026-10-05T18:10"))
        self.assertEqual(details["elapsed_minutes"], 547)  # Total Duration 9h 07m
        self.assertTrue(details["working_intervals"])
        self.assertEqual(details["working_minutes"] + details["outside_minutes"], details["elapsed_minutes"])
        print("\n   day1 calc:", {k: details[k] for k in ("first_check_in", "last_check_out", "working_duration", "total_duration", "break_duration", "attendance_state")},
              "intervals:", [(i["from_time"][-14:-9], i["to_time"][-14:-9]) for i in details["working_intervals"]])

        # Unselected employee untouched.
        for day, before in meena_before.items():
            self.assertEqual(self.attendance(self.other_id, day), before)
        self.assertEqual(db["essl_raw_punches"].count_documents({"user_id": self.other_id}), 0)

    def test_other_employees_mapping_and_attendance_unaffected_when_selected_too(self):
        self.run_import([self.old_id, self.other_id])
        meena = {d: self.attendance(self.other_id, d) for d in (DAY1, DAY2)}
        new_id = self.delete_and_recreate_emp004()
        result = self.run_import([new_id])
        self.assertEqual(result["empty_punch_records"], 0)
        self.assertEqual({d: self.attendance(self.other_id, d) for d in (DAY1, DAY2)}, meena)
        self.assertEqual(db["users"].find_one({"_id": ObjectId(self.other_id)})["essl_employee_id"], "5")
        self.assertEqual({p["user_id"] for p in db["essl_raw_punches"].find({"essl_employee_id": "5"})}, {self.other_id})

    def test_first_import_without_history_still_works(self):
        result = self.run_import([self.old_id])
        self.assertEqual((result["attendance_records_created"], result["empty_punch_records"]), (3, 0))

    def test_reimport_updates_existing_attendance_from_latest_file(self):
        new_id = self.delete_and_recreate_emp004()
        self.run_import([new_id])
        rows = [r for r in FILE_ROWS if not (r[0] == "4" and r[1].startswith(DAY3))] + [("4", f"{DAY3} 09:05:00"), ("4", f"{DAY3} 17:45:00")]
        result = self.run_import([new_id], rows)
        self.assertEqual(result["attendance_records_updated"], 1)
        self.assertEqual(result["attendance_records_unchanged"], 2)
        self.assertEqual(self.attendance(new_id, DAY3)["check_out_time"], "17:45")
        self.assertEqual(db["attendance"].count_documents({"user_id": new_id}), 3)  # no duplicates

    def test_stale_punch_of_deleted_employee_not_in_new_file_does_not_leak(self):
        self.run_import([self.old_id])
        new_id = self.delete_and_recreate_emp004()
        rows = [r for r in FILE_ROWS if r[0] == "4" and r[1].startswith(DAY1) and not r[1].endswith("18:10:00")] + [("4", f"{DAY1} 17:55:00")]
        self.run_import([new_id], rows)
        record = self.attendance(new_id, DAY1)
        # The old 18:10 punch is gone for the new employee: the latest file is authoritative.
        self.assertEqual(record["check_out_time"], "17:55")

    def test_payroll_finalized_month_is_preserved(self):
        new_id = self.delete_and_recreate_emp004()
        db["monthly_payroll_records"].insert_one({"employee_id": "EMP004", "period": "2026-10", "status": "Approved"})
        result = self.run_import([new_id])
        self.assertEqual(result["payroll_locked_records"], 3)
        self.assertEqual(result["attendance_records_created"], 0)
        self.assertEqual(db["attendance"].count_documents({"user_id": new_id}), 0)

    def test_manual_attendance_is_protected(self):
        new_id = self.delete_and_recreate_emp004()
        db["attendance"].insert_one({"user_id": new_id, "attendance_date": DAY1, "status": "Present",
                                     "attendance_source": "MANUAL", "check_in_time": "10:00", "check_out_time": "19:00"})
        result = self.run_import([new_id])
        self.assertEqual(result["attendance_records_protected"], 1)
        self.assertEqual(self.attendance(new_id, DAY1)["check_in_time"], "10:00")

    def test_file_without_the_employees_punches_changes_nothing(self):
        new_id = self.delete_and_recreate_emp004()
        self.run_import([new_id])
        before = {d: self.attendance(new_id, d) for d in (DAY1, DAY2, DAY3)}
        self.run_import([new_id], [r for r in FILE_ROWS if r[0] == "5"])
        self.assertEqual({d: self.attendance(new_id, d) for d in (DAY1, DAY2, DAY3)}, before)

    def test_save_raw_punch_reassigns_owner_but_keeps_original_fields(self):
        stamp = datetime(2026, 10, 5, 3, 33, tzinfo=timezone.utc)
        old_user = {"_id": ObjectId(self.old_id), "employee_id": "EMP004"}
        new_user = {"_id": ObjectId(), "employee_id": "EMP004"}
        now = datetime.now(timezone.utc)
        essl._save_raw_punch("s1", old_user, "4", stamp, None, None, now, source="ESSL_FILE")
        essl._save_raw_punch("s2", new_user, "4", stamp, None, None, now, source="ESSL_FILE")
        rows = db["essl_raw_punches"].find({})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["user_id"], str(new_user["_id"]))
        self.assertEqual((rows[0]["first_sync_id"], rows[0]["last_sync_id"], rows[0]["punch_at"]), ("s1", "s2", stamp))


if __name__ == "__main__":
    unittest.main()
