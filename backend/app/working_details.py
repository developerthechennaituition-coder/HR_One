"""Shared employee working-schedule helpers.

Schedules are stored on employee_details. When changed, the previous schedule is
closed in working_schedule_history so historical attendance keeps its effective
schedule without rewriting attendance or payroll records.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from app.db.mongodb import db

employee_details_collection = db["employee_details"]


def clock_minutes(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        parts = value.strip().split(":")
        hour, minute = int(parts[0]), int(parts[1])
        if 0 <= hour < 24 and 0 <= minute < 60:
            return hour * 60 + minute
    except (ValueError, IndexError):
        return None
    return None


def schedule_duration(start: Any, end: Any) -> int:
    start_minutes, end_minutes = clock_minutes(start), clock_minutes(end)
    if start_minutes is None or end_minutes is None or start_minutes == end_minutes:
        return 0
    return (end_minutes - start_minutes) % 1440


def format_minutes(value: int) -> str:
    value = max(0, int(value or 0))
    return f"{value // 60:02d}:{value % 60:02d}"


def format_hours_minutes(value: int) -> str:
    value = max(0, int(value or 0))
    return f"{value // 60:02d} Hours {value % 60:02d} Minutes"


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _snapshot(details: dict[str, Any], effective_from: Any = None) -> dict[str, Any]:
    start = details.get("scheduled_work_start_time") or ""
    end = details.get("scheduled_work_end_time") or ""
    duration = schedule_duration(start, end)
    stored_duration = int(details.get("scheduled_work_duration") or 0)
    effective_duration = duration or stored_duration
    stored_half_day = int(details.get("half_day_working_duration") or 0)
    return {
        "working_type": details.get("working_type") or "Full Time",
        "scheduled_work_start_time": start,
        "scheduled_work_end_time": end,
        "scheduled_work_duration": effective_duration,
        # Keep an explicitly stored threshold for legacy/partially migrated
        # records. Derive it from the schedule only when no threshold exists.
        "half_day_working_duration": stored_half_day or effective_duration // 2,
        "effective_from": str(effective_from or details.get("schedule_effective_from") or "1900-01-01")[:10],
    }


def applicable_schedule(user_id: str, attendance_date: date, user: dict[str, Any] | None = None, details: dict[str, Any] | None = None) -> dict[str, Any]:
    details = details if details is not None else (employee_details_collection.find_one({"user_id": str(user_id)}) or {})
    history = details.get("working_schedule_history") or []
    target = attendance_date.isoformat()
    candidates: list[dict[str, Any]] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        effective_from = _as_date(item.get("effective_from"))
        effective_to = _as_date(item.get("effective_to"))
        if effective_from and effective_from <= attendance_date and (not effective_to or attendance_date <= effective_to):
            candidates.append(item)
    current = _snapshot(details, details.get("schedule_effective_from"))
    current_from = _as_date(current["effective_from"])
    if current_from and current_from <= attendance_date:
        candidates.append(current)
    if candidates:
        chosen = max(candidates, key=lambda item: str(item.get("effective_from") or "1900-01-01"))
    else:
        # Existing employees without schedule metadata retain blank schedule data;
        # do not invent a default shift.
        chosen = {
            "working_type": details.get("working_type") or "",
            "scheduled_work_start_time": "",
            "scheduled_work_end_time": "",
            "scheduled_work_duration": 0,
            "half_day_working_duration": 0,
            "effective_from": "",
        }
    duration = int(chosen.get("scheduled_work_duration") or schedule_duration(
        chosen.get("scheduled_work_start_time"), chosen.get("scheduled_work_end_time")
    ))
    half_day_duration = int(chosen.get("half_day_working_duration") or duration // 2)
    result = {
        **chosen,
        "scheduled_work_duration": duration,
        "half_day_working_duration": half_day_duration,
        "scheduled_work_duration_text": format_hours_minutes(duration) if duration else "",
        # The text shown in attendance must represent the exact numeric
        # threshold used by classify_half_day(), including legacy snapshots.
        "half_day_working_duration_text": format_hours_minutes(half_day_duration) if half_day_duration else "",
    }
    return result


def _schedule_record(values: dict[str, Any], effective_from: date) -> dict[str, Any]:
    start = values.get("scheduled_work_start_time") or ""
    end = values.get("scheduled_work_end_time") or ""
    duration = schedule_duration(start, end)
    return {
        "working_type": values.get("working_type") or "Full Time",
        "scheduled_work_start_time": start,
        "scheduled_work_end_time": end,
        "scheduled_work_duration": duration,
        "half_day_working_duration": duration // 2,
        "effective_from": effective_from.isoformat(),
    }


def record_schedule_change(
    details: dict[str, Any] | None,
    new_values: dict[str, Any],
    effective_from: date,
    *,
    force_effective_date: bool = False,
    joining_date: date | datetime | str | None = None,
) -> dict[str, Any]:
    """Insert/replace a schedule at an effective date without losing later history.

    The top-level fields always represent the latest effective schedule. All earlier
    schedules are retained as closed ranges in working_schedule_history.
    """
    old = details or {}
    old_history = old.get("working_schedule_history") or []
    schedules: list[dict[str, Any]] = []
    for item in old_history:
        if not isinstance(item, dict):
            continue
        item_from = _as_date(item.get("effective_from"))
        if not item_from:
            continue
        schedules.append({
            **_snapshot(item, item_from),
            "effective_from": item_from.isoformat(),
        })

    # Prefer the schedule's own effective date. For legacy records without
    # schedule metadata, use the employee's joining date as the baseline so
    # their existing schedule is retained in history when a new one is saved.
    current_from = (
        _as_date(old.get("schedule_effective_from"))
        or _as_date(old.get("date_of_joining"))
        or _as_date(old.get("joining_date"))
        or _as_date(joining_date)
    )
    if current_from and (
        old.get("scheduled_work_start_time")
        or old.get("scheduled_work_end_time")
        or old.get("working_type")
    ):
        schedules.append({
            **_snapshot(old, current_from),
            "effective_from": current_from.isoformat(),
        })

    # Collapse duplicate effective dates deterministically, keeping the latest
    # current/top-level record where it shares a date with a history entry.
    by_date: dict[str, dict[str, Any]] = {}
    for item in sorted(schedules, key=lambda x: x["effective_from"]):
        by_date[item["effective_from"]] = item

    new_record = _schedule_record(new_values, effective_from)

    # The Working Details form edits the *current* schedule. When the saved
    # values describe a correction of it - the same schedule moved to another
    # date, or changed values with a starting date on/before the current one -
    # replace the current entry instead of keeping it alongside the new one.
    # Otherwise the old entry (still the latest by date) would win and the
    # previous values would reappear after saving.
    if current_from:
        current_key = current_from.isoformat()
        current_entry = by_date.get(current_key)
        if current_entry is not None and current_key != effective_from.isoformat():
            unchanged_values = (
                current_entry.get("working_type") == new_record.get("working_type")
                and current_entry.get("scheduled_work_start_time") == new_record.get("scheduled_work_start_time")
                and current_entry.get("scheduled_work_end_time") == new_record.get("scheduled_work_end_time")
            )
            if unchanged_values or effective_from < current_from:
                del by_date[current_key]

    ordered_before = [by_date[key] for key in sorted(by_date)]
    if ordered_before:
        latest_before = ordered_before[-1]
        same_configuration = (
            latest_before.get("working_type") == new_record.get("working_type")
            and latest_before.get("scheduled_work_start_time") == new_record.get("scheduled_work_start_time")
            and latest_before.get("scheduled_work_end_time") == new_record.get("scheduled_work_end_time")
        )
        if same_configuration and not force_effective_date and effective_from >= date.fromisoformat(latest_before["effective_from"]):
            return {
                "working_type": old.get("working_type") or latest_before["working_type"],
                "scheduled_work_start_time": old.get("scheduled_work_start_time") or latest_before["scheduled_work_start_time"],
                "scheduled_work_end_time": old.get("scheduled_work_end_time") or latest_before["scheduled_work_end_time"],
                "scheduled_work_duration": int(old.get("scheduled_work_duration") or latest_before["scheduled_work_duration"] or 0),
                "half_day_working_duration": int(old.get("half_day_working_duration") or latest_before["half_day_working_duration"] or 0),
                "schedule_effective_from": str(old.get("schedule_effective_from") or latest_before["effective_from"])[:10],
                "working_schedule_history": list(old_history),
            }
    by_date[effective_from.isoformat()] = new_record
    ordered = [by_date[key] for key in sorted(by_date)]

    # Rebuild closed intervals. This also preserves schedules that were already
    # planned for dates after the newly inserted effective date.
    history: list[dict[str, Any]] = []
    for index, item in enumerate(ordered[:-1]):
        next_from = date.fromisoformat(ordered[index + 1]["effective_from"])
        history.append({
            "working_type": item["working_type"],
            "scheduled_work_start_time": item["scheduled_work_start_time"],
            "scheduled_work_end_time": item["scheduled_work_end_time"],
            "scheduled_work_duration": int(item["scheduled_work_duration"] or 0),
            "half_day_working_duration": int(item["half_day_working_duration"] or 0),
            "effective_from": item["effective_from"],
            "effective_to": (next_from - timedelta(days=1)).isoformat(),
        })

    latest = ordered[-1]
    return {
        "working_type": latest["working_type"],
        "scheduled_work_start_time": latest["scheduled_work_start_time"],
        "scheduled_work_end_time": latest["scheduled_work_end_time"],
        "scheduled_work_duration": int(latest["scheduled_work_duration"] or 0),
        "half_day_working_duration": int(latest["half_day_working_duration"] or 0),
        "schedule_effective_from": latest["effective_from"],
        "working_schedule_history": history,
    }
