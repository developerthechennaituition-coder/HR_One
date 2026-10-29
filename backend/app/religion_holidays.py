"""Religion-based holiday calendars: shared lookups for attendance, leave and payroll.

A religion calendar (``holiday_calendars.religion_key`` set) applies to every
employee whose Employee Master religion matches and, when the calendar has
organization ``assignments`` (branch records in ``companies``), who belongs to
one of those branches. Calendars without assignments apply to all branches.
Only Published, active calendars and non-cancelled holidays are official.
Holidays of a deleted published calendar stay effective only for dates before
its deletion, so past attendance is unchanged. Legacy company-wide calendars
(``religion_key`` empty) keep their existing assignment rules.
"""

import re

from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from bson import ObjectId

from app.db.mongodb import db

companies_collection = db["companies"]
employees_collection = db["employees"]
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


def employee_branch_ids(user: dict[str, Any]) -> set[str]:
    """Branch records (``companies`` documents) the employee belongs to."""
    aliases = {str(user.get("_id") or ""), str(user.get("employee_id") or "")}
    employee = employees_collection.find_one({"employee_id": user.get("employee_id")}, {"_id": 1, "work_location": 1}) if user.get("employee_id") else None
    if employee:
        aliases.add(str(employee["_id"]))
    aliases.discard("")
    branches = {
        str(item["_id"])
        for item in companies_collection.find({"is_active": {"$ne": False}, "employee_ids": {"$in": list(aliases)}}, {"_id": 1})
    }
    if branches:
        return branches
    work_location = str(user.get("work_location") or (employee or {}).get("work_location") or "").strip()
    if work_location:
        pattern = {"$regex": f"^{re.escape(work_location)}$", "$options": "i"}
        branches = {
            str(item["_id"])
            for item in companies_collection.find(
                {"is_active": {"$ne": False}, "$or": [{"branch": pattern}, {"location": pattern}, {"branch_code": work_location}]}, {"_id": 1}
            )
        }
        if branches:
            return branches
    active = list(companies_collection.find({"is_active": {"$ne": False}}, {"_id": 1}).limit(2))
    return {str(active[0]["_id"])} if len(active) == 1 else set()


def calendar_branch_ids(calendar: dict[str, Any]) -> set[str]:
    return {str(item.get("company_id")) for item in calendar.get("assignments") or [] if item.get("company_id")}


def calendar_applies_to(calendar: dict[str, Any], user: dict[str, Any], branches: set[str] | None = None) -> bool:
    """Religion match plus, for assigned calendars, membership of an assigned branch."""
    key = calendar.get("religion_key") or ""
    if not key or religion_key(employee_religion(user)) != key:
        return False
    assigned = calendar_branch_ids(calendar)
    if not assigned:
        return True
    return bool(assigned & (employee_branch_ids(user) if branches is None else branches))


def calendar_user_ids(calendar: dict[str, Any]) -> list[str]:
    """Active employees a religion calendar applies to (religion + branch scope)."""
    user_ids = religion_user_ids(calendar.get("religion_key") or "")
    assigned = calendar_branch_ids(calendar)
    if not assigned or not user_ids:
        return user_ids
    users = users_collection.find({"_id": {"$in": [ObjectId(value) for value in user_ids]}})
    return [str(user["_id"]) for user in users if assigned & employee_branch_ids(user)]


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
    branches = employee_branch_ids(user)
    live_ids: list[str] = []
    deleted: list[tuple[str, date]] = []
    for calendar in holiday_calendars_collection.find(
        {
            "religion_key": key,
            "calendar_year": {"$in": list(range(start.year, end.year + 1))},
            "$or": [
                {"status": "Published", "is_active": True},
                {"status": "Deleted", "was_published": True},
            ],
        },
        {"status": 1, "religion_key": 1, "assignments": 1, "deleted_on": 1},
    ):
        if not calendar_applies_to(calendar, user, branches):
            continue
        if calendar.get("status") == "Published":
            live_ids.append(str(calendar["_id"]))
        elif _as_date(calendar.get("deleted_on")):
            deleted.append((str(calendar["_id"]), _as_date(calendar["deleted_on"])))
    if not live_ids and not deleted:
        return {}
    clauses: list[dict[str, Any]] = []
    if live_ids:
        clauses.append({"calendar_id": {"$in": live_ids}, "is_active": True, "status": {"$ne": "Cancelled"}})
    for calendar_id, deleted_on in deleted:
        # A deleted calendar keeps counting only for dates before its deletion.
        clauses.append({
            "calendar_id": calendar_id,
            "status": "Deleted",
            "holiday_date": {"$lt": datetime.combine(deleted_on, time.min, tzinfo=timezone.utc)},
        })
    query: dict[str, Any] = {
        "$or": clauses,
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


def flag_draft_payroll_for_holiday_change(user_ids: list[str], holiday_dates: list[date], reason: str) -> int:
    """Mark Calculated (draft) payroll outdated for the affected employees.

    Finalized payroll is never changed; historical attendance is untouched.
    """
    periods = sorted({f"{value.year:04d}-{value.month:02d}" for value in holiday_dates if value})
    user_ids = [value for value in user_ids if ObjectId.is_valid(value)]
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
