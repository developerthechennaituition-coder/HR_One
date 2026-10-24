import unittest
from time import time
from types import SimpleNamespace
from unittest.mock import patch

from bson import ObjectId
from fastapi import HTTPException
from pydantic import ValidationError

from app import attendance
from app.attendance import (
    WfhAttendancePayload,
    WfhChallengePayload,
    WfhLocationPayload,
    WfhSettingsPayload,
    _configured_wfh_accuracy_limit,
    _calculate_distance_meters,
    _validate_wfh_biometric_attendance,
    _validate_wfh_location,
)
from app.wfh_biometrics import _verify_liveness
from app.wfh_biometrics import _configured_face_match_tolerance


class WfhAttendanceValidationTests(unittest.TestCase):
    def setUp(self):
        self.wfh_enabled = patch.object(
            attendance,
            "WFH_ATTENDANCE_ENABLED",
            True,
        )
        self.wfh_enabled.start()
        self.addCleanup(self.wfh_enabled.stop)
        self.home = {
            "home_latitude": 0.0,
            "home_longitude": 0.0,
            "allowed_radius": 100,
        }

    @staticmethod
    def fresh_location_timestamp():
        return time() * 1000

    def test_server_distance_calculation_uses_meters(self):
        self.assertAlmostEqual(
            _calculate_distance_meters(0.0, 0.0, 0.0005, 0.0),
            55.6,
            delta=0.2,
        )

    def test_liveness_challenge_requires_requested_action_order(self):
        observations = [
            (0.3, 0.0),
            (0.3, 0.0),
            (0.1, 0.0),
            (0.3, 0.0),
            (0.3, 0.12),
            (0.3, 0.15),
            (0.3, 0.0),
            (0.3, 0.0),
        ]

        _verify_liveness(observations, ["blink", "turn_left"])

        with self.assertRaisesRegex(ValueError, "actions in order"):
            _verify_liveness(observations, ["turn_left", "blink"])

    def test_face_match_tolerance_cannot_be_weakened(self):
        with patch.dict(
            "os.environ",
            {"WFH_FACE_MATCH_TOLERANCE": "0.4"},
        ):
            self.assertEqual(_configured_face_match_tolerance(), 0.4)

        with patch.dict(
            "os.environ",
            {"WFH_FACE_MATCH_TOLERANCE": "0.49"},
        ):
            with self.assertRaisesRegex(RuntimeError, "no more than 0.48"):
                _configured_face_match_tolerance()

    def test_coordinates_from_same_home_use_haversine_distance(self):
        distance = _calculate_distance_meters(
            9.593801234,
            77.957391234,
            9.593712345,
            77.957423456,
        )

        self.assertAlmostEqual(distance, 10.4, delta=0.2)

    def test_location_inside_default_geofence_is_accepted(self):
        distance, radius = _validate_wfh_location(
            self.home,
            WfhLocationPayload(
                latitude=0.0005846,
                longitude=0.0,
                accuracy_m=10,
                location_timestamp=self.fresh_location_timestamp(),
            ),
        )

        self.assertLessEqual(distance, radius)
        self.assertAlmostEqual(distance, 65, delta=0.2)
        self.assertEqual(radius, 100)

    def test_location_outside_geofence_is_rejected_server_side(self):
        with self.assertRaises(HTTPException) as error:
            _validate_wfh_location(
                self.home,
                WfhLocationPayload(
                    latitude=0.001348,
                    longitude=0.0,
                    accuracy_m=20,
                    location_timestamp=self.fresh_location_timestamp(),
                ),
            )

        self.assertEqual(error.exception.status_code, 403)
        self.assertIn("150 meters away", error.exception.detail)
        self.assertIn("within 100 meters", error.exception.detail)

    def test_low_accuracy_location_is_rejected(self):
        for accuracy in (150, 500):
            with self.subTest(accuracy=accuracy):
                with self.assertRaises(HTTPException) as error:
                    _validate_wfh_location(
                        self.home,
                        WfhLocationPayload(
                            latitude=0.00044966,
                            longitude=0.0,
                            accuracy_m=accuracy,
                            location_timestamp=self.fresh_location_timestamp(),
                        ),
                    )

                self.assertEqual(error.exception.status_code, 422)

    def test_50000_meter_location_accuracy_is_rejected_with_measurement(self):
        payload = WfhLocationPayload(
            latitude=0.0,
            longitude=0.0,
            accuracy_m=50000,
            location_timestamp=self.fresh_location_timestamp(),
        )

        with self.assertRaises(HTTPException) as error:
            _validate_wfh_location(self.home, payload)

        self.assertEqual(error.exception.status_code, 422)
        self.assertIn(
            "could not determine your location accurately enough",
            error.exception.detail,
        )

    def test_location_timestamp_is_required(self):
        with self.assertRaises(ValidationError):
            WfhLocationPayload(
                latitude=0,
                longitude=0,
                accuracy_m=20,
            )

    def test_stale_location_timestamp_is_rejected(self):
        payload = WfhLocationPayload(
            latitude=0,
            longitude=0,
            accuracy_m=20,
            location_timestamp=self.fresh_location_timestamp() - 180000,
        )

        with self.assertRaises(HTTPException) as error:
            _validate_wfh_location(self.home, payload)

        self.assertEqual(error.exception.status_code, 422)
        self.assertIn("outdated", error.exception.detail)

    def test_accuracy_of_50_meters_is_accepted_regardless_of_radius(self):
        user = {**self.home, "allowed_radius": 10}
        distance, radius = _validate_wfh_location(
            user,
            WfhLocationPayload(
                latitude=0.0,
                longitude=0.0,
                accuracy_m=50,
                location_timestamp=self.fresh_location_timestamp(),
            ),
        )

        self.assertEqual(distance, 0)
        self.assertEqual(radius, 10)

    def test_accuracy_configuration_is_capped_at_100_meters(self):
        with patch.dict("os.environ", {"WFH_MAX_LOCATION_ACCURACY_METERS": "50000"}):
            self.assertEqual(_configured_wfh_accuracy_limit(), 100)
            with self.assertRaises(HTTPException) as error:
                _validate_wfh_location(
                    self.home,
                    WfhLocationPayload(
                        latitude=0.0,
                        longitude=0.0,
                        accuracy_m=150,
                        location_timestamp=self.fresh_location_timestamp(),
                    ),
                )

        self.assertEqual(error.exception.status_code, 422)

    def test_accuracy_configuration_can_only_make_limit_stricter(self):
        with patch.dict("os.environ", {"WFH_MAX_LOCATION_ACCURACY_METERS": "25"}):
            self.assertEqual(_configured_wfh_accuracy_limit(), 25)
            with self.assertRaises(HTTPException) as error:
                _validate_wfh_location(
                    self.home,
                    WfhLocationPayload(
                        latitude=0,
                        longitude=0,
                        accuracy_m=26,
                        location_timestamp=self.fresh_location_timestamp(),
                    ),
                )
        self.assertEqual(error.exception.status_code, 422)

        with patch.dict("os.environ", {"WFH_MAX_LOCATION_ACCURACY_METERS": "500"}):
            self.assertEqual(_configured_wfh_accuracy_limit(), 100)

    def test_accuracy_20_and_80_meters_at_50_meter_distance_are_accepted(self):
        for accuracy in (20, 80):
            with self.subTest(accuracy=accuracy):
                distance, radius = _validate_wfh_location(
                    self.home,
                    WfhLocationPayload(
                        latitude=0.00044966,
                        longitude=0.0,
                        accuracy_m=accuracy,
                        location_timestamp=self.fresh_location_timestamp(),
                    ),
                )
                self.assertAlmostEqual(distance, 50, delta=0.1)
                self.assertEqual(radius, 100)

    def test_location_verification_precedes_face_verification(self):
        order = []
        user = {"_id": ObjectId()}
        payload = WfhAttendancePayload(
            latitude=0,
            longitude=0,
            accuracy_m=10,
            location_timestamp=self.fresh_location_timestamp(),
            challenge_id="single-use-challenge-id-123456",
            frames=["frame"] * 8,
        )

        def validate_face(*_args):
            order.append("face")

        def validate_location(*_args):
            order.append("location")
            return 0, 100

        with (
            patch.object(
                attendance,
                "_assert_wfh_action_allowed",
                side_effect=lambda *_args: order.append("eligibility"),
            ),
            patch.object(
                attendance,
                "_consume_wfh_face_challenge",
                return_value={"actions": ["blink", "turn_left"]},
            ),
            patch.object(attendance, "_decode_and_verify_face", side_effect=validate_face),
            patch.object(
                attendance,
                "_validate_wfh_location",
                side_effect=validate_location,
            ),
        ):
            _validate_wfh_biometric_attendance(user, payload, "check_in")

        self.assertEqual(order, ["eligibility", "location", "face"])

    def test_face_failure_occurs_after_location_verification(self):
        user = {"_id": ObjectId()}
        payload = WfhAttendancePayload(
            latitude=0,
            longitude=0,
            accuracy_m=10,
            location_timestamp=self.fresh_location_timestamp(),
            challenge_id="single-use-challenge-id-123456",
            frames=["frame"] * 8,
        )
        with (
            patch.object(attendance, "_assert_wfh_action_allowed"),
            patch.object(
                attendance,
                "_consume_wfh_face_challenge",
                return_value={"actions": ["blink", "turn_left"]},
            ),
            patch.object(
                attendance,
                "_decode_and_verify_face",
                side_effect=HTTPException(422, "Face verification failed."),
            ),
            patch.object(
                attendance,
                "_validate_wfh_location",
                return_value=(0, 100),
            ) as validate_location,
        ):
            with self.assertRaises(HTTPException):
                _validate_wfh_biometric_attendance(user, payload, "check_in")

        validate_location.assert_called_once()

    def test_poor_location_does_not_consume_challenge_or_start_face_verification(self):
        user = {
            "_id": ObjectId(),
            **self.home,
        }
        payload = WfhAttendancePayload(
            latitude=0,
            longitude=0,
            accuracy_m=150,
            location_timestamp=self.fresh_location_timestamp(),
            challenge_id="single-use-challenge-id-123456",
            frames=["frame"] * 8,
        )

        with (
            patch.object(attendance, "_assert_wfh_action_allowed"),
            patch.object(
                attendance,
                "_consume_wfh_face_challenge",
            ) as consume_challenge,
            patch.object(attendance, "_decode_and_verify_face") as verify_face,
            self.assertRaises(HTTPException),
        ):
            _validate_wfh_biometric_attendance(user, payload, "check_in")

        consume_challenge.assert_not_called()
        verify_face.assert_not_called()

    def test_poor_location_cannot_start_server_face_challenge(self):
        user = {"_id": ObjectId()}
        payload = WfhChallengePayload(
            purpose="check_in",
            latitude=0,
            longitude=0,
            accuracy_m=150,
            location_timestamp=self.fresh_location_timestamp(),
        )

        with (
            patch.object(attendance, "_require_wfh_employee", return_value=user),
            patch.object(attendance, "_face_is_registered", return_value=True),
            patch.object(attendance, "_assert_wfh_action_allowed"),
            patch.object(
                attendance,
                "_validate_wfh_location",
                side_effect=HTTPException(422, "Location accuracy is insufficient."),
            ),
            patch.object(
                attendance.wfh_liveness_challenges_collection,
                "insert_one",
            ) as insert_challenge,
            self.assertRaises(HTTPException),
        ):
            attendance.create_wfh_face_challenge(payload)

        insert_challenge.assert_not_called()

    def test_configured_1050_meter_radius_is_preserved(self):
        distance, radius = _validate_wfh_location(
            {**self.home, "allowed_radius": 1050},
            WfhLocationPayload(
                latitude=0.0,
                longitude=0.0,
                accuracy_m=50,
                location_timestamp=self.fresh_location_timestamp(),
            ),
        )

        self.assertEqual(distance, 0)
        self.assertEqual(radius, 1050)

    def test_invalid_persisted_home_location_and_radius_are_rejected(self):
        invalid_settings = (
            {**self.home, "home_latitude": "0"},
            {**self.home, "home_latitude": 91},
            {**self.home, "allowed_radius": 0},
            {**self.home, "allowed_radius": 5001},
        )
        payload = WfhLocationPayload(
            latitude=0.0,
            longitude=0.0,
            accuracy_m=10,
            location_timestamp=self.fresh_location_timestamp(),
        )

        for settings in invalid_settings:
            with self.subTest(settings=settings):
                with self.assertRaises(HTTPException) as error:
                    _validate_wfh_location(settings, payload)
                self.assertEqual(error.exception.status_code, 409)

    def test_wfh_radius_must_be_configurable_and_positive(self):
        settings = WfhSettingsPayload(
            is_wfh_enabled=True,
            home_latitude=12.9,
            home_longitude=77.5,
            allowed_radius=250,
        )
        self.assertEqual(settings.allowed_radius, 250)

        with self.assertRaises(ValidationError):
            WfhSettingsPayload(
                is_wfh_enabled=True,
                home_latitude=12.9,
                home_longitude=77.5,
                allowed_radius=0,
            )

        with self.assertRaises(ValidationError):
            WfhSettingsPayload(
                is_wfh_enabled=True,
                home_latitude=12.9,
                home_longitude=77.5,
                allowed_radius=5001,
            )

    def test_admin_location_update_targets_each_employee_without_rounding(self):
        employee_ids = [str(ObjectId()), str(ObjectId())]
        employees = [{"_id": ObjectId(employee_id)} for employee_id in employee_ids]
        admin_id = ObjectId()
        home_latitude = 12.345678901
        home_longitude = 67.890123456
        payload = WfhSettingsPayload(
            is_wfh_enabled=True,
            home_latitude=home_latitude,
            home_longitude=home_longitude,
            allowed_radius=100,
            home_location_accuracy_meters=25,
            home_location_timestamp=self.fresh_location_timestamp(),
        )

        with (
            patch.object(
                attendance,
                "_require_wfh_admin",
                return_value={"_id": admin_id},
            ),
            patch.object(
                attendance.users_collection,
                "find_one",
                side_effect=employees,
            ),
            patch.object(attendance.users_collection, "update_one") as update,
            patch.object(attendance, "_face_is_registered", return_value=False),
        ):
            for employee_id in employee_ids:
                attendance.update_wfh_employee_settings(employee_id, payload)

        self.assertEqual(update.call_count, 2)
        for employee_id, update_call in zip(employee_ids, update.call_args_list):
            self.assertEqual(
                update_call.args[0],
                {"_id": ObjectId(employee_id)},
            )
            saved_settings = update_call.args[1]["$set"]
            self.assertIsInstance(saved_settings["home_latitude"], float)
            self.assertIsInstance(saved_settings["home_longitude"], float)
            self.assertEqual(saved_settings["home_latitude"], home_latitude)
            self.assertEqual(saved_settings["home_longitude"], home_longitude)
            self.assertEqual(saved_settings["home_location_accuracy_meters"], 25)
            self.assertEqual(
                saved_settings["home_location_timestamp"],
                payload.home_location_timestamp,
            )
            self.assertEqual(
                saved_settings["home_location_updated_by"],
                str(admin_id),
            )

    def test_employee_without_wfh_eligibility_is_rejected(self):
        with (
            patch.object(
                attendance,
                "_current_user",
                return_value={"role": "Employee", "is_wfh_enabled": False},
            ),
            self.assertRaises(HTTPException) as error,
        ):
            attendance._require_wfh_employee("Bearer valid")

        self.assertEqual(error.exception.status_code, 403)
        self.assertIn("not enabled", error.exception.detail)

    def test_attendance_payload_cannot_select_identity_or_server_fields(self):
        payload = WfhAttendancePayload(
            employee_id="forged",
            attendance_source="MANUAL",
            face_verification_status="verified",
            latitude=0,
            longitude=0,
            accuracy_m=10,
            location_timestamp=self.fresh_location_timestamp(),
            challenge_id="single-use-challenge-id-123456",
            frames=["frame"] * 8,
        )

        self.assertFalse(hasattr(payload, "employee_id"))
        self.assertFalse(hasattr(payload, "attendance_source"))
        self.assertFalse(hasattr(payload, "face_verification_status"))

    def test_outside_location_cannot_create_wfh_attendance(self):
        user = {
            "_id": ObjectId(),
            "employee_id": "E-1",
            "home_latitude": 0.0,
            "home_longitude": 0.0,
            "allowed_radius": 100,
        }
        payload = WfhAttendancePayload(
            latitude=0.001214,
            longitude=0.0,
            accuracy_m=10,
            location_timestamp=self.fresh_location_timestamp(),
            challenge_id="single-use-challenge-id-123456",
            frames=["frame"] * 8,
        )
        with (
            patch.object(attendance, "_require_wfh_employee", return_value=user),
            patch.object(attendance, "_today_wfh_attendance", return_value=None),
            patch.object(attendance, "_approved_leaves_for_date", return_value=[]),
            patch.object(
                attendance,
                "_consume_wfh_face_challenge",
                return_value={"actions": ["blink", "turn_left"]},
            ),
            patch.object(attendance, "_decode_and_verify_face"),
            patch.object(attendance.attendance_collection, "insert_one") as insert,
        ):
            with self.assertRaises(HTTPException) as error:
                attendance.wfh_check_in(payload)

        self.assertEqual(error.exception.status_code, 403)
        insert.assert_not_called()

    def test_wfh_checkin_creates_common_attendance_record(self):
        user = {
            "_id": ObjectId(),
            "employee_id": "E-1",
            "name": "Employee",
            "home_latitude": 0.0,
            "home_longitude": 0.0,
            "allowed_radius": 100,
        }
        payload = WfhAttendancePayload(
            latitude=0.0004,
            longitude=0.0,
            accuracy_m=20,
            location_timestamp=self.fresh_location_timestamp(),
            challenge_id="single-use-challenge-id-123456",
            frames=["frame"] * 8,
        )
        record_id = ObjectId()
        response_record = {
            "_id": record_id,
            "attendance_date": attendance.company_today().isoformat(),
            "status": "Present",
            "check_in_time": "09:00",
            "check_out_time": None,
            "attendance_type": "WFH",
            "attendance_source": "WFH_FACE_GPS",
        }

        with (
            patch.object(attendance, "_require_wfh_employee", return_value=user),
            patch.object(
                attendance,
                "_validate_wfh_biometric_attendance",
                return_value=(44.5, 100.0, {}),
            ),
            patch.object(
                attendance.attendance_collection,
                "insert_one",
                return_value=SimpleNamespace(inserted_id=record_id),
            ) as insert,
            patch.object(
                attendance.attendance_collection,
                "find_one",
                return_value=response_record,
            ),
        ):
            response = attendance.wfh_check_in(payload)

        self.assertEqual(response["attendance_type"], "WFH")
        self.assertEqual(response["attendance_source"], "WFH_FACE_GPS")
        self.assertEqual(
            insert.call_args.args[0]["attendance_type"],
            "WFH",
        )

    def test_duplicate_wfh_check_in_is_rejected(self):
        user = {
            "_id": ObjectId(),
            "employee_id": "E-1",
            "home_latitude": 0.0,
            "home_longitude": 0.0,
            "allowed_radius": 100,
        }
        payload = WfhAttendancePayload(
            latitude=0.0,
            longitude=0.0,
            accuracy_m=10,
            location_timestamp=self.fresh_location_timestamp(),
            challenge_id="single-use-challenge-id-123456",
            frames=["frame"] * 8,
        )
        with (
            patch.object(attendance, "_require_wfh_employee", return_value=user),
            patch.object(
                attendance,
                "_today_wfh_attendance",
                return_value={
                    "attendance_source": "WFH_FACE_GPS",
                    "check_in_time": "09:10",
                },
            ),
            patch.object(attendance.attendance_collection, "insert_one") as insert,
        ):
            with self.assertRaises(HTTPException) as error:
                attendance.wfh_check_in(payload)

        self.assertEqual(error.exception.status_code, 409)
        self.assertIn("already checked in", error.exception.detail)
        insert.assert_not_called()

    def test_checkout_without_check_in_is_rejected(self):
        user = {
            "_id": ObjectId(),
            "employee_id": "E-1",
            "home_latitude": 0.0,
            "home_longitude": 0.0,
            "allowed_radius": 100,
        }
        payload = WfhAttendancePayload(
            latitude=0.0,
            longitude=0.0,
            accuracy_m=10,
            location_timestamp=self.fresh_location_timestamp(),
            challenge_id="single-use-challenge-id-123456",
            frames=["frame"] * 8,
        )
        with (
            patch.object(attendance, "_require_wfh_employee", return_value=user),
            patch.object(attendance, "_today_wfh_attendance", return_value=None),
            patch.object(attendance.attendance_collection, "update_one") as update,
        ):
            with self.assertRaises(HTTPException) as error:
                attendance.wfh_check_out(payload)

        self.assertEqual(error.exception.status_code, 409)
        self.assertIn("required before Check-Out", error.exception.detail)
        update.assert_not_called()

    def test_wfh_checkout_updates_common_attendance_record(self):
        user = {
            "_id": ObjectId(),
            "employee_id": "E-1",
            "name": "Employee",
            "home_latitude": 0.0,
            "home_longitude": 0.0,
            "allowed_radius": 100,
        }
        payload = WfhAttendancePayload(
            latitude=0.0,
            longitude=0.0,
            accuracy_m=10,
            location_timestamp=self.fresh_location_timestamp(),
            challenge_id="single-use-challenge-id-123456",
            frames=["frame"] * 8,
        )
        response_record = {
            "_id": ObjectId(),
            "attendance_date": attendance.company_today().isoformat(),
            "status": "Present",
            "check_in_time": "09:00",
            "check_out_time": "18:00",
            "attendance_type": "WFH",
            "attendance_source": "WFH_FACE_GPS",
        }
        with (
            patch.object(attendance, "_require_wfh_employee", return_value=user),
            patch.object(
                attendance,
                "_validate_wfh_biometric_attendance",
                return_value=(20.0, 100.0, {}),
            ),
            patch.object(
                attendance.attendance_collection,
                "update_one",
                return_value=SimpleNamespace(matched_count=1),
            ) as update,
            patch.object(
                attendance.attendance_collection,
                "find_one",
                return_value=response_record,
            ),
        ):
            response = attendance.wfh_check_out(payload)

        self.assertEqual(response["attendance_type"], "WFH")
        self.assertEqual(response["attendance_source"], "WFH_FACE_GPS")
        self.assertEqual(update.call_args.args[1]["$set"]["attendance_type"], "WFH")

    def test_face_failure_cannot_create_wfh_attendance(self):
        user = {"_id": ObjectId()}
        payload = WfhAttendancePayload(
            latitude=0.0,
            longitude=0.0,
            accuracy_m=10,
            location_timestamp=self.fresh_location_timestamp(),
            challenge_id="single-use-challenge-id-123456",
            frames=["frame"] * 8,
        )
        with (
            patch.object(attendance, "_require_wfh_employee", return_value=user),
            patch.object(
                attendance,
                "_validate_wfh_biometric_attendance",
                side_effect=HTTPException(
                    422,
                    "Face verification failed. Please try again.",
                ),
            ),
            patch.object(attendance.attendance_collection, "insert_one") as insert,
        ):
            with self.assertRaises(HTTPException) as error:
                attendance.wfh_check_in(payload)

        self.assertEqual(error.exception.status_code, 422)
        self.assertIn("Face verification failed", error.exception.detail)
        insert.assert_not_called()


if __name__ == "__main__":
    unittest.main()
