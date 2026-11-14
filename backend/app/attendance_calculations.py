"""Shared, side-effect-free attendance duration and half-day calculations.

All durations are elapsed wall-clock minutes.  The module intentionally does not
import FastAPI or MongoDB so the business rules can be reused and unit-tested.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Any


def duration_minutes(check_in: Any, check_out: Any) -> int | None:
    """Elapsed minutes between two clock/datetime values; None if incomplete."""
    if check_in in (None, "") or check_out in (None, ""):
        return None
    if isinstance(check_in, datetime) and isinstance(check_out, datetime):
        start, end = check_in, check_out
        if start.tzinfo and end.tzinfo:
            return max(0, int((end - start).total_seconds() // 60))
        if end < start:
            # Naive datetime pairs may represent an overnight shift.
            from datetime import timedelta
            end = end + timedelta(days=1)
        return max(0, int((end - start).total_seconds() // 60))
    start = _clock_minutes(check_in)
    end = _clock_minutes(check_out)
    if start is None or end is None:
        return None
    # A checkout earlier than check-in is an overnight shift.
    if end < start:
        end += 1440
    return max(0, end - start)


def _clock_minutes(value: Any) -> int | None:
    if isinstance(value, datetime):
        return value.hour * 60 + value.minute
    if isinstance(value, time):
        return value.hour * 60 + value.minute
    raw = str(value or "").strip()
    if not raw:
        return None
    # ISO datetime values from attendance calculations.
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed.hour * 60 + parsed.minute
    except ValueError:
        pass
    match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::\d{2})?\s*(AM|PM)?", raw, re.I)
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    suffix = (match.group(3) or "").upper()
    if minute > 59 or hour > (12 if suffix else 23) or hour < (1 if suffix else 0):
        return None
    if suffix:
        hour = hour % 12 + (12 if suffix == "PM" else 0)
    return hour * 60 + minute


def effective_working_minutes(working_minutes: int | None, permission_minutes: int | None = 0, *, permission_already_included: bool = False) -> int:
    """Return net worked minutes plus approved permission exactly once."""
    worked = max(0, int(working_minutes or 0))
    permission = max(0, int(permission_minutes or 0))
    return worked if permission_already_included else worked + permission


def union_interval_minutes(intervals: list[tuple[Any, Any]] | None) -> int:
    """Sum time intervals without counting overlapping minutes twice."""
    normalized: list[tuple[int, int]] = []
    for start_value, end_value in intervals or []:
        start = _clock_minutes(start_value)
        end = _clock_minutes(end_value)
        if start is None or end is None:
            continue
        if end <= start:
            end += 1440
        normalized.append((start, end))
    normalized.sort()
    total = 0
    current_start = current_end = None
    for start, end in normalized:
        if current_start is None:
            current_start, current_end = start, end
        elif start <= current_end:
            current_end = max(current_end, end)
        else:
            total += current_end - current_start
            current_start, current_end = start, end
    if current_start is not None:
        total += current_end - current_start
    return max(0, total)


def additional_interval_minutes(permission_intervals: list[tuple[Any, Any]] | None,
                                working_intervals: list[dict[str, Any]] | None = None) -> int:
    """Permission minutes not already covered by actual working intervals."""
    def to_minutes(value: Any) -> int | None:
        if isinstance(value, (int, float)):
            return int(value)
        return _clock_minutes(value)
    permissions: list[tuple[int, int]] = []
    for start_value, end_value in permission_intervals or []:
        start, end = to_minutes(start_value), to_minutes(end_value)
        if start is None or end is None:
            continue
        if end <= start:
            end += 1440
        permissions.append((start, end))
    covered: list[tuple[int, int]] = []
    for item in working_intervals or []:
        start = to_minutes(item.get("from_time") or item.get("start"))
        end = to_minutes(item.get("to_time") or item.get("end"))
        if start is None or end is None:
            continue
        if end <= start:
            end += 1440
        covered.append((start, end))
    total = 0
    for start, end in permissions:
        segments = [(start, end)]
        for cover_start, cover_end in covered:
            next_segments = []
            for seg_start, seg_end in segments:
                if cover_end <= seg_start or cover_start >= seg_end:
                    next_segments.append((seg_start, seg_end))
                else:
                    if seg_start < cover_start:
                        next_segments.append((seg_start, cover_start))
                    if cover_end < seg_end:
                        next_segments.append((cover_end, seg_end))
            segments = next_segments
        total += sum(max(0, b - a) for a, b in segments)
    return max(0, total)


def permission_minutes_outside_total_duration(permission_intervals: list[tuple[Any, Any]] | None,
                                              check_in: Any, check_out: Any) -> int:
    """Count only approved permission minutes outside the elapsed check-in/out span.

    A permission interval wholly or partly inside Total Duration is not added again.
    Multiple overlapping permission intervals are unioned before counting.
    """
    span_start = _clock_minutes(check_in)
    span_end = _clock_minutes(check_out)
    if span_start is None or span_end is None:
        # Without both punches there is no reliable elapsed span to compare against.
        return 0
    if span_end <= span_start:
        span_end += 1440
    raw: list[tuple[int, int]] = []
    for start_value, end_value in permission_intervals or []:
        start = int(start_value) if isinstance(start_value, (int, float)) else _clock_minutes(start_value)
        end = int(end_value) if isinstance(end_value, (int, float)) else _clock_minutes(end_value)
        if start is None or end is None:
            continue
        if end <= start:
            end += 1440
        raw.append((start, end))
    raw.sort()
    merged: list[tuple[int, int]] = []
    for start, end in raw:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    outside = 0
    for start, end in merged:
        # Test adjacent-day representations and use the one with the greatest
        # overlap with the actual elapsed span (equivalently, least outside time).
        candidates = []
        for offset in (-1440, 0, 1440):
            a, b = start + offset, end + offset
            overlap = max(0, min(b, span_end) - max(a, span_start))
            candidates.append((max(0, b - a - overlap), overlap))
        outside += min(candidates, key=lambda item: item[0])[0]
    return max(0, outside)


def attendance_issue(
    check_in: Any,
    check_out: Any,
    working_intervals: list[dict[str, Any]] | None = None,
    *,
    attendance_date: Any = None,
    now: datetime | None = None,
    scheduled_start: Any = None,
    scheduled_end: Any = None,
) -> dict[str, Any]:
    """Describe incomplete/inconsistent punches without changing attendance status.

    When ``attendance_date`` and ``now`` are given, a Check-In without a Check-Out
    is only reported once the scheduled work end time has passed (the next day for
    an overnight shift). Without them the previous, time-agnostic behaviour applies.
    """
    issues: list[str] = []
    if bool(check_in) != bool(check_out):
        if not check_in:
            issues.append("Missing check-in time")
        elif attendance_date is None or now is None or missing_checkout_issue(
            status="Present", check_in=check_in, check_out=check_out, attendance_date=attendance_date,
            now=now, scheduled_start=scheduled_start, scheduled_end=scheduled_end,
        ):
            issues.append("Missing check-out time")
    intervals = working_intervals or []
    normalized: list[tuple[int, int]] = []
    for interval in intervals:
        start_value = interval.get("from_time") or interval.get("start")
        end_value = interval.get("to_time") or interval.get("end")
        if bool(start_value) != bool(end_value):
            issues.append("Incomplete working interval")
            break
        if start_value and end_value:
            start = _clock_minutes(start_value)
            end = _clock_minutes(end_value)
            if start is None or end is None:
                issues.append("Invalid working interval")
                break
            if end <= start:
                end += 1440
            normalized.append((start, end))
    if normalized:
        normalized.sort()
        if any(current[0] < previous[1] for previous, current in zip(normalized, normalized[1:])):
            issues.append("Overlapping working intervals")
    return {"has_attendance_issue": bool(issues), "attendance_issue_details": issues}


ATTENDANCE_ISSUE_STATUS = "Attendance Issue"
# Statuses that may turn into an Attendance Issue when the day ended without a
# Check-Out. Leave, holiday, weekly off, half-day and manual Absent are never touched.
_ISSUE_ELIGIBLE_STATUSES = frozenset({"present", "attendance issue"})


def is_attendance_issue_status(status: Any) -> bool:
    return str(status or "").strip().casefold() == "attendance issue"


def missing_checkout_deadline(
    attendance_date: Any,
    scheduled_start: Any = None,
    scheduled_end: Any = None,
    tzinfo: Any = None,
) -> datetime | None:
    """Moment after which a missing Check-Out becomes an Attendance Issue.

    * Normal shift (end > start): the scheduled end time on the attendance date.
    * Overnight shift (end <= start): the scheduled end time on the NEXT day.
    * No usable schedule: the end of the attendance date (previous behaviour);
      no default shift is invented.
    """
    try:
        day = attendance_date if isinstance(attendance_date, date) and not isinstance(attendance_date, datetime) \
            else date.fromisoformat(str(attendance_date)[:10])
    except (TypeError, ValueError):
        return None
    end_min = _clock_minutes(scheduled_end)
    if end_min is None:
        return datetime.combine(day + timedelta(days=1), time.min, tzinfo=tzinfo)
    start_min = _clock_minutes(scheduled_start)
    overnight = start_min is not None and end_min <= start_min
    base = day + timedelta(days=1) if overnight else day
    return datetime.combine(base, time.min, tzinfo=tzinfo) + timedelta(minutes=end_min)


def missing_checkout_issue(
    *,
    status: Any,
    check_in: Any,
    check_out: Any,
    attendance_date: Any,
    now: datetime,
    scheduled_start: Any = None,
    scheduled_end: Any = None,
) -> bool:
    """True when the employee checked in, never checked out and the scheduled
    work end time has already passed.

    Until the scheduled end time (on the same day, or the following day for an
    overnight shift) the attendance is still open and is never an issue.
    """
    if str(status or "").strip().casefold() not in _ISSUE_ELIGIBLE_STATUSES:
        return False
    if not check_in or check_out:
        return False
    deadline = missing_checkout_deadline(attendance_date, scheduled_start, scheduled_end, now.tzinfo)
    if deadline is None:
        return False
    return now >= deadline


def effective_attendance_status(
    status: Any,
    *,
    check_in: Any,
    check_out: Any,
    attendance_date: Any,
    now: datetime,
    scheduled_start: Any = None,
    scheduled_end: Any = None,
) -> str:
    """Status used by every view/count: Present without a Check-Out is an Attendance Issue.

    A stored Attendance Issue whose Check-Out now exists reverts to Present.
    """
    text = str(status or "").strip()
    if missing_checkout_issue(
        status=text, check_in=check_in, check_out=check_out, attendance_date=attendance_date,
        now=now, scheduled_start=scheduled_start, scheduled_end=scheduled_end,
    ):
        return ATTENDANCE_ISSUE_STATUS
    if is_attendance_issue_status(text) and check_in:
        # Check-Out now exists, or the scheduled end time has not passed yet
        # (e.g. a stale stored issue): the day is a normal Present day.
        return "Present"
    return text


# Half-day thresholds, as percentages of the employee's configured Half Day Time.
# They are applied to Total Duration (Check-Out minus Check-In, breaks included).
HALF_DAY_ABSENT_BELOW_PERCENT = 50
HALF_DAY_FULL_ABOVE_PERCENT = 120

PROTECTED_DAY_STATUSES = frozenset({
    "holiday", "weekly off", "weekly-off", "week off", "not applicable",
    "before joining", "leave", "medical leave", "permission",
})
_RECALCULABLE_STATUSES = frozenset({
    "", "present", "absent", "half-day present", "half day present", "not marked", "attendance issue",
})


def half_day_band(total_duration_minutes: int | None, half_day_threshold_minutes: int | None) -> str | None:
    """Return 'absent', 'half' or 'full' for a completed day, or None if undecidable.

    * below 50% of Half Day Time           -> 'absent'
    * 50% .. 120% of Half Day Time (both inclusive) -> 'half'
    * above 120% of Half Day Time          -> 'full' (existing full-day rules apply)

    Integer cross-multiplication is used (no floating-point percentages), so the
    exact boundaries 50% and 120% behave exactly as written for any configured
    Half Day Time.
    """
    threshold = int(half_day_threshold_minutes or 0)
    if total_duration_minutes is None or threshold <= 0:
        return None
    total = max(0, int(total_duration_minutes))
    if total * 100 < threshold * HALF_DAY_ABSENT_BELOW_PERCENT:
        return "absent"
    if total * 100 > threshold * HALF_DAY_FULL_ABOVE_PERCENT:
        return "full"
    return "half"


def half_day_status_override(
    half: dict[str, Any],
    *,
    total_duration_minutes: int | None,
    half_day_threshold_minutes: int | None,
    check_in: Any,
    check_out: Any,
    day_completed: bool,
    current_status: Any,
    recalculated: bool = False,
) -> str | None:
    """Day status implied by the half-day rules, or None to keep ``current_status``.

    Shared by every attendance view, the persisted recalculation and payroll so
    that all of them reach the same conclusion. Protected statuses (holiday,
    weekly off, leave...) are never overridden. ``recalculated`` marks a status
    that came from an earlier automatic calculation (ESSL/WFH, not edited by HR
    Admin), which may therefore be replaced by the current result.
    """
    current = str(current_status or "").strip().casefold()
    if current in PROTECTED_DAY_STATUSES:
        return None
    if str((half or {}).get("half_day_present") or "No").startswith("Yes"):
        return "Half-Day Present"
    if not (day_completed and check_in and check_out):
        return None
    band = half_day_band(total_duration_minutes, half_day_threshold_minutes)
    if band == "absent":
        return "Absent"
    if band == "full" and recalculated and current in _RECALCULABLE_STATUSES and current != "present":
        return "Present"
    return None


def _to_clock(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return _clock_minutes(value)


def _subtract_segments(segments: list[tuple[float, float]], covers: list[tuple[float, float]]) -> list[tuple[float, float]]:
    for cover_start, cover_end in covers:
        remaining: list[tuple[float, float]] = []
        for seg_start, seg_end in segments:
            if cover_end <= seg_start or cover_start >= seg_end:
                remaining.append((seg_start, seg_end))
                continue
            if seg_start < cover_start:
                remaining.append((seg_start, cover_start))
            if cover_end < seg_end:
                remaining.append((cover_end, seg_end))
        segments = remaining
    return segments


def _half_side_from_intervals(scheduled_start: Any, scheduled_end: Any, check_in: Any, check_out: Any,
                              working_intervals: list[dict[str, Any]] | None,
                              permission_intervals: list[tuple[Any, Any]] | None = None) -> str | None:
    """Return 'first' or 'second' for a day that qualifies as Half-Day Present.

    The scheduled shift is split at its midpoint. The half containing more
    actual working time wins; approved permission time (not already covered by
    actual working intervals) counts as presence in the half it falls in. Ties
    are resolved deterministically (check-in/check-out span, then the half in
    which the employee started), so a qualifying day is never left undecided.
    ``None`` is returned only when check-in/check-out cannot be read at all.
    """
    actual_in = _clock_minutes(check_in)
    actual_out = _clock_minutes(check_out)
    if actual_in is None or actual_out is None:
        return None
    start = _clock_minutes(scheduled_start)
    end = _clock_minutes(scheduled_end)
    span = ((end - start) % 1440) if (start is not None and end is not None) else 0
    if span <= 0:
        # No usable schedule: split the calendar day at noon.
        start, span = 0, 1440
    mid = span / 2

    def relative(value: int) -> float:
        return float((value - start) % 1440)

    def split(a: float, b: float) -> tuple[float, float]:
        if b <= a:
            b += 1440
        return (max(0.0, min(b, mid) - max(a, 0.0)), max(0.0, min(b, span) - max(a, mid)))

    work: list[tuple[float, float]] = []
    for interval in working_intervals or []:
        a = _clock_minutes(interval.get("from_time") or interval.get("start"))
        b = _clock_minutes(interval.get("to_time") or interval.get("end"))
        if a is None or b is None:
            continue
        ar, br = relative(a), relative(b)
        if br <= ar:
            br += 1440
        work.append((ar, br))
    first_minutes = second_minutes = 0.0
    for ar, br in work:
        f, sec = split(ar, br)
        first_minutes += f
        second_minutes += sec

    perms: list[tuple[float, float]] = []
    for start_value, end_value in permission_intervals or []:
        a, b = _to_clock(start_value), _to_clock(end_value)
        if a is None or b is None:
            continue
        ar, br = relative(a), relative(b)
        if br <= ar:
            br += 1440
        perms.append((ar, br))
    for seg_a, seg_b in _subtract_segments(perms, work):
        f, sec = split(seg_a, seg_b)
        first_minutes += f
        second_minutes += sec

    if first_minutes != second_minutes:
        return "first" if first_minutes > second_minutes else "second"

    in_rel, out_rel = relative(actual_in), relative(actual_out)
    if out_rel <= in_rel:
        out_rel += 1440
    first_overlap, second_overlap = split(in_rel, out_rel)
    if first_overlap != second_overlap:
        return "first" if first_overlap > second_overlap else "second"

    if first_overlap == 0 and second_overlap == 0:
        # Worked entirely outside the scheduled hours: attribute to the nearer end.
        centre = (actual_in + ((actual_out - actual_in) % 1440) / 2) % 1440
        to_start = min((centre - start) % 1440, (start - centre) % 1440)
        to_end = min((centre - (start + span)) % 1440, ((start + span) - centre) % 1440)
        return "second" if to_end < to_start else "first"
    # Perfectly balanced across the midpoint: the half in which the day began.
    return "first" if in_rel < mid else "second"


def classify_half_day(
    *,
    total_duration_minutes: int | None,
    half_day_threshold_minutes: int,
    scheduled_start: Any,
    scheduled_end: Any,
    check_in: Any,
    check_out: Any,
    day_completed: bool,
    base_status: str = "Not Marked",
    leave_day_part: str | None = None,
    leave_is_full_day: bool = False,
    working_intervals: list[dict[str, Any]] | None = None,
    permission_intervals: list[tuple[Any, Any]] | None = None,
    recalculated_base: bool = False,
) -> dict[str, Any]:
    """Classify a completed day using Total Duration and the configured half-day threshold.

    The comparison value is elapsed Check-Out minus Check-In time (including breaks),
    never net working time. Thresholds are derived from the employee's configured
    Half Day Time (never hard-coded durations):

    * below 50%            -> Absent
    * 50% to 120% inclusive -> Half-Day Present (First or Second Half)
    * above 120%           -> existing full-day rules (Present)

    ``half_day_review_required`` is raised only when HR Admin action is actually
    needed: a completed day with a missing punch, or an unfinished day in progress.
    Protected holiday/weekly-off/leave statuses are never overwritten.
    ``recalculated_base`` allows a stale automatic status (Absent / Half-Day
    Present from an earlier calculation) to be replaced when the day is full.
    """
    status = str(base_status or "Not Marked").strip()
    normalized = status.casefold()
    result = {
        "half_day_present": "No", "half_day_present_side": None,
        "first_half_status": status, "second_half_status": status,
        "half_day_review_required": False,
    }
    if leave_is_full_day:
        result.update(first_half_status="Leave", second_half_status="Leave")
        return result
    part = str(leave_day_part or "").strip().casefold()
    if part in {"first_half", "second_half"}:
        leave_side = "first" if part == "first_half" else "second"
        present_side = "second" if leave_side == "first" else "first"
        has_punches = bool(day_completed and check_in and check_out and (total_duration_minutes or 0) > 0)
        other = "Present" if has_punches else ("Not Marked" if not day_completed else "Absent")
        result["first_half_status"] = "Leave" if leave_side == "first" else other
        result["second_half_status"] = "Leave" if leave_side == "second" else other
        if has_punches:
            result["half_day_present"] = "Yes – First Half" if present_side == "first" else "Yes – Second Half"
            result["half_day_present_side"] = present_side
        result["half_day_review_required"] = bool(not day_completed and check_in and not check_out)
        return result
    if normalized in PROTECTED_DAY_STATUSES:
        return result
    if not day_completed:
        if check_in and not check_out:
            result.update(first_half_status="Not Finalized", second_half_status="Not Finalized", half_day_review_required=True)
        return result
    if bool(check_in) != bool(check_out):
        # A completed day with only one punch cannot be finalized automatically.
        result["half_day_review_required"] = True
        return result
    if not check_in or not check_out:
        return result
    total = total_duration_minutes
    if total is None:
        total = duration_minutes(check_in, check_out)
    if total is None:
        result["half_day_review_required"] = True
        return result
    band = half_day_band(total, half_day_threshold_minutes)
    if band is None:
        # No Half Day Time is configured for this employee/date, so the half-day
        # rules do not apply and there is nothing for HR Admin to approve.
        return result
    if band == "absent":
        result.update(first_half_status="Absent", second_half_status="Absent")
        return result
    if band == "full":
        if normalized == "present" or (recalculated_base and normalized in _RECALCULABLE_STATUSES):
            result.update(first_half_status="Present", second_half_status="Present")
        return result
    side = _half_side_from_intervals(scheduled_start, scheduled_end, check_in, check_out,
                                     working_intervals, permission_intervals)
    if side is None:
        result["half_day_review_required"] = True
        return result
    result.update(
        half_day_present="Yes – First Half" if side == "first" else "Yes – Second Half",
        half_day_present_side=side,
        first_half_status="Present" if side == "first" else "Absent",
        second_half_status="Present" if side == "second" else "Absent",
    )
    return result


def combine_half_day_statuses(first_half_status: str, second_half_status: str) -> dict[str, Any]:
    """Derive a display status without discarding either half's source status."""
    first = str(first_half_status or "Not Marked").strip()
    second = str(second_half_status or "Not Marked").strip()
    first_key, second_key = first.casefold(), second.casefold()
    result: dict[str, Any] = {
        "first_half_status": first,
        "second_half_status": second,
        "half_day_present": "No",
        "half_day_present_side": None,
        "half_day_review_required": False,
    }
    if first_key == "present" and second_key == "present":
        result["status"] = "Present"
    elif first_key == "present" or second_key == "present":
        side = "first" if first_key == "present" else "second"
        result["half_day_present"] = "Yes – First Half" if side == "first" else "Yes – Second Half"
        result["half_day_present_side"] = side
        result["status"] = "Half-Day Present"
    elif first_key == second_key:
        result["status"] = first
    elif "absent" in {first_key, second_key}:
        result["status"] = "Absent"
    elif "medical leave" in {first_key, second_key}:
        result["status"] = "Medical Leave"
    elif "leave" in {first_key, second_key}:
        result["status"] = "Leave"
    elif "holiday" in {first_key, second_key}:
        result["status"] = "Holiday"
    elif "weekly off" in {first_key, second_key}:
        result["status"] = "Weekly Off"
    else:
        result["status"] = first if first_key != "not marked" else second
    return result


def calculate_half_day_portions(
    *,
    first_half_status: str,
    second_half_status: str,
    first_half_check_in_time: Any = None,
    first_half_check_out_time: Any = None,
    second_half_check_in_time: Any = None,
    second_half_check_out_time: Any = None,
    half_day_threshold_minutes: int,
    scheduled_start: Any,
    scheduled_end: Any,
) -> dict[str, Any]:
    """Validate half-day portions and return the persisted attendance fields."""
    statuses = {
        "first": str(first_half_status or "").strip().title(),
        "second": str(second_half_status or "").strip().title(),
    }
    supported = {"Present", "Absent", "Leave", "Medical Leave", "Holiday", "Weekly Off"}
    if any(value not in supported for value in statuses.values()):
        raise ValueError("Select a valid status for both halves.")
    if "Present" not in statuses.values():
        raise ValueError("Half-Day Present requires at least one Present half.")
    if half_day_threshold_minutes <= 0:
        raise ValueError("A scheduled half-day duration is required to validate Half-Day Present.")

    times = {
        "first": (first_half_check_in_time, first_half_check_out_time),
        "second": (second_half_check_in_time, second_half_check_out_time),
    }
    intervals: dict[str, list[dict[str, Any]]] = {"first": [], "second": []}
    for side, (check_in, check_out) in times.items():
        if statuses[side] != "Present":
            if check_in or check_out:
                raise ValueError(f"Clear the {side} half times unless that half is Present.")
            continue
        if not check_in or not check_out:
            raise ValueError(f"{side.title()} Half requires check-in and check-out times.")
        if str(check_out) <= str(check_in) or duration_minutes(check_in, check_out) is None:
            raise ValueError(f"{side.title()} Half check-out must be later than check-in.")
        minutes = duration_minutes(check_in, check_out) or 0
        interval = {"from_time": check_in, "to_time": check_out}
        eligibility = classify_half_day(
            total_duration_minutes=minutes,
            half_day_threshold_minutes=half_day_threshold_minutes,
            scheduled_start=scheduled_start,
            scheduled_end=scheduled_end,
            check_in=check_in,
            check_out=check_out,
            day_completed=True,
            base_status="Present",
            working_intervals=[interval],
        )
        expected = "First Half" if side == "first" else "Second Half"
        if eligibility.get("half_day_present") != f"Yes – {expected}":
            raise ValueError(
                f"{side.title()} Half work time must meet the inclusive 50%–120% scheduled half-day threshold."
            )
        interval["duration_minutes"] = minutes
        intervals[side].append(interval)

    if (
        intervals["first"] and intervals["second"]
        and intervals["first"][0]["to_time"] > intervals["second"][0]["from_time"]
    ):
        raise ValueError("First-half and second-half working intervals cannot overlap.")
    combined = combine_half_day_statuses(statuses["first"], statuses["second"])
    all_intervals = intervals["first"] + intervals["second"]
    return {
        **combined,
        "first_half_check_in_time": times["first"][0] if statuses["first"] == "Present" else None,
        "first_half_check_out_time": times["first"][1] if statuses["first"] == "Present" else None,
        "second_half_check_in_time": times["second"][0] if statuses["second"] == "Present" else None,
        "second_half_check_out_time": times["second"][1] if statuses["second"] == "Present" else None,
        "first_half_working_intervals": intervals["first"],
        "second_half_working_intervals": intervals["second"],
        "working_intervals": all_intervals,
        "actual_working_minutes": union_interval_minutes([
            (item["from_time"], item["to_time"]) for item in all_intervals
        ]),
        "half_day_working_duration": half_day_threshold_minutes,
        "half_day_eligibility": "Eligible",
    }


def select_weekly_off_dates(candidates: list[Any], slots: int, preferred_weekdays: set[int] | None = None) -> list[Any]:
    """Choose deterministic, week-distributed dates from eligible date objects."""
    from datetime import date

    remaining_slots = max(0, min(4, int(slots or 0)))
    if remaining_slots == 0 or not candidates:
        return []
    preferred = set(preferred_weekdays or set())
    groups: dict[tuple[int, int], list[date]] = {}
    for candidate in sorted(set(candidates)):
        if isinstance(candidate, date):
            groups.setdefault(candidate.isocalendar()[:2], []).append(candidate)
    ordered_groups = sorted(groups.items(), key=lambda item: item[1][0])
    if len(ordered_groups) > remaining_slots:
        indexes = (
            [round(i * (len(ordered_groups) - 1) / max(1, remaining_slots - 1)) for i in range(remaining_slots)]
            if remaining_slots > 1 else [len(ordered_groups) // 2]
        )
        ordered_groups = [ordered_groups[index] for index in dict.fromkeys(indexes)]
    selected: list[date] = []
    for _, week_dates in ordered_groups:
        preferred_dates = [item for item in week_dates if item.weekday() in preferred]
        if preferred_dates:
            chosen = preferred_dates[0]
        elif preferred:
            chosen = min(week_dates, key=lambda item: (min(min((item.weekday() - day) % 7, (day - item.weekday()) % 7) for day in preferred), item))
        else:
            chosen = min(week_dates, key=lambda item: (abs(item.weekday() - 3), item))
        selected.append(chosen)
        if len(selected) >= remaining_slots:
            break
    selected_set = set(selected)
    for candidate in sorted(set(candidates)):
        if len(selected) >= remaining_slots:
            break
        if candidate not in selected_set:
            selected.append(candidate)
            selected_set.add(candidate)
    return sorted(selected)
