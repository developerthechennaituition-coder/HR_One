"""Religion-based holiday calendars: shared lookups for attendance, leave and payroll.

A religion calendar (``holiday_calendars.religion_key`` set) applies to every
employee whose Employee Master religion matches. Only Published, active
calendars and non-cancelled holidays are official. Legacy company-wide
calendars (``religion_key`` empty) keep their existing assignment rules.
"""

from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from bson import ObjectId

from app.db.mongodb import db

holiday_calendars_collection = db["holiday_calendars"]
holidays_collection = db["holidays"]
employee_details_collection = db["employee_details"]
users_collection = db["users"]
payroll_collection = db["monthly_payroll_records"]

PAYROLL_DRAFT_STATUSES = ("Calculated",)


def religion_key(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def employee_religion(user: dict[str, Any]) -> str:
    """The employee's religion from the authoritative Employee Master record."""
    user_id = str(user.get("_id") or "")
    employee_id = str(user.get("employee_id") or "")
    clauses = [{"user_id": user_id}] if user_id else []
    if employee_id:
        clauses.append({"employee_id": employee_id})
    details = employee_details_collection.find_one({"$or": clauses}, {"religion": 1}) if clauses else None
    return " ".join(str((details or {}).get("religion") or user.get("religion") or "").split())


def employee_religions() -> list[str]:
    """Distinct religions currently assigned to active employees."""
    active_ids = {str(item["_id"]) for item in users_collection.find({"is_active": True}, {"_id": 1})}
    names: dict[str, str] = {}
    for details in employee_details_collection.find({"religion": {"$nin": [None, ""]}}, {"religion": 1, "user_id": 1}):
        if details.get("user_id") and str(details["user_id"]) not in active_ids:
            continue
        name = " ".join(str(details.get("religion") or "").split())
        if name:
            names.setdefault(religion_key(name), name)
    return sorted(names.values(), key=str.casefold)


def religion_user_ids(key: str) -> list[str]:
    """Active user ids whose Employee Master religion matches ``key``."""
    if not key:
        return []
    candidate_ids = [
        str(details.get("user_id"))
        for details in employee_details_collection.find({"religion": {"$nin": [None, ""]}}, {"religion": 1, "user_id": 1})
        if details.get("user_id") and religion_key(details.get("religion")) == key
    ]
    valid = [ObjectId(value) for value in candidate_ids if ObjectId.is_valid(value)]
    return [str(item["_id"]) for item in users_collection.find({"_id": {"$in": valid}, "is_active": True}, {"_id": 1})]


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def published_religion_holidays(user: dict[str, Any], start: date, end: date, include_optional: bool = True) -> dict[date, dict[str, Any]]:
    """Official holidays from the employee's published religion calendar(s)."""
    key = religion_key(employee_religion(user))
    if not key or end < start:
        return {}
    calendar_ids = [
        str(item["_id"])
        for item in holiday_calendars_collection.find(
            {
                "religion_key": key,
                "status": "Published",
                "is_active": True,
                "calendar_year": {"$in": list(range(start.year, end.year + 1))},
            },
            {"_id": 1},
        )
    ]
    if not calendar_ids:
        return {}
    query: dict[str, Any] = {
        "calendar_id": {"$in": calendar_ids},
        "is_active": True,
        "status": {"$ne": "Cancelled"},
        "holiday_date": {
            "$gte": datetime.combine(start, time.min, tzinfo=timezone.utc),
            "$lt": datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone.utc),
        },
    }
    if not include_optional:
        query["is_optional"] = {"$ne": True}
    result: dict[date, dict[str, Any]] = {}
    for holiday in holidays_collection.find(query).sort("holiday_date", 1):
        holiday_day = _as_date(holiday.get("holiday_date"))
        if holiday_day:
            result.setdefault(holiday_day, holiday)
    return result


def flag_draft_payroll_for_holiday_change(key: str, holiday_dates: list[date], reason: str) -> int:
    """Mark Calculated (draft) payroll outdated for employees of a religion.

    Finalized payroll is never changed; historical attendance is untouched.
    """
    periods = sorted({f"{value.year:04d}-{value.month:02d}" for value in holiday_dates if value})
    user_ids = religion_user_ids(key)
    if not periods or not user_ids:
        return 0
    employee_ids = [
        str(item.get("employee_id"))
        for item in users_collection.find({"_id": {"$in": [ObjectId(value) for value in user_ids]}}, {"employee_id": 1})
        if item.get("employee_id")
    ]
    if not employee_ids:
        return 0
    now = datetime.now(timezone.utc)
    result = payroll_collection.update_many(
        {"employee_id": {"$in": employee_ids}, "period": {"$in": periods}, "status": {"$in": list(PAYROLL_DRAFT_STATUSES)}},
        {
            "$set": {"attendance_outdated": True, "attendance_outdated_at": now, "attendance_outdated_reason": reason},
            "$addToSet": {"attendance_outdated_dates": {"$each": [value.isoformat() for value in holiday_dates if value]}},
        },
    )
    return result.modified_count
