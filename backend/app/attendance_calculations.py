"""Shared, side-effect-free attendance duration and half-day calculations.

All durations are elapsed wall-clock minutes.  The module intentionally does not
import FastAPI or MongoDB so the business rules can be reused and unit-tested.
"""
from __future__ import annotations

import re
from datetime import datetime, time
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
            return None
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


def attendance_issue(check_in: Any, check_out: Any, working_intervals: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Describe incomplete/inconsistent punches without changing attendance status."""
    issues: list[str] = []
    if bool(check_in) != bool(check_out):
        issues.append("Missing check-out time" if check_in else "Missing check-in time")
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


def _half_side_from_intervals(scheduled_start: Any, scheduled_end: Any, check_in: Any, check_out: Any,
                              working_intervals: list[dict[str, Any]] | None) -> str | None:
    start = _clock_minutes(scheduled_start)
    end = _clock_minutes(scheduled_end)
    actual_in = _clock_minutes(check_in)
    actual_out = _clock_minutes(check_out)
    if None in (start, end, actual_in, actual_out):
        return None
    span = (end - start) % 1440
    if span <= 0:
        return None
    midpoint = (start + span / 2) % 1440
    # Put clock values on one linear timeline, including overnight schedules.
    def relative(value: int) -> float:
        return float((value - start) % 1440)
    midpoint_rel = span / 2
    first_minutes = second_minutes = 0.0
    for interval in working_intervals or []:
        a = _clock_minutes(interval.get("from_time"))
        b = _clock_minutes(interval.get("to_time"))
        if a is None or b is None:
            continue
        ar, br = relative(a), relative(b)
        if br <= ar:
            br += 1440
        first_minutes += max(0.0, min(br, midpoint_rel) - max(ar, 0.0))
        second_minutes += max(0.0, min(br, span) - max(ar, midpoint_rel))
    if first_minutes or second_minutes:
        if first_minutes == second_minutes:
            return None
        return "first" if first_minutes > second_minutes else "second"
    in_rel, out_rel = relative(actual_in), relative(actual_out)
    if out_rel <= in_rel:
        out_rel += 1440
    first_overlap = max(0.0, min(out_rel, midpoint_rel) - max(in_rel, 0.0))
    second_overlap = max(0.0, min(out_rel, span) - max(in_rel, midpoint_rel))
    if first_overlap == second_overlap:
        return None
    return "first" if first_overlap > second_overlap else "second"


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
) -> dict[str, Any]:
    """Classify a completed day using the configured half-day threshold.

    80%-150% of the configured half-day duration (inclusive) is half-day present.
    Below 80% is absent. Above 150% falls through to the existing full-day status
    rules. Protected holiday/weekly-off/leave statuses are never overwritten.
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
    if normalized in {"holiday", "weekly off", "weekly-off", "week off", "not applicable", "before joining", "leave", "medical leave"}:
        return result
    if not day_completed:
        if check_in and not check_out:
            result.update(first_half_status="Not Finalized", second_half_status="Not Finalized", half_day_review_required=True)
        return result
    if total_duration_minutes is None or not check_in or not check_out or half_day_threshold_minutes <= 0:
        result["half_day_review_required"] = bool(check_in or check_out)
        return result
    threshold = int(half_day_threshold_minutes)
    lower = threshold * 0.80
    upper = threshold * 1.50
    worked = max(0, int(total_duration_minutes))
    if worked < lower:
        result.update(first_half_status="Absent", second_half_status="Absent")
        return result
    if worked > upper:
        if normalized == "present":
            result.update(first_half_status="Present", second_half_status="Present")
        return result
    side = _half_side_from_intervals(scheduled_start, scheduled_end, check_in, check_out, working_intervals)
    if side is None:
        result["half_day_review_required"] = True
        result.update(first_half_status="Absent", second_half_status="Absent")
        return result
    result.update(
        half_day_present="Yes – First Half" if side == "first" else "Yes – Second Half",
        half_day_present_side=side,
        first_half_status="Present" if side == "first" else "Absent",
        second_half_status="Present" if side == "second" else "Absent",
    )
    return result


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
