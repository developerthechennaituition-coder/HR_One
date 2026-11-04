from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "app" / "attendance_calculations.py"
SPEC = spec_from_file_location("attendance_calculations_under_test", MODULE_PATH)
calc = module_from_spec(SPEC)
SPEC.loader.exec_module(calc)


def _classify(check_in, check_out, total=192, **overrides):
    args = {
        "total_duration_minutes": total,
        "half_day_threshold_minutes": 240,
        "scheduled_start": "09:00",
        "scheduled_end": "18:00",
        "check_in": check_in,
        "check_out": check_out,
        "day_completed": True,
        "base_status": "Present",
        "working_intervals": ([{"from_time": check_in, "to_time": check_out}] if check_in and check_out else []),
    }
    args.update(overrides)
    return calc.classify_half_day(**args)


def test_total_duration_handles_normal_and_overnight_shifts():
    assert calc.duration_minutes("09:00", "18:00") == 540
    assert calc.duration_minutes("10:15 AM", "06:45 PM") == 510
    assert calc.duration_minutes("22:00", "06:00") == 480
    assert calc.duration_minutes("09:00", None) is None


def test_first_half_present_is_detected_from_schedule_boundary():
    result = _classify("09:00", "12:00")
    assert result["half_day_present"] == "Yes – First Half"
    assert result["first_half_status"] == "Present"
    assert result["second_half_status"] == "Absent"


def test_second_half_present_is_detected_from_schedule_boundary():
    result = _classify("15:00", "18:00")
    assert result["half_day_present"] == "Yes – Second Half"
    assert result["first_half_status"] == "Absent"
    assert result["second_half_status"] == "Present"


def test_no_half_day_classification_while_day_is_in_progress():
    result = _classify("09:00", None, day_completed=False)
    assert result["half_day_present"] == "No"
    assert result["half_day_review_required"] is True


def test_approved_first_half_leave_and_second_half_attendance():
    result = _classify("15:00", "18:00", leave_day_part="first_half")
    assert result["first_half_status"] == "Leave"
    assert result["second_half_status"] == "Present"
    assert result["half_day_present"] == "Yes – Second Half"


def test_approved_second_half_leave_without_attendance_marks_first_half_absent():
    result = _classify(None, None, total=0, leave_day_part="second_half")
    assert result["first_half_status"] == "Absent"
    assert result["second_half_status"] == "Leave"
    assert result["half_day_present"] == "No"


def test_equal_schedule_distances_require_review_not_guess():
    result = _classify("12:00", "15:00", working_intervals=[])
    assert result["half_day_present"] == "No"
    assert result["half_day_review_required"] is True


def test_weekly_off_selection_spreads_four_dates_across_month_weeks():
    from datetime import date, timedelta
    candidates = [date(2026, 10, 1) + timedelta(days=offset) for offset in range(31)]
    selected = calc.select_weekly_off_dates(candidates, 4, {6})
    assert len(selected) == 4
    assert len({item.isocalendar()[:2] for item in selected}) == 4
    assert sum(item.weekday() == 6 for item in selected) >= 3


def test_weekly_off_selection_never_exceeds_available_dates_or_four():
    from datetime import date, timedelta
    candidates = [date(2026, 10, 1) + timedelta(days=offset) for offset in range(3)]
    assert len(calc.select_weekly_off_dates(candidates, 4)) == 3
    assert len(calc.select_weekly_off_dates(candidates, 10)) == 3
    assert calc.select_weekly_off_dates(candidates, 0) == []


def test_half_day_leave_does_not_mark_remaining_half_absent_before_day_completion():
    result = _classify(None, None, total=0, day_completed=False, leave_day_part="first_half")
    assert result["first_half_status"] == "Leave"
    assert result["second_half_status"] == "Not Marked"
    assert result["half_day_present"] == "No"


def test_half_day_threshold_uses_inclusive_eighty_to_one_hundred_fifty_percent():
    below = _classify("09:00", "12:00", total=191)
    at_lower = _classify("09:00", "12:00", total=192)
    at_upper = _classify("09:00", "12:00", total=360)
    above = _classify("09:00", "12:00", total=361)
    assert below["half_day_present"] == "No"
    assert below["first_half_status"] == "Absent"
    assert at_lower["half_day_present"] == "Yes – First Half"
    assert at_upper["half_day_present"] == "Yes – First Half"
    assert above["half_day_present"] == "No"
    assert above["first_half_status"] == "Present"
    assert above["second_half_status"] == "Present"


def test_permission_only_adds_minutes_outside_total_duration():
    assert calc.permission_minutes_outside_total_duration([("10:00", "11:00")], "09:00", "17:00") == 0
    assert calc.permission_minutes_outside_total_duration([("08:00", "09:00")], "09:00", "17:00") == 60
    assert calc.permission_minutes_outside_total_duration([("08:30", "09:30")], "09:00", "17:00") == 30


def test_attendance_issue_is_derived_from_incomplete_or_overlapping_intervals():
    assert calc.attendance_issue("09:00", None)["has_attendance_issue"] is True
    assert calc.attendance_issue("09:00", "17:00", [
        {"from_time": "09:00", "to_time": "12:00"},
        {"from_time": "11:00", "to_time": "13:00"},
    ])["has_attendance_issue"] is True
    assert calc.attendance_issue("09:00", "17:00", [
        {"from_time": "09:00", "to_time": "12:00"},
        {"from_time": "13:00", "to_time": "17:00"},
    ])["has_attendance_issue"] is False
