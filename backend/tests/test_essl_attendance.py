import unittest
import json
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from bson import ObjectId
from cryptography.fernet import Fernet
from fastapi import HTTPException

from app import essl
from app.essl import EsslConfigPayload, EsslSyncPayload


class EsslPunchProcessingTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(
            2026, 10, 7, 20, 0, tzinfo=ZoneInfo("Asia/Kolkata")
        )

    def test_iso_timestamp_is_normalized_to_india_date_and_timezone(self):
        result = essl._parse_punch_timestamp(
            "2026-10-07T02:30:00Z",
            None,
        )
        self.assertEqual(result.date(), date(2026, 10, 7))
        self.assertEqual(result.strftime("%H:%M"), "08:00")
        self.assertEqual(result.tzinfo, ZoneInfo("Asia/Kolkata"))

    def test_naive_timestamp_is_treated_as_india_local_time(self):
        result = essl._parse_punch_timestamp(
            "2026-10-07 09:03:00",
            "%Y-%m-%d %H:%M:%S",
        )
        self.assertEqual(result.strftime("%Y-%m-%d %H:%M"), "2026-10-07 09:03")

    def test_epoch_milliseconds_are_accepted(self):
        epoch_ms = datetime(
            2026, 10, 7, 3, 33, tzinfo=timezone.utc
        ).timestamp() * 1000
        result = essl._parse_punch_timestamp(epoch_ms, None)
        self.assertEqual(result.strftime("%H:%M"), "09:03")

    def test_invalid_and_future_timestamps_are_rejected(self):
        with patch.object(essl, "company_now", return_value=self.now):
            for value in ("not-a-timestamp", "1990-01-01T00:00:00+05:30"):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    essl._parse_punch_timestamp(value, None)

            future = self.now.replace(day=8).isoformat()
            with self.assertRaisesRegex(ValueError, "future-dated"):
                essl._parse_punch_timestamp(future, None)

    def test_dot_paths_support_nested_objects_and_array_indexes(self):
        response = {"payload": {"rows": [{"emp": {"code": "1004"}}]}}
        self.assertEqual(
            essl._read_path(response, "payload.rows.0.emp.code"),
            "1004",
        )
        self.assertIsNone(essl._read_path(response, "payload.missing"))

    def test_one_punch_creates_check_in_without_check_out(self):
        user = {"_id": ObjectId(), "employee_id": "26IT004"}
        punch = datetime(2026, 10, 7, 9, 3, tzinfo=ZoneInfo("Asia/Kolkata"))
        with (
            patch.object(essl.attendance_collection, "find_one", return_value=None),
            patch.object(essl.attendance_collection, "update_one") as update,
        ):
            result = essl._save_daily_attendance(
                user, "2026-10-07", [{"timestamp": punch}], "sync-1", self.now
            )

        values = update.call_args.args[1]["$set"]
        self.assertEqual(result, "created")
        self.assertEqual(values["check_in_time"], "09:03")
        self.assertIsNone(values["check_out_time"])
        self.assertEqual(values["attendance_type"], "OFFICE")
        self.assertEqual(values["attendance_source"], "ESSL")

    def test_many_punches_select_first_and_last(self):
        user = {"_id": ObjectId(), "employee_id": "26IT004"}
        times = [
            datetime(2026, 10, 7, hour, minute, tzinfo=ZoneInfo("Asia/Kolkata"))
            for hour, minute in ((9, 3), (12, 45), (13, 32), (18, 12))
        ]
        with (
            patch.object(essl.attendance_collection, "find_one", return_value=None),
            patch.object(essl.attendance_collection, "update_one") as update,
        ):
            essl._save_daily_attendance(
                user,
                "2026-10-07",
                [{"timestamp": value} for value in times],
                "sync-1",
                self.now,
            )
        values = update.call_args.args[1]["$set"]
        self.assertEqual(values["check_in_time"], "09:03")
        self.assertEqual(values["check_out_time"], "18:12")

    def test_duplicate_sync_is_unchanged_and_new_late_punch_updates_checkout(self):
        user = {"_id": ObjectId(), "employee_id": "26IT004"}
        first = datetime(2026, 10, 7, 9, 5, tzinfo=ZoneInfo("Asia/Kolkata"))
        checkout = datetime(2026, 10, 7, 17, 45, tzinfo=ZoneInfo("Asia/Kolkata"))
        last = datetime(2026, 10, 7, 18, 10, tzinfo=ZoneInfo("Asia/Kolkata"))
        existing = {
            "employee_id": "26IT004",
            "status": "Present",
            "attendance_type": "OFFICE",
            "attendance_source": "ESSL",
            "check_in_time": "09:05",
            "check_out_time": "17:45",
            "first_punch": first.astimezone(timezone.utc).replace(tzinfo=None),
            "last_punch": checkout.astimezone(timezone.utc).replace(tzinfo=None),
        }
        with patch.object(essl.attendance_collection, "find_one", return_value=existing):
            self.assertEqual(
                essl._save_daily_attendance(
                    user,
                    "2026-10-07",
                    [{"timestamp": first}, {"timestamp": checkout}],
                    "sync-2",
                    self.now,
                ),
                "unchanged",
            )
        with (
            patch.object(essl.attendance_collection, "find_one", return_value=existing),
            patch.object(essl.attendance_collection, "update_one") as update,
        ):
            result = essl._save_daily_attendance(
                user,
                "2026-10-07",
                [{"timestamp": first}, {"timestamp": checkout}, {"timestamp": last}],
                "sync-3",
                self.now,
            )
        self.assertEqual(result, "updated")
        self.assertEqual(update.call_args.args[1]["$set"]["check_out_time"], "18:10")

    def test_existing_manual_attendance_is_not_overwritten(self):
        existing = {"attendance_source": "MANUAL"}
        with (
            patch.object(essl.attendance_collection, "find_one", return_value=existing),
            patch.object(essl.attendance_collection, "update_one") as update,
        ):
            result = essl._save_daily_attendance(
                {"_id": ObjectId(), "employee_id": "A1"},
                "2026-10-07",
                [{"timestamp": self.now}],
                "sync-1",
                self.now,
            )
        self.assertEqual(result, "protected")
        update.assert_not_called()

    def test_saved_raw_punches_are_reloaded_for_local_day_grouping(self):
        stored_at = datetime(
            2026, 10, 7, 3, 35, tzinfo=timezone.utc
        ).replace(tzinfo=None)
        with patch.object(
            essl.essl_raw_punches_collection,
            "find",
            return_value=[{"punch_at": stored_at}],
        ) as find:
            punches = essl._load_daily_raw_punches(
                "user-1",
                "2026-10-07",
            )
        self.assertEqual(punches[0]["timestamp"].strftime("%H:%M"), "09:05")
        self.assertEqual(
            find.call_args.args[0]["punch_at"]["$gte"],
            datetime(2026, 10, 6, 18, 30, tzinfo=timezone.utc),
        )
        self.assertEqual(
            find.call_args.args[0]["punch_at"]["$lt"],
            datetime(2026, 10, 7, 18, 30, tzinfo=timezone.utc),
        )

    def test_employee_id_normalization_does_not_treat_object_id_as_mapping(self):
        self.assertEqual(essl._normalise_employee_id(" 10A "), "10a")
        self.assertEqual(essl._normalise_employee_id(1004), "1004")
        self.assertIsNone(essl._normalise_employee_id(ObjectId()))
        self.assertIsNone(essl._normalise_employee_id(True))


class EsslSyncAuthorizationAndSafetyTests(unittest.TestCase):
    def test_hr_admin_and_administrator_are_authorized_but_employee_is_not(self):
        for role in ("HR Admin", "Administrator"):
            with patch.object(essl, "_current_user", return_value={"role": role}):
                self.assertEqual(essl._require_essl_admin("token")["role"], role)

        with (
            patch.object(essl, "_current_user", return_value={"role": "Employee"}),
            self.assertRaises(HTTPException) as error,
        ):
            essl._require_essl_admin("token")
        self.assertEqual(error.exception.status_code, 403)

    def test_connection_test_reports_valid_response_and_api_failure(self):
        config = {"records_path": ""}
        with (
            patch.object(essl, "_require_essl_admin"),
            patch.object(essl, "_load_config", return_value=config),
            patch.object(essl, "_decrypt_credentials", return_value={}),
            patch.object(essl, "_request_json", return_value=[]),
        ):
            result = essl.test_essl_connection("token")
        self.assertTrue(result["success"])
        self.assertEqual(result["records_returned"], 0)

        with (
            patch.object(essl, "_require_essl_admin"),
            patch.object(essl, "_load_config", return_value=config),
            patch.object(essl, "_decrypt_credentials", return_value={}),
            patch.object(essl, "_request_json", side_effect=RuntimeError("offline")),
            self.assertRaises(HTTPException) as error,
        ):
            essl.test_essl_connection("token")
        self.assertEqual(error.exception.status_code, 502)

    def test_saved_credentials_and_request_values_are_never_returned_in_plaintext(self):
        key = Fernet.generate_key().decode("ascii")
        payload = EsslConfigPayload(
            api_url="https://essl.example/api/punches",
            method="GET",
            auth_type="bearer",
            auth_secret="private-token",
            request_params={"tenant": "private-tenant"},
            request_headers={"X-Customer-Key": "private-header"},
            records_path="data",
            employee_id_path="emp",
            timestamp_path="time",
        )
        with (
            patch.dict("os.environ", {"ESSL_CONFIG_ENCRYPTION_KEY": key}),
            patch.object(essl, "_require_essl_admin", return_value={"_id": ObjectId()}),
            patch.object(essl.essl_config_collection, "find_one", return_value=None),
            patch.object(essl.essl_config_collection, "replace_one") as replace,
        ):
            public_config = essl.save_essl_config(payload, "token")

        stored = replace.call_args.args[1]
        serialized_config = json.dumps(stored, default=str)
        self.assertNotIn("private-token", serialized_config)
        self.assertNotIn("private-tenant", serialized_config)
        self.assertNotIn("private-header", serialized_config)
        self.assertNotIn("private-token", json.dumps(public_config))
        decrypted = json.loads(
            Fernet(key.encode("ascii")).decrypt(
                stored["credentials_encrypted"].encode("ascii")
            )
        )
        self.assertEqual(decrypted["auth_secret"], "private-token")
        self.assertEqual(decrypted["request_params"]["tenant"], "private-tenant")

    def test_http_request_uses_configured_auth_and_date_query(self):
        response = MagicMock()
        response.read.return_value = b'{"rows":[]}'
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value = response
        config = {
            "api_url": "https://essl.example/api/punches",
            "method": "GET",
            "auth_type": "bearer",
            "from_date_parameter": "start",
            "to_date_parameter": "end",
        }
        credentials = {
            "auth_secret": "test-token",
            "request_params": {"company": "sample"},
        }
        with patch("app.essl.urllib.request.build_opener", return_value=opener):
            result = essl._request_json(
                config,
                credentials,
                date(2026, 10, 1),
                date(2026, 10, 7),
            )

        request = opener.open.call_args.args[0]
        self.assertEqual(result, {"rows": []})
        self.assertIn("company=sample", request.full_url)
        self.assertIn("start=2026-10-01", request.full_url)
        self.assertIn("end=2026-10-07", request.full_url)
        self.assertEqual(request.get_header("Authorization"), "Bearer test-token")

    def test_wfh_routes_remain_available_and_require_authentication(self):
        payload = essl.EsslSyncPayload(
            from_date="2026-10-07",
            to_date="2026-10-07",
        )
        self.assertEqual(payload.from_date, date(2026, 10, 7))

        from app.attendance import (
            WfhAttendancePayload,
            WfhChallengePayload,
            WfhLocationVerifyPayload,
            create_wfh_face_challenge,
            verify_wfh_location,
            wfh_check_in,
            wfh_check_out,
        )
        from app import attendance

        location = WfhLocationVerifyPayload(
            latitude=0,
            longitude=0,
            accuracy_m=5,
            location_timestamp=1,
            purpose="check_in",
        )
        attendance_payload = WfhAttendancePayload(
            latitude=0,
            longitude=0,
            accuracy_m=5,
            location_timestamp=1,
            challenge_id="single-use-challenge-id-123456",
            frames=["frame"] * 8,
        )
        with (
            patch.object(attendance, "WFH_ATTENDANCE_ENABLED", True),
            patch.object(
                attendance,
                "_require_wfh_employee",
                side_effect=HTTPException(401, "Authentication required"),
            ),
        ):
            for endpoint, payload in (
                (verify_wfh_location, location),
                (
                    create_wfh_face_challenge,
                    WfhChallengePayload(
                        purpose="check_in",
                        latitude=0,
                        longitude=0,
                        accuracy_m=5,
                        location_timestamp=1,
                    ),
                ),
                (wfh_check_in, attendance_payload),
                (wfh_check_out, attendance_payload),
            ):
                with self.subTest(endpoint=endpoint.__name__):
                    with self.assertRaises(HTTPException) as error:
                        endpoint(payload, "token")
                    self.assertEqual(error.exception.status_code, 401)

    def test_employee_cannot_trigger_synchronization(self):
        payload = EsslSyncPayload(
            from_date="2026-10-07",
            to_date="2026-10-07",
        )
        with (
            patch.object(
                essl,
                "_require_essl_admin",
                side_effect=HTTPException(403, "Forbidden"),
            ),
            patch.object(essl.essl_sync_history_collection, "insert_one") as insert,
            self.assertRaises(HTTPException) as error,
        ):
            essl.synchronize_essl_attendance(payload, "employee-token")
        self.assertEqual(error.exception.status_code, 403)
        insert.assert_not_called()

    def test_empty_api_response_completes_without_creating_attendance(self):
        admin = {"_id": ObjectId()}
        config = {
            "records_path": "records",
            "employee_id_path": "emp",
            "timestamp_path": "at",
            "method": "GET",
        }
        with (
            patch.object(essl, "_require_essl_admin", return_value=admin),
            patch.object(essl, "_load_config", return_value=config),
            patch.object(essl, "_decrypt_credentials", return_value={}),
            patch.object(essl, "_request_json", return_value={"records": []}),
            patch.object(essl, "_save_daily_attendance") as save_attendance,
            patch.object(essl.users_collection, "find", return_value=[]),
            patch.object(essl.essl_sync_history_collection, "insert_one"),
            patch.object(essl.essl_sync_history_collection, "update_one"),
            patch.object(essl, "company_today", return_value=date(2026, 10, 7)),
        ):
            result = essl.synchronize_essl_attendance(
                EsslSyncPayload(from_date="2026-10-07", to_date="2026-10-07"),
                "admin-token",
            )
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["punches_received"], 0)
        self.assertEqual(result["attendance_records_created"], 0)
        save_attendance.assert_not_called()

    def test_malformed_api_response_fails_before_attendance_is_changed(self):
        admin = {"_id": ObjectId()}
        config = {
            "records_path": "data.records",
            "employee_id_path": "employeeId",
            "timestamp_path": "time",
        }
        with (
            patch.object(essl, "_require_essl_admin", return_value=admin),
            patch.object(essl, "_load_config", return_value=config),
            patch.object(essl, "_decrypt_credentials", return_value={}),
            patch.object(essl, "_request_json", return_value={"wrong": []}),
            patch.object(essl.essl_sync_history_collection, "insert_one"),
            patch.object(essl.essl_sync_history_collection, "update_one") as history_update,
            patch.object(essl, "_save_daily_attendance") as save_attendance,
            patch.object(essl, "company_today", return_value=date(2026, 10, 7)),
            self.assertRaises(HTTPException) as error,
        ):
            essl.synchronize_essl_attendance(
                EsslSyncPayload(from_date="2026-10-07", to_date="2026-10-07"),
                "token",
            )

        self.assertEqual(error.exception.status_code, 502)
        self.assertIn("Existing attendance data has not been changed", error.exception.detail)
        save_attendance.assert_not_called()
        self.assertEqual(
            history_update.call_args.args[1]["$set"]["status"],
            "FAILED",
        )

    def test_sync_groups_multiple_employee_dates_and_rejects_unmatched_and_invalid(self):
        admin = {"_id": ObjectId()}
        user_id = ObjectId()
        employee = {
            "_id": user_id,
            "employee_id": "26IT004",
            "essl_employee_id_normalized": "1004",
        }
        second_employee = {
            "_id": ObjectId(),
            "employee_id": "26IT005",
            "essl_employee_id_normalized": "2004",
        }
        records = [
            {"emp": "1004", "at": "2026-10-07T09:03:00+05:30"},
            {"emp": "1004", "at": "2026-10-07T18:12:00+05:30"},
            {"emp": "1004", "at": "2026-10-06T09:10:00+05:30"},
            {"emp": "2004", "at": "2026-10-07T09:12:00+05:30"},
            {"emp": "unknown", "at": "2026-10-07T09:00:00+05:30"},
            {"emp": "1004", "at": "invalid"},
        ]
        config = {
            "records_path": "records",
            "employee_id_path": "emp",
            "timestamp_path": "at",
            "method": "GET",
        }
        saved_groups = []
        issues = []

        def save_group(_user, attendance_date, group, _sync_id, _now):
            del _user, _sync_id, _now
            saved_groups.append((attendance_date, [item["timestamp"] for item in group]))
            return "created"

        with (
            patch.object(essl, "_require_essl_admin", return_value=admin),
            patch.object(essl, "_load_config", return_value=config),
            patch.object(essl, "_decrypt_credentials", return_value={}),
            patch.object(essl, "_request_json", return_value={"records": records}),
            patch.object(essl, "_save_daily_attendance", side_effect=save_group),
            patch.object(essl, "_save_raw_punch"),
            patch.object(essl, "_load_daily_raw_punches", return_value=[]),
            patch.object(
                essl,
                "_store_issue",
                side_effect=lambda *args: issues.append(args),
            ),
            patch.object(
                essl.users_collection,
                "find",
                return_value=[employee, second_employee],
            ),
            patch.object(essl.essl_sync_history_collection, "insert_one"),
            patch.object(essl.essl_sync_history_collection, "update_one"),
            patch.object(essl, "company_today", return_value=date(2026, 10, 7)),
            patch.object(
                essl,
                "company_now",
                return_value=datetime(
                    2026, 10, 7, 20, 0, tzinfo=ZoneInfo("Asia/Kolkata")
                ),
            ),
        ):
            result = essl.synchronize_essl_attendance(
                EsslSyncPayload(from_date="2026-10-06", to_date="2026-10-07"),
                "token",
            )

        self.assertEqual(result["employees_processed"], 2)
        self.assertEqual(result["punches_received"], 6)
        self.assertEqual(result["processed_punches"], 4)
        self.assertEqual(result["unmatched_employees"], 1)
        self.assertEqual(result["invalid_records"], 1)
        self.assertEqual(len(saved_groups), 3)
        self.assertEqual(len(issues), 2)
        day_one = next(group for group in saved_groups if group[0] == "2026-10-07")
        self.assertEqual([value.strftime("%H:%M") for value in day_one[1]], ["09:03", "18:12"])


if __name__ == "__main__":
    unittest.main()
