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
    return {
        "working_type": details.get("working_type") or "Full Time",
        "scheduled_work_start_time": start,
        "scheduled_work_end_time": end,
        "scheduled_work_duration": duration or int(details.get("scheduled_work_duration") or 0),
        "half_day_working_duration": (duration or int(details.get("scheduled_work_duration") or 0)) // 2,
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
    result = {
        **chosen,
        "scheduled_work_duration": duration,
        "half_day_working_duration": int(chosen.get("half_day_working_duration") or duration // 2),
        "scheduled_work_duration_text": format_hours_minutes(duration) if duration else "",
        "half_day_working_duration_text": format_hours_minutes(duration // 2) if duration else "",
    }
    return result


def record_schedule_change(details: dict[str, Any] | None, new_values: dict[str, Any], effective_from: date) -> dict[str, Any]:
    """Return update fields while preserving the old schedule's effective range."""
    old = details or {}
    old_start = old.get("scheduled_work_start_time") or ""
    old_end = old.get("scheduled_work_end_time") or ""
    new_start = new_values.get("scheduled_work_start_time") or ""
    new_end = new_values.get("scheduled_work_end_time") or ""
    old_type = old.get("working_type") or "Full Time"
    new_type = new_values.get("working_type") or "Full Time"
    old_duration = schedule_duration(old_start, old_end)
    new_duration = schedule_duration(new_start, new_end)
    history = list(old.get("working_schedule_history") or [])
    current_from = _as_date(old.get("schedule_effective_from")) or _as_date(old.get("date_of_joining")) or effective_from
    schedule_changed = (old_start, old_end, old_type) != (new_start, new_end, new_type)
    if schedule_changed and (old_start or old_end):
        if effective_from > current_from:
            history.append({
                "working_type": old_type,
                "scheduled_work_start_time": old_start,
                "scheduled_work_end_time": old_end,
                "scheduled_work_duration": old_duration,
                "half_day_working_duration": old_duration // 2,
                "effective_from": current_from.isoformat(),
                "effective_to": (effective_from - timedelta(days=1)).isoformat(),
            })
    return {
        "working_type": new_type,
        "scheduled_work_start_time": new_start,
        "scheduled_work_end_time": new_end,
        "scheduled_work_duration": new_duration,
        "half_day_working_duration": new_duration // 2,
        "schedule_effective_from": (
            effective_from.isoformat() if schedule_changed
            else str(old.get("schedule_effective_from") or current_from.isoformat())[:10]
        ),
        "working_schedule_history": history,
    }
