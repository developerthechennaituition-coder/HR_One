import unittest
from datetime import date

from app.working_details import record_schedule_change

CURRENT = {
    "working_type": "Full Time",
    "scheduled_work_start_time": "09:00",
    "scheduled_work_end_time": "18:00",
    "schedule_effective_from": "2026-11-01",
    "working_schedule_history": [],
}


def values(start, end):
    return {"working_type": "Full Time", "scheduled_work_start_time": start, "scheduled_work_end_time": end}


class WorkingDetailsEditTests(unittest.TestCase):
    def save(self, start, end, day, force=True):
        return record_schedule_change(CURRENT, values(start, end), date.fromisoformat(day), force_effective_date=force)

    def test_times_changed_on_same_date_are_saved(self):
        result = self.save("10:00", "19:00", "2026-11-01", force=False)
        self.assertEqual((result["scheduled_work_start_time"], result["scheduled_work_end_time"]), ("10:00", "19:00"))

    def test_times_changed_with_earlier_starting_date_are_saved(self):
        result = self.save("10:00", "19:00", "2026-10-15")
        self.assertEqual(result["scheduled_work_start_time"], "10:00")
        self.assertEqual(result["schedule_effective_from"], "2026-10-15")

    def test_starting_date_moved_earlier_is_saved(self):
        result = self.save("09:00", "18:00", "2026-10-15")
        self.assertEqual(result["schedule_effective_from"], "2026-10-15")
        self.assertEqual(result["working_schedule_history"], [])

    def test_starting_date_moved_later_is_saved(self):
        result = self.save("09:00", "18:00", "2026-11-15")
        self.assertEqual(result["schedule_effective_from"], "2026-11-15")

    def test_later_effective_date_keeps_previous_schedule_in_history(self):
        result = self.save("10:00", "19:00", "2026-11-15")
        self.assertEqual(result["schedule_effective_from"], "2026-11-15")
        self.assertEqual(result["working_schedule_history"][0]["effective_to"], "2026-11-14")


if __name__ == "__main__":
    unittest.main()
