import unittest
from datetime import datetime
from time import time
from unittest.mock import patch
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from app import attendance, leave


class WfhTimestampAndIstTests(unittest.TestCase):
    def test_naive_mongo_datetime_is_serialised_as_utc_and_converts_to_ist(self):
        value = attendance._utc_iso(datetime(2026, 10, 6, 8, 0, 0))
        self.assertTrue(value.endswith("+00:00"))
        ist = datetime.fromisoformat(value).astimezone(ZoneInfo("Asia/Kolkata"))
        self.assertEqual((ist.hour, ist.minute), (13, 30))

    def test_small_clock_skew_is_tolerated_but_large_is_not(self):
        attendance._validate_wfh_location_timestamp(time() * 1000 + 60_000)
        with self.assertRaises(HTTPException) as error:
            attendance._validate_wfh_location_timestamp(time() * 1000 + 300_000)
        self.assertEqual(error.exception.status_code, 422)

    def test_reading_age_limit_applies_to_server_aligned_timestamps(self):
        attendance._validate_wfh_location_timestamp(time() * 1000 - 100_000)
        with self.assertRaises(HTTPException) as error:
            attendance._validate_wfh_location_timestamp(time() * 1000 - 130_000)
        self.assertIn("outdated", error.exception.detail)

    def test_device_measured_age_is_independent_of_clock_skew(self):
        # Timestamp 10 minutes off (wrong device clock) but the reading is 5 s old.
        attendance._validate_wfh_location_timestamp(time() * 1000 - 600_000, 5_000)
        with self.assertRaises(HTTPException) as error:
            attendance._validate_wfh_location_timestamp(time() * 1000, 130_000)
        self.assertIn("outdated", error.exception.detail)


class LeaveCancelDeadlineTests(unittest.TestCase):
    ist = ZoneInfo("Asia/Kolkata")

    def record(self, from_date):
        return {"status": "approved", "user_id": "u1", "from_date": from_date}

    def test_deadline_enforced_for_employee_and_own_hr_requests(self):
        now = datetime(2026, 11, 14, 10, 0, tzinfo=self.ist)
        with patch.object(leave, "company_now", return_value=now), \
             patch.object(leave, "_is_admin_or_hr", side_effect=lambda u: u["hr"]):
            self.assertTrue(leave._can_cancel(self.record("2026-11-15"), {"_id": "u1", "hr": False}))
            self.assertFalse(leave._can_cancel(self.record("2026-11-14"), {"_id": "u1", "hr": False}))
            self.assertFalse(leave._can_cancel(self.record("2026-11-14"), {"_id": "u1", "hr": True}))
            self.assertTrue(leave._can_cancel(self.record("2026-11-14"), {"_id": "hr", "hr": True}))


if __name__ == "__main__":
    unittest.main()
