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


def test_work_balanced_across_midpoint_is_half_day_present_without_review():
    # 12:00-15:00 straddles the 13:30 midpoint of a 09:00-18:00 shift equally.
    # It is within 50%-120% of Half Day Time, so it must be a finalized
    # Half-Day Present (deterministic side), never Absent / HR review.
    result = _classify("12:00", "15:00", total=180, working_intervals=[])
    assert result["half_day_present"] == "Yes – First Half"
    assert result["first_half_status"] == "Present"
    assert result["second_half_status"] == "Absent"
    assert result["half_day_review_required"] is False


def test_work_outside_scheduled_hours_is_attributed_without_review():
    result = _classify("19:00", "22:00", total=180, working_intervals=[])
    assert result["half_day_present"] == "Yes – Second Half"
    assert result["half_day_review_required"] is False


def test_approved_permission_counts_toward_the_half_it_covers():
    # Worked 11:00-15:00 (balanced), approved permission 09:00-11:00 is first half.
    result = _classify("11:00", "15:00", total=240, working_intervals=[],
                       permission_intervals=[("09:00", "11:00")])
    assert result["half_day_present"] == "Yes – First Half"
    # Permission already covered by actual working intervals is not counted twice.
    covered = _classify("09:00", "11:00", total=120,
                        permission_intervals=[("09:00", "11:00")])
    assert covered["half_day_present"] == "Yes – First Half"


def test_completed_day_with_a_missing_punch_still_requires_review():
    result = _classify("09:00", None, total=0, working_intervals=[])
    assert result["half_day_review_required"] is True
    assert result["half_day_present"] == "No"


def test_no_configured_half_day_time_does_not_force_hr_review():
    result = _classify("09:00", "18:00", total=540, half_day_threshold_minutes=0)
    assert result["half_day_review_required"] is False
    assert result["half_day_present"] == "No"


def test_boundaries_are_derived_from_each_employees_half_day_time():
    # Half Day Time 270 (09:00-18:00 shift): 50% = 135, 120% = 324.
    cases = {134: "absent", 135: "half", 136: "half", 324: "half", 325: "full"}
    for total, expected in cases.items():
        assert calc.half_day_band(total, 270) == expected, total
    # A different Half Day Time moves every boundary: 333 -> 166.5 and 399.6.
    assert calc.half_day_band(166, 333) == "absent"
    assert calc.half_day_band(167, 333) == "half"
    assert calc.half_day_band(399, 333) == "half"
    assert calc.half_day_band(400, 333) == "full"
    # 225 -> exactly 112.5 / 270.
    assert calc.half_day_band(112, 225) == "absent"
    assert calc.half_day_band(113, 225) == "half"
    assert calc.half_day_band(270, 225) == "half"
    assert calc.half_day_band(271, 225) == "full"
    assert calc.half_day_band(100, 0) is None
    assert calc.half_day_band(None, 270) is None


def test_every_threshold_boundary_through_classify_half_day():
    def run(total):
        return _classify("09:00", "13:30", total=total, half_day_threshold_minutes=270,
                         working_intervals=[{"from_time": "09:00", "to_time": "13:30"}])
    below = run(134)
    exactly_50 = run(135)
    between = run(200)
    exactly_120 = run(324)
    above = run(325)
    assert (below["first_half_status"], below["second_half_status"]) == ("Absent", "Absent")
    assert below["half_day_present"] == "No"
    assert exactly_50["half_day_present"] == "Yes – First Half"
    assert between["half_day_present"] == "Yes – First Half"
    assert exactly_120["half_day_present"] == "Yes – First Half"
    assert above["half_day_present"] == "No"
    assert (above["first_half_status"], above["second_half_status"]) == ("Present", "Present")
    for result in (below, exactly_50, between, exactly_120, above):
        assert result["half_day_review_required"] is False


def test_total_duration_not_net_working_time_decides_the_band():
    # 5 hours elapsed (300 min) with only 100 net working minutes is still 'half'.
    result = _classify("09:00", "14:00", total=300, half_day_threshold_minutes=270,
                       working_intervals=[{"from_time": "09:00", "to_time": "10:40"}])
    assert result["half_day_present"] == "Yes – First Half"


def test_stale_automatic_status_is_replaced_only_when_recalculated():
    kept = _classify("09:00", "18:00", total=540, half_day_threshold_minutes=270, base_status="Absent")
    assert kept["first_half_status"] == "Absent"
    fixed = _classify("09:00", "18:00", total=540, half_day_threshold_minutes=270,
                      base_status="Absent", recalculated_base=True)
    assert (fixed["first_half_status"], fixed["second_half_status"]) == ("Present", "Present")
    override = calc.half_day_status_override(
        fixed, total_duration_minutes=540, half_day_threshold_minutes=270,
        check_in="09:00", check_out="18:00", day_completed=True,
        current_status="Absent", recalculated=True)
    assert override == "Present"
    assert calc.half_day_status_override(
        fixed, total_duration_minutes=540, half_day_threshold_minutes=270,
        check_in="09:00", check_out="18:00", day_completed=True,
        current_status="Absent", recalculated=False) is None
    assert calc.half_day_status_override(
        {}, total_duration_minutes=100, half_day_threshold_minutes=270,
        check_in="09:00", check_out="10:40", day_completed=True,
        current_status="Holiday", recalculated=True) is None
    assert calc.half_day_status_override(
        {}, total_duration_minutes=100, half_day_threshold_minutes=270,
        check_in="09:00", check_out="10:40", day_completed=True,
        current_status="Present") == "Absent"


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


def test_half_day_threshold_uses_inclusive_fifty_to_one_hundred_twenty_percent():
    below = _classify("09:00", "12:00", total=119)
    at_lower = _classify("09:00", "11:00", total=120)
    at_upper = _classify("09:00", "12:00", total=288)
    above = _classify("09:00", "12:00", total=289)
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


def test_half_day_classification_uses_total_duration_even_when_net_working_time_is_below_threshold():
    # H=240 minutes; Total Duration=240 minutes; Break Time=130 minutes;
    # Net Working Time=110 minutes. The threshold comparison must use 240.
    result = _classify("09:00", "13:00", total=240)
    assert result["half_day_present"] == "Yes – First Half"
    assert result["first_half_status"] == "Present"
    assert result["second_half_status"] == "Absent"


def test_half_day_threshold_policy_examples_from_seventy_to_one_hundred_twenty_percent():
    # The configured half-day duration is 240 minutes (4 hours).
    cases = [
        (101, False),  # below 50%
        (120, True),   # exactly 50%
        (180, True),   # 75%
        (192, True),   # 80%
        (240, True),   # 100%
        (264, True),   # 110%
        (288, True),   # exactly 120%
        (289, False),  # above 120%
    ]
    for minutes, qualifies in cases:
        result = _classify("09:00", "12:00", total=minutes)
        assert result["half_day_present"].startswith("Yes") is qualifies


def test_half_day_portions_preserve_both_statuses_and_fractional_result():
    result = calc.calculate_half_day_portions(
        first_half_status="Present",
        second_half_status="Leave",
        first_half_check_in_time="09:00",
        first_half_check_out_time="12:00",
        half_day_threshold_minutes=240,
        scheduled_start="09:00",
        scheduled_end="18:00",
    )
    assert result["status"] == "Half-Day Present"
    assert result["first_half_status"] == "Present"
    assert result["second_half_status"] == "Leave"
    assert result["half_day_present"] == "Yes – First Half"
    assert result["actual_working_minutes"] == 180


def test_half_day_portions_accept_inclusive_seventy_and_one_hundred_twenty_percent():
    for check_out in ("11:48", "13:48"):
        result = calc.calculate_half_day_portions(
            first_half_status="Present",
            second_half_status="Absent",
            first_half_check_in_time="09:00",
            first_half_check_out_time=check_out,
            half_day_threshold_minutes=240,
            scheduled_start="09:00",
            scheduled_end="18:00",
        )
        assert result["half_day_eligibility"] == "Eligible"


def test_half_day_portions_reject_ineligible_or_overlapping_times():
    args = {
        "first_half_status": "Present",
        "second_half_status": "Absent",
        "first_half_check_in_time": "09:00",
        "first_half_check_out_time": "10:59",
        "half_day_threshold_minutes": 240,
        "scheduled_start": "09:00",
        "scheduled_end": "18:00",
    }
    import pytest
    with pytest.raises(ValueError, match="50%–120%"):
        calc.calculate_half_day_portions(**args)
    with pytest.raises(ValueError, match="cannot overlap"):
        calc.calculate_half_day_portions(
            first_half_status="Present",
            second_half_status="Present",
            first_half_check_in_time="09:00",
            first_half_check_out_time="13:00",
            second_half_check_in_time="12:00",
            second_half_check_out_time="16:00",
            half_day_threshold_minutes=240,
            scheduled_start="09:00",
            scheduled_end="18:00",
        )


def test_duration_minutes_handles_overnight_clock_values():
    assert calc.duration_minutes("22:30", "06:15") == 465
    assert calc.duration_minutes("09:00", "18:00") == 540
    assert calc.duration_minutes("09:00", None) is None
