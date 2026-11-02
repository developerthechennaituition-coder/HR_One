from datetime import date

from app.working_details import (
    format_hours_minutes,
    record_schedule_change,
    schedule_duration,
)


def test_scheduled_duration_supports_full_time_and_part_time():
    assert schedule_duration("09:00", "18:30") == 570
    assert schedule_duration("09:00", "13:00") == 240
    assert format_hours_minutes(570 // 2) == "04 Hours 45 Minutes"


def test_scheduled_duration_supports_overnight_shifts():
    assert schedule_duration("22:00", "06:00") == 480


def test_identical_schedule_times_are_invalid_duration():
    assert schedule_duration("09:00", "09:00") == 0


def test_unchanged_schedule_keeps_historical_effective_date():
    existing = {
        "working_type": "Full Time",
        "scheduled_work_start_time": "09:00",
        "scheduled_work_end_time": "18:30",
        "schedule_effective_from": "2026-01-01",
    }
    saved = record_schedule_change(
        existing,
        {
            "working_type": "Full Time",
            "scheduled_work_start_time": "09:00",
            "scheduled_work_end_time": "18:30",
        },
        date(2026, 11, 2),
    )
    assert saved["schedule_effective_from"] == "2026-01-01"
    assert saved["working_schedule_history"] == []


def test_schedule_change_closes_previous_schedule_without_rewriting_it():
    existing = {
        "working_type": "Full Time",
        "scheduled_work_start_time": "09:00",
        "scheduled_work_end_time": "18:30",
        "schedule_effective_from": "2026-01-01",
    }
    saved = record_schedule_change(
        existing,
        {
            "working_type": "Part Time",
            "scheduled_work_start_time": "10:00",
            "scheduled_work_end_time": "15:00",
        },
        date(2026, 11, 2),
    )
    assert saved["schedule_effective_from"] == "2026-11-02"
    assert saved["scheduled_work_duration"] == 300
    assert saved["half_day_working_duration"] == 150
    assert saved["working_schedule_history"] == [{
        "working_type": "Full Time",
        "scheduled_work_start_time": "09:00",
        "scheduled_work_end_time": "18:30",
        "scheduled_work_duration": 570,
        "half_day_working_duration": 285,
        "effective_from": "2026-01-01",
        "effective_to": "2026-11-01",
    }]
