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



def test_starting_date_change_is_saved_even_when_schedule_times_are_unchanged():
    existing = {
        "working_type": "Full Time",
        "scheduled_work_start_time": "09:00",
        "scheduled_work_end_time": "18:00",
        "scheduled_work_duration": 540,
        "half_day_working_duration": 270,
        "schedule_effective_from": "2026-01-01",
        "working_schedule_history": [],
    }
    saved = record_schedule_change(
        existing,
        {
            "working_type": "Full Time",
            "scheduled_work_start_time": "09:00",
            "scheduled_work_end_time": "18:00",
        },
        date(2026, 3, 1),
        force_effective_date=True,
    )

    assert saved["schedule_effective_from"] == "2026-03-01"
    assert saved["working_schedule_history"][0]["effective_from"] == "2026-01-01"
    assert saved["working_schedule_history"][0]["effective_to"] == "2026-02-28"

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



def test_applicable_schedule_preserves_stored_half_day_threshold_text():
    from app.working_details import applicable_schedule

    details = {
        "working_type": "Full Time",
        "scheduled_work_start_time": "",
        "scheduled_work_end_time": "",
        "scheduled_work_duration": 0,
        "half_day_working_duration": 240,
        "schedule_effective_from": "2026-01-01",
        "working_schedule_history": [],
    }
    schedule = applicable_schedule(
        "employee-1",
        date(2026, 10, 10),
        details=details,
    )
    assert schedule["half_day_working_duration"] == 240
    assert schedule["half_day_working_duration_text"] == "04 Hours 00 Minutes"
    assert schedule["scheduled_work_duration_text"] == ""


def test_historical_schedule_text_uses_selected_history_threshold():
    from app.working_details import applicable_schedule

    details = {
        "working_type": "Full Time",
        "scheduled_work_start_time": "10:00",
        "scheduled_work_end_time": "18:00",
        "scheduled_work_duration": 480,
        "half_day_working_duration": 240,
        "schedule_effective_from": "2026-11-05",
        "working_schedule_history": [{
            "working_type": "Full Time",
            "scheduled_work_start_time": "09:00",
            "scheduled_work_end_time": "18:30",
            "scheduled_work_duration": 570,
            "half_day_working_duration": 270,
            "effective_from": "2026-01-01",
            "effective_to": "2026-11-04",
        }],
    }
    schedule = applicable_schedule(
        "employee-1",
        date(2026, 10, 10),
        details=details,
    )
    assert schedule["scheduled_work_start_time"] == "09:00"
    assert schedule["scheduled_work_end_time"] == "18:30"
    assert schedule["half_day_working_duration"] == 270
    assert schedule["half_day_working_duration_text"] == "04 Hours 30 Minutes"


def test_inserting_past_effective_date_preserves_later_schedule():
    from app.working_details import record_schedule_change

    existing = {
        "working_type": "Full Time",
        "scheduled_work_start_time": "10:00",
        "scheduled_work_end_time": "19:00",
        "scheduled_work_duration": 540,
        "half_day_working_duration": 270,
        "schedule_effective_from": "2026-11-15",
        "date_of_joining": "2026-01-01",
        "working_schedule_history": [{
            "working_type": "Full Time",
            "scheduled_work_start_time": "09:00",
            "scheduled_work_end_time": "18:00",
            "scheduled_work_duration": 540,
            "half_day_working_duration": 270,
            "effective_from": "2026-01-01",
            "effective_to": "2026-11-14",
        }],
    }
    updated = record_schedule_change(existing, {
        "working_type": "Part Time",
        "scheduled_work_start_time": "08:00",
        "scheduled_work_end_time": "12:00",
    }, date(2026, 10, 1))

    assert updated["schedule_effective_from"] == "2026-11-15"
    assert updated["scheduled_work_start_time"] == "10:00"
    assert len(updated["working_schedule_history"]) == 2
    assert updated["working_schedule_history"][0]["effective_from"] == "2026-01-01"
    assert updated["working_schedule_history"][0]["effective_to"] == "2026-09-30"
    assert updated["working_schedule_history"][1]["effective_from"] == "2026-10-01"
    assert updated["working_schedule_history"][1]["effective_to"] == "2026-11-14"


def test_schedule_effective_date_can_precede_joining_date_and_account_creation():
    from app.working_details import record_schedule_change

    saved = record_schedule_change(
        {
            "date_of_joining": "2026-05-01",
            "created_at": "2026-11-10T08:00:00+00:00",
        },
        {
            "working_type": "Full Time",
            "scheduled_work_start_time": "09:00",
            "scheduled_work_end_time": "18:00",
        },
        date(2026, 1, 1),
    )

    assert saved["schedule_effective_from"] == "2026-01-01"
    assert saved["scheduled_work_start_time"] == "09:00"
