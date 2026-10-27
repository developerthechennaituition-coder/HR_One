from __future__ import annotations

import csv
import io
import calendar
import base64
import binascii
import logging
import smtplib
from datetime import date, datetime, timedelta, timezone
from typing import Any

from bson import ObjectId
from bson.binary import Binary
from fastapi import APIRouter, Header, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, field_validator
from pymongo.errors import DuplicateKeyError
from pymongo import ReturnDocument

from app.attendance import (
    COMPANY_START_TIME,
    WEEKDAY_NAMES,
    company_now,
    company_weekly_off_weekdays,
    leave_cancel_deadline,
    remove_leave_attendance,
    sync_leave_for_today,
)
from app.db.mongodb import db
from app.session import active_sessions
from app.activation import send_hr_email_with_attachment

router = APIRouter(prefix="/api/leave", tags=["Leave Management"])

leave_types_collection = db["leave_types"]
leave_policies_collection = db["leave_policies"]
leave_balances_collection = db["leave_balances"]
leave_requests_collection = db["leave_requests"]
users_collection = db["users"]
employee_details_collection = db["employee_details"]
holidays_collection = db["holidays"]
employee_holiday_calendars_collection = db["employee_holiday_calendars"]
leave_notifications_collection = db["leave_notifications"]
permission_requests_collection = db["permission_requests"]
notification_sequences_collection = db["notification_sequences"]
audit_logs_collection = db["audit_logs"]
logger = logging.getLogger(__name__)

APPROVER_ROLES = {"hr admin", "administrator", "hr manager"}
HR_ADMIN_ROLE = "hr admin"


class LeaveTypeCreate(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    is_paid: bool = True
    allow_half_day: bool = True
    requires_attachment: bool = False
    is_active: bool = True

    @field_validator("code", "name")
    @classmethod
    def clean_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class LeavePolicyCreate(BaseModel):
    leave_type_id: str = Field(min_length=1)
    annual_entitlement: float = Field(ge=0)
    carry_forward_allowed: bool = False
    max_carry_forward: float = Field(default=0, ge=0)
    accrual_enabled: bool = False
    accrual_frequency: str = "annual"
    is_active: bool = True

    @field_validator("accrual_frequency")
    @classmethod
    def valid_frequency(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"annual", "monthly", "quarterly"}:
            raise ValueError("Accrual frequency must be annual, monthly or quarterly")
        return value


class LeaveApplicationCreate(BaseModel):
    leave_type_id: str = Field(min_length=1)
    from_date: date
    to_date: date
    day_part: str = "full_day"
    reason: str = Field(min_length=1, max_length=1000)
    attachment_name: str | None = Field(default=None, max_length=255)
    attachment_content_base64: str | None = Field(default=None, max_length=7_000_000)
    attachment_content_type: str | None = Field(default=None, max_length=100)

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Reason is required")
        return value

    @field_validator("day_part")
    @classmethod
    def valid_day_part(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"full_day", "first_half", "second_half"}:
            raise ValueError("Invalid day part")
        return value


class LeaveDecisionPayload(BaseModel):
    remarks: str | None = Field(default=None, max_length=1000)


class LeaveTypeUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    is_paid: bool | None = None
    allow_half_day: bool | None = None
    requires_attachment: bool | None = None
    is_active: bool | None = None


class LeavePolicyUpdate(BaseModel):
    annual_entitlement: float | None = Field(default=None, ge=0)
    carry_forward_allowed: bool | None = None
    max_carry_forward: float | None = Field(default=None, ge=0)
    accrual_enabled: bool | None = None
    accrual_frequency: str | None = None
    is_active: bool | None = None

    @field_validator("accrual_frequency")
    @classmethod
    def valid_frequency(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().lower()
        if value not in {"annual", "monthly", "quarterly"}:
            raise ValueError("Accrual frequency must be annual, monthly or quarterly")
        return value


class LeaveCompanyPolicy(BaseModel):
    weekly_offs_per_month: int = Field(default=4, ge=1, le=10)
    # Configured by HR Admin; no weekday is a weekly off until HR selects one.
    weekly_off_weekdays: list[str] = Field(default_factory=list, max_length=6)
    probation_period_months: int = Field(default=3, ge=0, le=24)
    sick_leave_after_probation: float = Field(default=1, ge=0)
    sick_leave_before_probation_allowed: bool = False
    one_day_approver: str = "reporting_manager"
    continuous_two_day_leave_allowed: bool = True
    more_than_two_days_approver: str = "hr_admin"
    medical_certificate_required: bool = True
    religion_holidays_enabled: bool = True

    @field_validator("weekly_off_weekdays")
    @classmethod
    def validate_weekdays(cls, value: list[str]) -> list[str]:
        days = {str(item).strip().lower() for item in value}
        if not days <= set(WEEKDAY_NAMES):
            raise ValueError("Weekly off days must be valid weekdays.")
        return [day for day in WEEKDAY_NAMES if day in days]

    @field_validator("one_day_approver", "more_than_two_days_approver")
    @classmethod
    def validate_approver(cls, value: str) -> str:
        value = value.strip().lower()
        allowed = {"reporting_manager", "hr_admin"}
        if value not in allowed:
            raise ValueError("Approver must be reporting_manager or hr_admin.")
        return value


def ensure_leave_indexes() -> None:
    leave_types_collection.create_index("code", unique=True)
    leave_policies_collection.create_index("leave_type_id", unique=True)
    leave_balances_collection.create_index(
        [("user_id", 1), ("leave_type_id", 1), ("year", 1)], unique=True
    )
    leave_requests_collection.create_index([("user_id", 1), ("created_at", -1)])
    leave_requests_collection.create_index([("status", 1), ("created_at", -1)])
    leave_requests_collection.create_index([("user_id", 1), ("from_date", 1), ("to_date", 1)])
    leave_notifications_collection.create_index("leave_notification_id", unique=True)
    permission_requests_collection.create_index([("user_id", 1), ("permission_date", -1)])
    permission_requests_collection.create_index([("status", 1), ("reporting_manager_id", 1)])
    leave_notifications_collection.create_index([("recipient_user_id", 1), ("is_read", 1), ("created_at", -1)])
    audit_logs_collection.create_index([("created_at", -1)])
    existing_ids = [
        record.get("leave_notification_id", "")
        for record in leave_notifications_collection.find({}, {"leave_notification_id": 1})
    ]
    maximum_id = max(
        (int(value[1:]) for value in existing_ids if value.startswith("L") and value[1:].isdigit()),
        default=0,
    )
    notification_sequences_collection.update_one(
        {"_id": "leave_notification"},
        {"$max": {"value": maximum_id}},
        upsert=True,
    )
    company_policy_defaults = LeaveCompanyPolicy().model_dump()
    leave_policies_collection.update_one(
        {"leave_type_id": "__company_policy__"},
        {
            "$setOnInsert": {
                "leave_type_id": "__company_policy__",
                **company_policy_defaults,
                "created_at": datetime.now(timezone.utc),
            },
            "$set": {"updated_at": datetime.now(timezone.utc)},
        },
        upsert=True,
    )
    # One-time migration from the old single weekly_off_weekday field. Its
    # "sunday" value was a seeded default, so only an HR-chosen other day is kept.
    legacy_policy = leave_policies_collection.find_one(
        {"leave_type_id": "__company_policy__", "weekly_off_weekdays": {"$exists": False}}
    )
    if legacy_policy:
        legacy_day = str(legacy_policy.get("weekly_off_weekday") or "").strip().lower()
        leave_policies_collection.update_one(
            {"_id": legacy_policy["_id"]},
            {
                "$set": {"weekly_off_weekdays": [legacy_day] if legacy_day in WEEKDAY_NAMES and legacy_day != "sunday" else []},
                "$unset": {"weekly_off_weekday": ""},
            },
        )

    defaults = [
        {"code": "CL", "name": "Casual Leave", "description": "Casual leave", "is_paid": True, "allow_half_day": True, "requires_attachment": False, "is_active": True},
        {"code": "SL", "name": "Sick Leave", "description": "Sick leave", "is_paid": True, "allow_half_day": True, "requires_attachment": False, "is_active": True},
        {"code": "EL", "name": "Earned Leave", "description": "Earned / annual leave", "is_paid": True, "allow_half_day": True, "requires_attachment": False, "is_active": True},
        {"code": "LOP", "name": "Loss of Pay", "description": "Leave without pay", "is_paid": False, "allow_half_day": True, "requires_attachment": False, "is_active": True},
    ]
    default_entitlements = {"CL": 12, "SL": 12, "EL": 15, "LOP": 0}
    now = datetime.now(timezone.utc)
    for item in defaults:
        leave_types_collection.update_one(
            {"code": item["code"]},
            {"$setOnInsert": {**item, "created_at": now, "updated_at": now}},
            upsert=True,
        )
    for code, entitlement in default_entitlements.items():
        leave_type = leave_types_collection.find_one({"code": code})
        if not leave_type:
            continue
        leave_type_id = str(leave_type["_id"])
        leave_policies_collection.update_one(
            {"leave_type_id": leave_type_id},
            {"$setOnInsert": {"leave_type_id": leave_type_id, "annual_entitlement": entitlement, "carry_forward_allowed": False, "max_carry_forward": 0, "accrual_enabled": False, "accrual_frequency": "annual", "is_active": True, "created_at": now, "updated_at": now}},
            upsert=True,
        )


def _serialize(record: dict[str, Any]) -> dict[str, Any]:
    result = dict(record)
    if "_id" in result:
        result["id"] = str(result.pop("_id"))
    for key, value in list(result.items()):
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            result[key] = value.isoformat()
        elif isinstance(value, ObjectId):
            result[key] = str(value)
    return result


def _parse_object_id(raw_id: str, name: str = "id") -> ObjectId:
    if not ObjectId.is_valid(raw_id):
        raise HTTPException(status_code=400, detail=f"Invalid {name}")
    return ObjectId(raw_id)


def _current_user(authorization: str | None) -> dict[str, Any]:
    if not isinstance(authorization, str) or not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    token = authorization.removeprefix("Bearer ").strip()
    user_id = active_sessions.get(token)
    if not user_id or not ObjectId.is_valid(user_id):
        raise HTTPException(status_code=401, detail="Session expired. Please login again.")
    user = users_collection.find_one({"_id": ObjectId(user_id), "is_active": True})
    if not user:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return user


def _is_admin_or_hr(user: dict[str, Any]) -> bool:
    return str(user.get("role", "")).strip().lower() in APPROVER_ROLES


def _is_hr_admin(user: dict[str, Any]) -> bool:
    return str(user.get("role", "")).strip().lower() == HR_ADMIN_ROLE


def _company_policy() -> dict[str, Any]:
    record = leave_policies_collection.find_one({"leave_type_id": "__company_policy__"})
    defaults = LeaveCompanyPolicy().model_dump()
    return {**defaults, **(record or {})}


def _get_reporting_manager_id(user: dict[str, Any]) -> str | None:
    manager_id = user.get("reporting_manager_id")
    if manager_id:
        return str(manager_id)
    details = employee_details_collection.find_one(
        {"user_id": str(user["_id"])},
        {"reporting_manager_id": 1},
    )
    manager_id = details.get("reporting_manager_id") if details else None
    return str(manager_id) if manager_id else None


def _direct_report_ids(manager_id: str) -> set[str]:
    manager_ids: list[str | ObjectId] = [manager_id]
    if ObjectId.is_valid(manager_id):
        manager_ids.append(ObjectId(manager_id))
    report_ids = {
        str(record["_id"])
        for record in users_collection.find(
            {"is_active": True, "reporting_manager_id": {"$in": manager_ids}},
            {"_id": 1},
        )
    }
    detail_records = employee_details_collection.find(
        {"reporting_manager_id": {"$in": manager_ids}},
        {"user_id": 1},
    )
    for record in detail_records:
        employee_id = str(record.get("user_id", ""))
        if not ObjectId.is_valid(employee_id):
            continue
        employee = users_collection.find_one(
            {"_id": ObjectId(employee_id), "is_active": True},
            {"_id": 1},
        )
        if employee:
            report_ids.add(employee_id)
    return report_ids


def _is_reporting_manager_for(record: dict[str, Any], approver: dict[str, Any]) -> bool:
    manager_id = str(approver["_id"])
    assigned_manager_id = record.get("reporting_manager_id")
    if assigned_manager_id:
        return str(assigned_manager_id) == manager_id
    employee = users_collection.find_one(
        {"_id": _parse_object_id(record["user_id"], "employee user")},
        {"reporting_manager_id": 1},
    )
    return bool(employee and _get_reporting_manager_id(employee) == manager_id)


def _notify_user_role(role: str, title: str, message: str, request_id: str | None = None) -> None:
    for approver in users_collection.find({"is_active": True, "role": {"$regex": f"^{role}$", "$options": "i"}}):
        _notify(str(approver["_id"]), title, message, request_id=request_id)


def _notify_approvers_of_cancellation(
    record: dict[str, Any],
    old_status: str,
    request_id: str,
) -> None:
    employee_id = str(record.get("user_id", ""))
    employee = (
        users_collection.find_one(
            {"_id": ObjectId(employee_id)},
            {"name": 1, "employee_id": 1},
        )
        if ObjectId.is_valid(employee_id)
        else None
    )
    employee_name = employee.get("name", "Employee") if employee else "Employee"
    display_employee_id = (
        str(employee.get("employee_id") or employee_id)
        if employee
        else employee_id or "Unknown"
    )
    days = float(record.get("days", 0))
    message = (
        f"Employee {display_employee_id} - {employee_name} cancelled their "
        f"{days:g} day(s) leave request for {record.get('from_date', 'the selected dates')}."
    )
    notified_user_ids: set[str] = set()

    if old_status in {"pending", "pending_hr_admin", "approved"}:
        manager_id = record.get("reporting_manager_id")
        if not manager_id and employee:
            manager_id = _get_reporting_manager_id(
                {"_id": ObjectId(employee_id), **employee}
            )
        manager_id = str(manager_id) if manager_id else ""
        if ObjectId.is_valid(manager_id):
            manager = users_collection.find_one(
                {"_id": ObjectId(manager_id), "is_active": True},
                {"_id": 1},
            )
            if manager:
                manager_user_id = str(manager["_id"])
                _notify(
                    manager_user_id,
                    "Leave Request Cancelled",
                    message,
                    request_id=request_id,
                )
                notified_user_ids.add(manager_user_id)

    if old_status == "pending_hr_admin" or (
        old_status == "approved" and days >= 2
    ):
        hr_admins = users_collection.find(
            {
                "is_active": True,
                "role": {"$regex": f"^{HR_ADMIN_ROLE}$", "$options": "i"},
            },
            {"_id": 1},
        )
        for approver in hr_admins:
            approver_id = str(approver["_id"])
            if approver_id not in notified_user_ids:
                _notify(
                    approver_id,
                    "Leave Request Cancelled",
                    message,
                    request_id=request_id,
                )
                notified_user_ids.add(approver_id)


def _get_leave_type(leave_type_id: str, active_only: bool = True) -> dict[str, Any]:
    query: dict[str, Any] = {"_id": _parse_object_id(leave_type_id, "leave type")}
    if active_only:
        query["is_active"] = True
    record = leave_types_collection.find_one(query)
    if not record:
        raise HTTPException(status_code=404, detail="Leave type not found")
    return record


def _get_policy(leave_type_id: str) -> dict[str, Any] | None:
    return leave_policies_collection.find_one({"leave_type_id": leave_type_id, "is_active": True})


def _employee_id_for_user(user: dict[str, Any]) -> str | None:
    value = user.get("employee_id")
    return str(value).strip() if value else None


def _joining_date(user: dict[str, Any]) -> date | None:
    value = user.get("date_of_joining")
    if value is None:
        details = employee_details_collection.find_one(
            {"user_id": str(user["_id"])},
            {"date_of_joining": 1},
        )
        value = details.get("date_of_joining") if details else None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _is_sick_leave_type(leave_type: dict[str, Any]) -> bool:
    return str(leave_type.get("code", "")).strip().lower() == "sl" or "sick" in str(
        leave_type.get("name", "")
    ).lower() or "medical" in str(leave_type.get("name", "")).lower()


def _sick_entitlement(user: dict[str, Any], entitlement: float) -> float:
    policy = _company_policy()
    if policy.get("sick_leave_before_probation_allowed"):
        return min(entitlement, float(policy["sick_leave_after_probation"]))
    joining = _joining_date(user)
    if not joining:
        return 0.0
    eligible_from = _add_months(joining, int(policy["probation_period_months"]))
    if company_now().date() < eligible_from:
        return 0.0
    return min(entitlement, float(policy["sick_leave_after_probation"]))


def _holiday_dates(user: dict[str, Any], start: date, end: date) -> set[date]:
    employee_id = _employee_id_for_user(user)
    if not employee_id:
        return set()
    assignment = employee_holiday_calendars_collection.find_one({"employee_id": employee_id, "is_active": True})
    if not assignment:
        return set()
    company_policy = _company_policy()
    employee_details = employee_details_collection.find_one(
        {"user_id": str(user["_id"])},
        {"religion": 1},
    )
    employee_religion = str((employee_details or {}).get("religion", "")).strip().casefold()
    records = holidays_collection.find({"calendar_id": assignment.get("calendar_id"), "is_active": True})
    result: set[date] = set()
    for record in records:
        applicable_religion = str(record.get("applicable_religion") or "").strip().casefold()
        if applicable_religion and (
            not company_policy.get("religion_holidays_enabled")
            or not employee_religion
            or applicable_religion != employee_religion
        ):
            continue
        value = record.get("holiday_date")
        if isinstance(value, datetime):
            value = value.date()
        if isinstance(value, date) and start <= value <= end:
            result.add(value)
    return result


def _working_dates(user: dict[str, Any], start: date, end: date) -> list[date]:
    if end < start:
        raise HTTPException(status_code=400, detail="To date must be on or after from date")
    holidays = _holiday_dates(user, start, end)
    weekly_offs = company_weekly_off_weekdays()
    result: list[date] = []
    current = start
    while current <= end:
        if current.weekday() not in weekly_offs and current not in holidays:
            result.append(current)
        current += timedelta(days=1)
    return result


def _overlap_exists(user_id: str, start: date, end: date, exclude_request_id: str | None = None) -> bool:
    query: dict[str, Any] = {"user_id": user_id, "status": {"$in": ["pending", "approved"]}, "from_date": {"$lte": end.isoformat()}, "to_date": {"$gte": start.isoformat()}}
    if exclude_request_id:
        query["_id"] = {"$ne": _parse_object_id(exclude_request_id, "leave request")}
    return leave_requests_collection.find_one(query) is not None


def _carry_forward(user_id: str, leave_type_id: str, year: int, policy: dict[str, Any]) -> float:
    if not policy.get("carry_forward_allowed") or year <= 2000:
        return 0.0
    previous = leave_balances_collection.find_one({"user_id": user_id, "leave_type_id": leave_type_id, "year": year - 1})
    if not previous:
        return 0.0
    previous_available = max(float(previous.get("entitled", 0)) - float(previous.get("used", 0)), 0.0)
    return min(previous_available, float(policy.get("max_carry_forward", 0)))


def _ensure_balance(user_id: str, leave_type_id: str, year: int) -> dict[str, Any]:
    existing = leave_balances_collection.find_one({"user_id": user_id, "leave_type_id": leave_type_id, "year": year})
    if existing:
        return existing
    policy = _get_policy(leave_type_id) or {}
    annual = float(policy.get("annual_entitlement", 0))
    carry = _carry_forward(user_id, leave_type_id, year, policy)
    now = datetime.now(timezone.utc)
    balance = {"user_id": user_id, "leave_type_id": leave_type_id, "year": year, "annual_entitlement": annual, "carry_forward": carry, "entitled": annual + carry, "used": 0.0, "pending": 0.0, "created_at": now, "updated_at": now}
    try:
        leave_balances_collection.insert_one(balance)
    except DuplicateKeyError:
        pass
    return leave_balances_collection.find_one({"user_id": user_id, "leave_type_id": leave_type_id, "year": year})


def _effective_entitled(balance: dict[str, Any], policy: dict[str, Any] | None, year: int) -> float:
    base = float(balance.get("annual_entitlement", balance.get("entitled", 0)))
    carry = float(balance.get("carry_forward", 0))
    if not policy or not policy.get("accrual_enabled") or policy.get("accrual_frequency") == "annual" or year != datetime.now(timezone.utc).year:
        return base + carry
    now = datetime.now(timezone.utc)
    if policy.get("accrual_frequency") == "monthly":
        accrued = base * now.month / 12
    else:
        accrued = base * ((now.month - 1) // 3 + 1) / 4
    return accrued + carry


def _balance_response(balance: dict[str, Any], leave_type: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
    policy = _get_policy(str(balance["leave_type_id"]))
    entitled = _effective_entitled(balance, policy, int(balance["year"]))
    if _is_sick_leave_type(leave_type):
        entitled = _sick_entitlement(user, entitled)
    used = float(balance.get("used", 0))
    pending = float(balance.get("pending", 0))
    return {"id": str(balance["_id"]), "leave_type_id": str(balance["leave_type_id"]), "code": leave_type.get("code"), "name": leave_type.get("name"), "is_paid": leave_type.get("is_paid", True), "entitled": round(entitled, 2), "annual_entitlement": float(balance.get("annual_entitlement", entitled)), "carry_forward": float(balance.get("carry_forward", 0)), "used": round(used, 2), "pending": round(pending, 2), "available": round(max(entitled - used - pending, 0), 2), "year": balance["year"], "accrual_enabled": bool(policy and policy.get("accrual_enabled"))}


def _can_cancel(record: dict[str, Any], viewer: dict[str, Any] | None) -> bool:
    """Employee can cancel until company start time on the leave start date.
    HR / Admin approvers are exempt from the deadline."""
    if record.get("status") not in {"pending", "approved"}:
        return False
    if viewer and _is_admin_or_hr(viewer):
        return True
    return company_now() < leave_cancel_deadline(record["from_date"])


def _request_response(record: dict[str, Any], viewer: dict[str, Any] | None = None) -> dict[str, Any]:
    result = _serialize(record)
    result.pop("attachment_data", None)
    result.pop("attachment_content_type", None)
    leave_type = leave_types_collection.find_one({"_id": _parse_object_id(result["leave_type_id"], "leave type")})
    result["leave_type_code"] = leave_type.get("code") if leave_type else None
    result["leave_type_name"] = leave_type.get("name") if leave_type else None
    user = users_collection.find_one({"_id": _parse_object_id(result["user_id"], "user")})
    result["employee_name"] = user.get("name") if user else None
    result["employee_id"] = user.get("employee_id") if user else None
    result["day_part_label"] = {"full_day": "Full Day", "first_half": "First Half", "second_half": "Second Half"}.get(result.get("day_part"), "Full Day")
    result["can_cancel"] = _can_cancel(record, viewer)
    result["cancel_deadline"] = leave_cancel_deadline(record["from_date"]).isoformat() if record.get("from_date") else None
    return result


def _update_balance(user_id: str, leave_type_id: str, year: int, used_delta: float = 0.0, pending_delta: float = 0.0) -> None:
    _ensure_balance(user_id, leave_type_id, year)
    leave_balances_collection.update_one({"user_id": user_id, "leave_type_id": leave_type_id, "year": year}, {"$inc": {"used": used_delta, "pending": pending_delta}, "$set": {"updated_at": datetime.now(timezone.utc)}})


def _audit(actor: dict[str, Any], action: str, entity_id: str, details: dict[str, Any] | None = None) -> None:
    try:
        audit_logs_collection.insert_one({"user_id": str(actor["_id"]), "action": action, "entity": "leave", "entity_id": entity_id, "details": details or {}, "created_at": datetime.now(timezone.utc)})
    except Exception:
        pass


def _email_medical_certificate(
    employee: dict[str, Any],
    leave_type: dict[str, Any],
    payload: LeaveApplicationCreate,
    attachment_bytes: bytes,
) -> None:
    recipients = [
        str(user.get("work_email", "")).strip()
        for user in users_collection.find(
            {
                "is_active": True,
                "role": {"$regex": r"^\s*hr admin\s*$", "$options": "i"},
            },
            {"work_email": 1},
        )
        if str(user.get("work_email", "")).strip()
    ]
    if not recipients:
        raise HTTPException(
            status_code=503,
            detail="The medical certificate was not emailed because no active HR Admin has a registered work email. Contact HR Admin to update recipient accounts.",
        )

    filename = (
        str(payload.attachment_name or "medical-certificate")
        .replace("\\", "/")
        .rsplit("/", 1)[-1]
        .replace('"', "")
        .replace("\r", "")
        .replace("\n", "")
    )
    subject = f"Medical certificate for {employee.get('employee_id') or employee.get('name', 'employee')}"
    body = (
        f"Employee: {employee.get('name', 'Employee')}\n"
        f"Employee ID: {employee.get('employee_id') or 'Not provided'}\n"
        f"Leave type: {leave_type.get('name', 'Medical Leave')}\n"
        f"Leave dates: {payload.from_date.isoformat()} to {payload.to_date.isoformat()}\n\n"
        "The medical certificate is attached. The leave request will be submitted after successful email delivery."
    )
    try:
        send_hr_email_with_attachment(
            recipients,
            subject,
            body,
            filename,
            attachment_bytes,
            payload.attachment_content_type or "application/octet-stream",
        )
    except (OSError, smtplib.SMTPException, RuntimeError, ValueError) as error:
        logger.error("Medical certificate email delivery failed (%s).", type(error).__name__)
        raise HTTPException(
            status_code=503,
            detail="The medical certificate could not be emailed to HR Admin, so the leave request was not submitted. Please try again or contact HR Admin.",
        ) from error


def _notify(
    user_id: str,
    title: str,
    message: str,
    notification_type: str = "leave",
    request_id: str | None = None,
) -> None:
    if not ObjectId.is_valid(user_id):
        raise ValueError("Leave notification recipient ID is invalid.")
    recipient = users_collection.find_one({"_id": ObjectId(user_id)}, {"name": 1, "employee_id": 1})
    leave_request = None
    employee = None
    approver = None
    if request_id and ObjectId.is_valid(request_id):
        leave_request = leave_requests_collection.find_one({"_id": ObjectId(request_id)})
        if leave_request:
            employee_id = leave_request.get("user_id")
            employee = users_collection.find_one(
                {"_id": ObjectId(employee_id)} if ObjectId.is_valid(employee_id) else {"_id": None},
                {"name": 1, "employee_id": 1},
            )
            approver_id = leave_request.get("approved_by") or leave_request.get("reporting_manager_approved_by")
            if approver_id and ObjectId.is_valid(approver_id):
                approver = users_collection.find_one({"_id": ObjectId(approver_id)}, {"name": 1})
    now = datetime.now(timezone.utc)
    sequence = notification_sequences_collection.find_one_and_update(
        {"_id": "leave_notification"},
        {"$inc": {"value": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    leave_notifications_collection.insert_one(
        {
            "leave_notification_id": f"L{int(sequence['value']):03d}",
            "recipient_user_id": user_id,
            "employee_name": (employee or recipient or {}).get("name"),
            "employee_id": (employee or recipient or {}).get("employee_id"),
            "leave_request_id": request_id,
            "related_user_id": leave_request.get("user_id") if leave_request else user_id,
            "related_user_name": (employee or recipient or {}).get("name"),
            "approver_user_id": (
                leave_request.get("approved_by") or leave_request.get("reporting_manager_approved_by")
            ) if leave_request else None,
            "approver_name": approver.get("name") if approver else None,
            "notification_type": notification_type,
            "title": title,
            "message": message,
            "is_read": False,
            "is_starred": False,
            "is_deleted": False,
            "created_at": now,
            "expires_at": None,
            "read_at": None,
            "deleted_at": None,
        }
    )


def _notify_reporting_manager(employee: dict[str, Any], title: str, message: str, request_id: str | None = None) -> None:
    manager_id = _get_reporting_manager_id(employee)
    if not manager_id or not ObjectId.is_valid(manager_id):
        return
    manager = users_collection.find_one({"_id": ObjectId(manager_id), "is_active": True})
    if manager:
        _notify(str(manager["_id"]), title, message, request_id=request_id)


def _notify_approvers(title: str, message: str, request_id: str | None = None) -> None:
    for approver in users_collection.find({"is_active": True}):
        if str(approver.get("role", "")).strip().lower() in APPROVER_ROLES:
            _notify(str(approver["_id"]), title, message, request_id=request_id)


def _route_legacy_requests_to_reporting_manager() -> None:
    legacy_requests = leave_requests_collection.find(
        {
            "status": "pending_hr_admin",
            "approval_stage": "hr_admin",
            "$or": [
                {"reporting_manager_approved_by": {"$exists": False}},
                {"reporting_manager_approved_by": None},
            ],
        }
    )
    for record in legacy_requests:
        request_id = str(record.get("_id", ""))
        employee_id = str(record.get("user_id", ""))
        if not ObjectId.is_valid(employee_id):
            logger.warning(
                "Cannot reroute legacy leave request %s: employee ID is invalid.",
                request_id,
            )
            continue
        employee = users_collection.find_one(
            {"_id": ObjectId(employee_id), "is_active": True}
        )
        if not employee:
            logger.warning(
                "Cannot reroute legacy leave request %s: employee is inactive or missing.",
                request_id,
            )
            continue

        manager_id = _get_reporting_manager_id(employee)
        manager = (
            users_collection.find_one(
                {"_id": ObjectId(manager_id), "is_active": True}
            )
            if manager_id and ObjectId.is_valid(manager_id)
            else None
        )
        update: dict[str, dict[str, Any]] = {
            "$set": {
                "status": "pending",
                "approval_stage": "reporting_manager",
                "updated_at": datetime.now(timezone.utc),
            }
        }
        if manager:
            update["$set"]["reporting_manager_id"] = str(manager["_id"])
        else:
            update["$unset"] = {"reporting_manager_id": ""}

        result = leave_requests_collection.update_one(
            {
                "_id": record["_id"],
                "status": "pending_hr_admin",
                "approval_stage": "hr_admin",
                "$or": [
                    {"reporting_manager_approved_by": {"$exists": False}},
                    {"reporting_manager_approved_by": None},
                ],
            },
            update,
        )
        if result.matched_count and manager:
            _notify(
                str(manager["_id"]),
                "Leave Request Requires Reporting Manager Approval",
                f"{employee.get('name', 'Employee')} has a pending leave request that needs your approval before HR Admin review.",
                request_id=request_id,
            )
        elif result.matched_count:
            logger.warning(
                "Legacy leave request %s is waiting for an active Reporting Manager assignment.",
                request_id,
            )


@router.on_event("startup")
def startup_leave_module():
    ensure_leave_indexes()
    _route_legacy_requests_to_reporting_manager()


@router.get("/types")
def list_leave_types(authorization: str | None = Header(default=None)):
    _current_user(authorization)
    return [_serialize(record) for record in leave_types_collection.find({"is_active": True}).sort("name", 1)]


@router.get("/balance")
def get_leave_balance(authorization: str | None = Header(default=None), year: int | None = None):
    user = _current_user(authorization)
    target_year = year or datetime.now(timezone.utc).year
    if target_year < 2000 or target_year > 2100:
        raise HTTPException(status_code=400, detail="Invalid year")
    response_data = []
    for leave_type in leave_types_collection.find({"is_active": True}).sort("name", 1):
        balance = _ensure_balance(str(user["_id"]), str(leave_type["_id"]), target_year)
        response_data.append(_balance_response(balance, leave_type, user))
    return response_data


@router.post("/apply", status_code=status.HTTP_201_CREATED)
def apply_leave(payload: LeaveApplicationCreate, authorization: str | None = Header(default=None)):
    user = _current_user(authorization)
    leave_type = _get_leave_type(payload.leave_type_id)
    if payload.from_date.year != payload.to_date.year:
        raise HTTPException(status_code=400, detail="Leave application cannot cross calendar years. Submit separate applications.")
    if payload.day_part != "full_day":
        if not leave_type.get("allow_half_day", True):
            raise HTTPException(status_code=400, detail="Half-day leave is not allowed for this leave type")
        if payload.from_date != payload.to_date:
            raise HTTPException(status_code=400, detail="Half-day leave can be applied for a single date only")
    company_policy = _company_policy()
    if _is_sick_leave_type(leave_type) and not company_policy.get("sick_leave_before_probation_allowed"):
        joining = _joining_date(user)
        if joining is None:
            raise HTTPException(
                status_code=400,
                detail="Your date of joining is missing. Please ask HR Admin to update your employee profile before applying for Sick Leave.",
            )
        eligible_from = _add_months(joining, int(company_policy["probation_period_months"]))
        if company_now().date() < eligible_from:
            raise HTTPException(
                status_code=400,
                detail=f"Sick Leave is available after your {company_policy['probation_period_months']}-month probation period. Eligibility begins on {eligible_from.isoformat()}.",
            )
    requires_medical_certificate = (
        company_policy.get("medical_certificate_required")
        and _is_sick_leave_type(leave_type)
    )
    attachment_bytes = None
    if requires_medical_certificate:
        if not payload.attachment_content_base64 or not payload.attachment_content_type:
            raise HTTPException(
                status_code=400,
                detail="A medical certificate file is required for Sick/Medical Leave. Upload a PDF, PNG or JPEG certificate before submitting.",
            )
        if payload.attachment_content_type not in {"application/pdf", "image/png", "image/jpeg"}:
            raise HTTPException(
                status_code=400,
                detail="Medical certificate must be a PDF, PNG or JPEG file.",
            )
        try:
            attachment_bytes = base64.b64decode(payload.attachment_content_base64, validate=True)
        except (binascii.Error, ValueError) as error:
            raise HTTPException(status_code=400, detail="Medical certificate file data is invalid. Please upload the file again.") from error
        if not attachment_bytes or len(attachment_bytes) > 5 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Medical certificate must be smaller than 5 MB.")
        valid_signature = (
            payload.attachment_content_type == "application/pdf" and attachment_bytes.startswith(b"%PDF")
        ) or (
            payload.attachment_content_type == "image/png" and attachment_bytes.startswith(b"\x89PNG\r\n\x1a\n")
        ) or (
            payload.attachment_content_type == "image/jpeg" and attachment_bytes.startswith(b"\xff\xd8\xff")
        )
        if not valid_signature:
            raise HTTPException(status_code=400, detail="The selected file content does not match its declared file type.")
    if leave_type.get("requires_attachment") and not payload.attachment_name:
        raise HTTPException(
            status_code=400,
            detail="An attachment reference is required for this leave type.",
        )
    if requires_medical_certificate and not payload.attachment_name:
        raise HTTPException(status_code=400, detail="The medical certificate file name is missing. Please select the certificate again.")
    working_dates = _working_dates(user, payload.from_date, payload.to_date)
    if not working_dates:
        raise HTTPException(status_code=400, detail="Selected dates contain no working days")
    days = float(len(working_dates)) if payload.day_part == "full_day" else 0.5
    user_id = str(user["_id"])
    if _overlap_exists(user_id, payload.from_date, payload.to_date):
        raise HTTPException(status_code=409, detail="You already have pending or approved leave overlapping these dates")
    balance = _ensure_balance(user_id, payload.leave_type_id, payload.from_date.year)
    policy = _get_policy(payload.leave_type_id)
    entitled = _effective_entitled(balance, policy, payload.from_date.year)
    if _is_sick_leave_type(leave_type):
        entitled = _sick_entitlement(user, entitled)
    available = entitled - float(balance.get("used", 0)) - float(balance.get("pending", 0))
    if leave_type.get("is_paid", True) and days > available:
        raise HTTPException(status_code=400, detail=f"Insufficient {leave_type.get('name', 'leave')} balance. Available: {available:g} day(s).")
    now = datetime.now(timezone.utc)
    manager_id = _get_reporting_manager_id(user)
    if not manager_id or not ObjectId.is_valid(manager_id):
        raise HTTPException(status_code=400, detail="A valid Reporting Manager is not assigned. Please ask HR Admin to assign an active Reporting Manager before applying for leave.")
    manager = users_collection.find_one({"_id": ObjectId(manager_id), "is_active": True}, {"_id": 1})
    if not manager or manager_id == user_id:
        raise HTTPException(status_code=400, detail="The assigned Reporting Manager is inactive or invalid. Please ask HR Admin to assign an active Reporting Manager.")

    approval_stage = "reporting_manager"
    initial_status = "pending"
    if requires_medical_certificate:
        _email_medical_certificate(user, leave_type, payload, attachment_bytes)
    record = {
        "user_id": user_id, "leave_type_id": payload.leave_type_id,
        "from_date": payload.from_date.isoformat(), "to_date": payload.to_date.isoformat(),
        "days": days, "day_part": payload.day_part,
        # working days (weekends + holidays removed) - used by Attendance integration
        "leave_dates": [d.isoformat() for d in working_dates],
        "reason": payload.reason.strip(), "attachment_name": payload.attachment_name,
        "status": initial_status, "approval_stage": approval_stage,
        "reporting_manager_id": manager_id,
        "created_at": now, "updated_at": now,
    }
    if attachment_bytes is not None:
        record["attachment_data"] = Binary(attachment_bytes)
        record["attachment_content_type"] = payload.attachment_content_type
    result = leave_requests_collection.insert_one(record)
    record["_id"] = result.inserted_id
    _update_balance(user_id, payload.leave_type_id, payload.from_date.year, pending_delta=days)
    _notify_reporting_manager(user, "New Leave Request", f"{user.get('name', 'Employee')} submitted {days:g} day(s) of {leave_type.get('name')} for your approval.", str(result.inserted_id))
    _audit(user, "LEAVE_APPLIED", str(result.inserted_id), {"days": days, "leave_type": leave_type.get("code")})
    return _request_response(record, user)


@router.get("/my-requests")
def my_leave_requests(authorization: str | None = Header(default=None)):
    user = _current_user(authorization)
    records = leave_requests_collection.find({"user_id": str(user["_id"])}).sort("created_at", -1)
    return [_request_response(record, user) for record in records]


@router.get("/requests/{request_id}")
def get_leave_request(request_id: str, authorization: str | None = Header(default=None)):
    user = _current_user(authorization)
    record = leave_requests_collection.find_one({"_id": _parse_object_id(request_id, "leave request")})
    if not record:
        raise HTTPException(status_code=404, detail="Leave request not found")
    if record["user_id"] != str(user["_id"]) and not _is_admin_or_hr(user):
        raise HTTPException(status_code=403, detail="Access denied")
    return _request_response(record, user)


@router.get("/requests/{request_id}/attachment")
def download_leave_attachment(
    request_id: str,
    authorization: str | None = Header(default=None),
):
    user = _current_user(authorization)
    record = leave_requests_collection.find_one(
        {"_id": _parse_object_id(request_id, "leave request")}
    )
    if not record:
        raise HTTPException(status_code=404, detail="Leave request not found")
    if record.get("user_id") != str(user["_id"]) and not _is_admin_or_hr(user):
        raise HTTPException(status_code=403, detail="Access denied")
    attachment_data = record.get("attachment_data")
    if not attachment_data:
        raise HTTPException(status_code=404, detail="No uploaded certificate is available for this leave request")
    filename = (
        str(record.get("attachment_name") or "leave-certificate")
        .replace("\\", "/")
        .rsplit("/", 1)[-1]
        .replace('"', "")
        .replace("\r", "")
        .replace("\n", "")
    )
    return Response(
        content=bytes(attachment_data),
        media_type=record.get("attachment_content_type") or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/pending")
def pending_leave_requests(authorization: str | None = Header(default=None)):
    user = _current_user(authorization)
    user_id = str(user["_id"])
    direct_report_ids = _direct_report_ids(user_id)
    pending_query: dict[str, Any] = {
        "status": "pending",
        "approval_stage": {"$in": ["reporting_manager", None]},
        "$or": [{"reporting_manager_id": {"$in": [user_id, user["_id"]]}}],
    }
    if direct_report_ids:
        pending_query["$or"].append({"user_id": {"$in": list(direct_report_ids)}})

    is_hr_admin = _is_hr_admin(user)
    if is_hr_admin:
        query = {
            "$or": [
                {"status": "pending_hr_admin", "approval_stage": "hr_admin"},
                pending_query,
            ]
        }
    else:
        query = pending_query

    records = list(leave_requests_collection.find(query).sort("created_at", 1))
    if is_hr_admin:
        return {
            "can_approve": True,
            "approval_role": "hr_admin",
            "requests": [_request_response(record, user) for record in records],
        }
    if not records and not direct_report_ids:
        return {"can_approve": False, "approval_role": None, "requests": []}
    return {
        "can_approve": bool(direct_report_ids or records),
        "approval_role": "reporting_manager" if direct_report_ids or records else None,
        "requests": [_request_response(record, user) for record in records],
    }


@router.put("/requests/{request_id}/approve")
def approve_leave(request_id: str, payload: LeaveDecisionPayload, authorization: str | None = Header(default=None)):
    approver = _current_user(authorization)
    object_id = _parse_object_id(request_id, "leave request")
    record = leave_requests_collection.find_one({"_id": object_id})
    if not record:
        raise HTTPException(status_code=404, detail="Leave request not found")

    is_manager_approval = (
        record.get("status") == "pending"
        and record.get("approval_stage", "reporting_manager") == "reporting_manager"
        and _is_reporting_manager_for(record, approver)
    )
    is_hr_final_approval = (
        _is_hr_admin(approver)
        and record.get("status") == "pending_hr_admin"
        and record.get("approval_stage") == "hr_admin"
    )

    if not is_manager_approval and not is_hr_final_approval:
        raise HTTPException(status_code=403, detail="You are not authorized to approve this leave request at its current approval stage")

    if _overlap_exists(record["user_id"], date.fromisoformat(record["from_date"]), date.fromisoformat(record["to_date"]), request_id):
        raise HTTPException(status_code=409, detail="Another pending or approved leave overlaps this request")

    now = datetime.now(timezone.utc)
    days = float(record["days"])

    if (
        is_manager_approval
        and days >= 2
    ):
        updated = leave_requests_collection.update_one(
            {"_id": object_id, "status": "pending", "approval_stage": "reporting_manager"},
            {"$set": {
                "status": "pending_hr_admin",
                "approval_stage": "hr_admin",
                "reporting_manager_approved_by": str(approver["_id"]),
                "reporting_manager_name": approver.get("name"),
                "reporting_manager_remarks": payload.remarks,
                "reporting_manager_decided_at": now,
                "updated_at": now,
            }},
        )
        if updated.matched_count == 0:
            raise HTTPException(status_code=409, detail="Leave request was already processed")
        _notify_user_role(HR_ADMIN_ROLE, "Leave Awaiting HR Approval", f"{record.get('days', 0):g} day(s) leave request from employee {record.get('user_id')} has been approved by the Reporting Manager and requires final HR Admin approval.", request_id)
        _audit(approver, "LEAVE_MANAGER_APPROVED", request_id, {"employee_user_id": record["user_id"], "days": days})
        pending_record = leave_requests_collection.find_one({"_id": object_id})
        return _request_response(pending_record, approver)

    updated = leave_requests_collection.update_one(
        {"_id": object_id, "status": record.get("status"), "approval_stage": record.get("approval_stage")},
        {"$set": {
            "status": "approved",
            "approval_stage": "completed",
            "approved_by": str(approver["_id"]),
            "approver_name": approver.get("name"),
            "remarks": payload.remarks,
            "decided_at": now,
            "updated_at": now,
        }},
    )
    if updated.matched_count == 0:
        raise HTTPException(status_code=409, detail="Leave request was already processed")

    year = int(record["from_date"][:4])
    _update_balance(record["user_id"], record["leave_type_id"], year, used_delta=days, pending_delta=-days)
    approved_record = leave_requests_collection.find_one({"_id": object_id})
    sync_leave_for_today(approved_record)
    _notify(record["user_id"], "Leave Approved", f"Your {days:g} day(s) leave request has been fully approved.", request_id=request_id)
    _audit(approver, "LEAVE_APPROVED", request_id, {"employee_user_id": record["user_id"], "days": days})
    return _request_response(approved_record, approver)


@router.put("/requests/{request_id}/reject")
def reject_leave(request_id: str, payload: LeaveDecisionPayload, authorization: str | None = Header(default=None)):
    approver = _current_user(authorization)
    object_id = _parse_object_id(request_id, "leave request")
    record = leave_requests_collection.find_one({"_id": object_id})
    if not record:
        raise HTTPException(status_code=404, detail="Leave request not found")

    is_manager_rejection = (
        record.get("status") == "pending"
        and record.get("approval_stage", "reporting_manager") == "reporting_manager"
        and _is_reporting_manager_for(record, approver)
    )
    is_hr_rejection = (
        _is_hr_admin(approver)
        and record.get("status") == "pending_hr_admin"
        and record.get("approval_stage") == "hr_admin"
    )
    if not is_manager_rejection and not is_hr_rejection:
        raise HTTPException(status_code=403, detail="You are not authorized to reject this leave request at its current approval stage")

    now = datetime.now(timezone.utc)
    updated = leave_requests_collection.update_one(
        {"_id": object_id, "status": record.get("status"), "approval_stage": record.get("approval_stage")},
        {"$set": {
            "status": "rejected",
            "approval_stage": "completed",
            "approved_by": str(approver["_id"]),
            "approver_name": approver.get("name"),
            "remarks": payload.remarks,
            "decided_at": now,
            "updated_at": now,
        }},
    )
    if updated.matched_count == 0:
        raise HTTPException(status_code=409, detail="Leave request was already processed")
    year = int(record["from_date"][:4])
    _update_balance(record["user_id"], record["leave_type_id"], year, pending_delta=-float(record["days"]))
    _notify(record["user_id"], "Leave Rejected", f"Your leave request was rejected. {payload.remarks or ''}".strip(), request_id=request_id)
    _audit(approver, "LEAVE_REJECTED", request_id, {"employee_user_id": record["user_id"], "days": record["days"], "stage": "reporting_manager" if is_manager_rejection else "hr_admin"})
    return _request_response(leave_requests_collection.find_one({"_id": object_id}), approver)


@router.put("/requests/{request_id}/cancel")
def cancel_leave(request_id: str, authorization: str | None = Header(default=None)):
    user = _current_user(authorization)
    object_id = _parse_object_id(request_id, "leave request")
    record = leave_requests_collection.find_one({"_id": object_id})
    if not record:
        raise HTTPException(status_code=404, detail="Leave request not found")
    user_id = str(user["_id"])
    if record["user_id"] != user_id and not _is_admin_or_hr(user):
        raise HTTPException(status_code=403, detail="Access denied")
    if record["status"] not in {"pending", "pending_hr_admin", "approved"}:
        raise HTTPException(status_code=400, detail="Only pending or approved leave can be cancelled")
    if not _can_cancel(record, user):
        raise HTTPException(status_code=400, detail=f"Leave can be cancelled only until {COMPANY_START_TIME} on the leave start date ({record['from_date']}).")
    now = datetime.now(timezone.utc)
    old_status = record["status"]
    result = leave_requests_collection.update_one({"_id": object_id, "status": old_status}, {"$set": {"status": "cancelled", "cancelled_by": user_id, "cancelled_at": now, "updated_at": now}})
    if result.matched_count == 0:
        raise HTTPException(status_code=409, detail="Leave request was already processed")
    year = int(record["from_date"][:4])
    days = float(record["days"])
    if old_status in {"pending", "pending_hr_admin"}:
        _update_balance(record["user_id"], record["leave_type_id"], year, pending_delta=-days)
    else:
        _update_balance(record["user_id"], record["leave_type_id"], year, used_delta=-days)
        # Attendance link: remove every "Leave" record created by this request.
        remove_leave_attendance(request_id)
    cancellation_message = (
        f"Your {days:g} day(s) leave request was cancelled by you."
        if record["user_id"] == user_id
        else f"Your {days:g} day(s) leave request was cancelled by {user.get('name', 'HR')}."
    )
    _notify(record["user_id"], "Leave Cancelled", cancellation_message, request_id=request_id)
    if record["user_id"] == user_id:
        _notify_approvers_of_cancellation(record, old_status, request_id)
    _audit(user, "LEAVE_CANCELLED", request_id, {"old_status": old_status, "days": days})
    return _request_response(leave_requests_collection.find_one({"_id": object_id}), user)


@router.post("/types", status_code=status.HTTP_201_CREATED)
def create_leave_type(payload: LeaveTypeCreate, authorization: str | None = Header(default=None)):
    user = _current_user(authorization)
    if not _is_admin_or_hr(user):
        raise HTTPException(status_code=403, detail="Only HR/Admin can manage leave types")
    code = payload.code.upper()
    if leave_types_collection.find_one({"code": code}):
        raise HTTPException(status_code=409, detail="Leave type code already exists")
    now = datetime.now(timezone.utc)
    record = {**payload.model_dump(), "code": code, "created_at": now, "updated_at": now}
    result = leave_types_collection.insert_one(record)
    record["_id"] = result.inserted_id
    _audit(user, "LEAVE_TYPE_CREATED", str(result.inserted_id), {"code": code})
    return _serialize(record)


@router.put("/types/{leave_type_id}")
def update_leave_type(leave_type_id: str, payload: LeaveTypeUpdate, authorization: str | None = Header(default=None)):
    user = _current_user(authorization)
    if not _is_admin_or_hr(user):
        raise HTTPException(status_code=403, detail="Only HR/Admin can manage leave types")
    object_id = _parse_object_id(leave_type_id, "leave type")
    values = {k: v for k, v in payload.model_dump().items() if v is not None}
    if "name" in values:
        values["name"] = values["name"].strip()
    values["updated_at"] = datetime.now(timezone.utc)
    result = leave_types_collection.update_one({"_id": object_id}, {"$set": values})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Leave type not found")
    _audit(user, "LEAVE_TYPE_UPDATED", leave_type_id, values)
    return _serialize(leave_types_collection.find_one({"_id": object_id}))


@router.get("/policies/{leave_type_id}")
def get_leave_policy(leave_type_id: str, authorization: str | None = Header(default=None)):
    user = _current_user(authorization)
    if not _is_admin_or_hr(user):
        raise HTTPException(status_code=403, detail="Only HR/Admin can view leave policies")
    _get_leave_type(leave_type_id, active_only=False)
    policy = leave_policies_collection.find_one({"leave_type_id": leave_type_id})
    if not policy:
        raise HTTPException(status_code=404, detail="Leave policy not found")
    return _serialize(policy)


@router.post("/policies", status_code=status.HTTP_201_CREATED)
def create_or_update_policy(payload: LeavePolicyCreate, authorization: str | None = Header(default=None)):
    user = _current_user(authorization)
    if not _is_admin_or_hr(user):
        raise HTTPException(status_code=403, detail="Only HR/Admin can manage leave policies")
    _get_leave_type(payload.leave_type_id, active_only=False)
    now = datetime.now(timezone.utc)
    values = payload.model_dump()
    values["updated_at"] = now
    leave_policies_collection.update_one({"leave_type_id": payload.leave_type_id}, {"$set": values, "$setOnInsert": {"created_at": now}}, upsert=True)
    record = leave_policies_collection.find_one({"leave_type_id": payload.leave_type_id})
    _audit(user, "LEAVE_POLICY_UPDATED", payload.leave_type_id, values)
    return _serialize(record)


@router.put("/policies/{leave_type_id}")
def update_policy(leave_type_id: str, payload: LeavePolicyUpdate, authorization: str | None = Header(default=None)):
    user = _current_user(authorization)
    if not _is_admin_or_hr(user):
        raise HTTPException(status_code=403, detail="Only HR/Admin can manage leave policies")
    _get_leave_type(leave_type_id, active_only=False)
    values = {k: v for k, v in payload.model_dump().items() if v is not None}
    values["updated_at"] = datetime.now(timezone.utc)
    leave_policies_collection.update_one({"leave_type_id": leave_type_id}, {"$set": values, "$setOnInsert": {"created_at": datetime.now(timezone.utc)}}, upsert=True)
    record = leave_policies_collection.find_one({"leave_type_id": leave_type_id})
    _audit(user, "LEAVE_POLICY_UPDATED", leave_type_id, values)
    return _serialize(record)


@router.get("/company-policy")
def get_company_leave_policy(authorization: str | None = Header(default=None)):
    _current_user(authorization)
    return _serialize(_company_policy())


@router.put("/company-policy")
def update_company_leave_policy(
    payload: LeaveCompanyPolicy,
    authorization: str | None = Header(default=None),
):
    user = _current_user(authorization)
    if not _is_hr_admin(user):
        raise HTTPException(status_code=403, detail="Only HR Admin can manage company leave policy")
    now = datetime.now(timezone.utc)
    values = payload.model_dump()
    leave_policies_collection.update_one(
        {"leave_type_id": "__company_policy__"},
        {
            "$set": {**values, "updated_at": now},
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    saved_policy = _company_policy()
    _audit(user, "COMPANY_LEAVE_POLICY_UPDATED", "__company_policy__", values)
    return _serialize(saved_policy)


@router.get("/dashboard")
def leave_dashboard(authorization: str | None = Header(default=None), year: int | None = None):
    user = _current_user(authorization)
    target_year = year or datetime.now(timezone.utc).year
    own_filter = {} if _is_admin_or_hr(user) else {"user_id": str(user["_id"])}
    requests = list(leave_requests_collection.find({**own_filter, "from_date": {"$regex": f"^{target_year}-"}}))
    summary = {"total_requests": len(requests), "pending": 0, "approved": 0, "rejected": 0, "cancelled": 0, "total_days": 0.0, "approved_days": 0.0}
    by_type: dict[str, float] = {}
    for item in requests:
        state = item.get("status", "pending")
        summary[state] = summary.get(state, 0) + 1
        days = float(item.get("days", 0))
        summary["total_days"] += days
        if state == "approved":
            summary["approved_days"] += days
        code_record = leave_types_collection.find_one({"_id": _parse_object_id(item["leave_type_id"], "leave type")})
        code = code_record.get("code", "Unknown") if code_record else "Unknown"
        by_type[code] = by_type.get(code, 0) + days
    summary["total_days"] = round(summary["total_days"], 2)
    summary["approved_days"] = round(summary["approved_days"], 2)
    return {"year": target_year, "summary": summary, "days_by_leave_type": by_type}


@router.get("/reports")
def leave_report(authorization: str | None = Header(default=None), year: int | None = None, month: int | None = Query(default=None, ge=1, le=12), status_filter: str | None = Query(default=None, alias="status"), employee_id: str | None = None, leave_type_id: str | None = None):
    user = _current_user(authorization)
    if not _is_admin_or_hr(user):
        raise HTTPException(status_code=403, detail="Only HR/Admin can view leave reports")
    target_year = year or datetime.now(timezone.utc).year
    query: dict[str, Any] = {}
    if status_filter:
        query["status"] = status_filter.lower()
    if employee_id:
        target = users_collection.find_one({"employee_id": employee_id})
        query["user_id"] = str(target["_id"]) if target else "__not_found__"
    if leave_type_id:
        query["leave_type_id"] = leave_type_id
    records = []
    for record in leave_requests_collection.find(query).sort("created_at", -1):
        if not str(record.get("from_date", "")).startswith(f"{target_year}-"):
            continue
        if month and int(record["from_date"][5:7]) != month:
            continue
        records.append(_request_response(record, user))
    totals = {"requests": len(records), "days": round(sum(float(r.get("days", 0)) for r in records), 2), "approved_days": round(sum(float(r.get("days", 0)) for r in records if r.get("status") == "approved"), 2)}
    return {"year": target_year, "month": month, "totals": totals, "rows": records}


@router.get("/reports/csv")
def leave_report_csv(authorization: str | None = Header(default=None), year: int | None = None, month: int | None = Query(default=None, ge=1, le=12)):
    # Explicit None values: calling an endpoint function directly would otherwise
    # pass FastAPI's Query(...) default objects (truthy) into the filters.
    data = leave_report(authorization=authorization, year=year, month=month, status_filter=None, employee_id=None, leave_type_id=None)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Employee ID", "Employee Name", "Leave Type", "From", "To", "Days", "Day Part", "Status", "Reason", "Approver", "Remarks"])
    for row in data["rows"]:
        writer.writerow([row.get("employee_id", ""), row.get("employee_name", ""), row.get("leave_type_name", ""), row.get("from_date", ""), row.get("to_date", ""), row.get("days", ""), row.get("day_part_label", ""), row.get("status", ""), row.get("reason", ""), row.get("approver_name", ""), row.get("remarks", "")])
    suffix = f"-{data['month']:02d}" if data["month"] else ""
    filename = f"leave-report-{data['year']}{suffix}.csv"
    return Response(content=output.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/notifications")
def leave_notifications(authorization: str | None = Header(default=None), limit: int = Query(default=20, ge=1, le=100)):
    user = _current_user(authorization)
    now = datetime.now(timezone.utc)
    records = leave_notifications_collection.find(
        {
            "recipient_user_id": str(user["_id"]),
            "is_deleted": {"$ne": True},
            "deleted_at": None,
            "$or": [{"expires_at": {"$gt": now}}, {"expires_at": None}, {"expires_at": {"$exists": False}}],
        }
    ).sort("created_at", -1).limit(limit)
    return [_serialize(record) for record in records]

# Employee permission workflow (separate from full/half-day leave requests).
class PermissionApplicationCreate(BaseModel):
    permission_date: date
    start_time: str = Field(min_length=5, max_length=5)
    end_time: str = Field(min_length=5, max_length=5)
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_time(cls, value: str) -> str:
        try:
            datetime.strptime(value, "%H:%M")
        except ValueError as exc:
            raise ValueError("Time must use 24-hour HH:MM format") from exc
        return value

    @field_validator("reason")
    @classmethod
    def validate_permission_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Reason is required")
        return value

    def validate_interval(self) -> None:
        if self.end_time <= self.start_time:
            raise HTTPException(status_code=422, detail="End time must be later than start time on the selected date.")


def _permission_response(record: dict[str, Any]) -> dict[str, Any]:
    result = _serialize(record)
    employee = users_collection.find_one({"_id": _parse_object_id(result["user_id"], "employee user")})
    result["employee_name"] = employee.get("name") if employee else "Employee"
    result["employee_id"] = employee.get("employee_id") if employee else None
    return result


def _permission_notify(recipient_id: str, title: str, message: str, permission_id: str) -> None:
    # Reuse the established Leave notification collection, ID sequence, and UI.
    if not ObjectId.is_valid(recipient_id):
        return
    recipient = users_collection.find_one({"_id": ObjectId(recipient_id)})
    if not recipient:
        return
    sequence = notification_sequences_collection.find_one_and_update(
        {"_id": "leave_notification"}, {"$inc": {"value": 1}},
        upsert=True, return_document=ReturnDocument.AFTER,
    )
    leave_notifications_collection.insert_one({
        "leave_notification_id": f"L{int(sequence['value']):03d}",
        "recipient_user_id": recipient_id,
        "employee_name": recipient.get("name"),
        "employee_id": recipient.get("employee_id"),
        "leave_request_id": None,
        "permission_request_id": permission_id,
        "related_user_id": recipient_id,
        "related_user_name": recipient.get("name"),
        "notification_type": "permission", "title": title, "message": message,
        "is_read": False, "is_starred": False, "is_deleted": False,
        "created_at": datetime.now(timezone.utc), "expires_at": None,
        "read_at": None, "deleted_at": None,
    })


@router.post("/permissions", status_code=status.HTTP_201_CREATED)
def apply_permission(payload: PermissionApplicationCreate, authorization: str | None = Header(default=None)):
    payload.validate_interval()
    user = _current_user(authorization)
    manager_id = _get_reporting_manager_id(user)
    if not manager_id or not ObjectId.is_valid(manager_id):
        raise HTTPException(status_code=422, detail="A reporting manager must be assigned before applying for permission.")
    if payload.permission_date < company_now().date():
        raise HTTPException(status_code=422, detail="Permission cannot be applied for a past date.")
    overlapping = permission_requests_collection.find_one({
        "user_id": str(user["_id"]), "permission_date": payload.permission_date.isoformat(),
        "status": {"$in": ["pending", "approved"]},
        "start_time": {"$lt": payload.end_time}, "end_time": {"$gt": payload.start_time},
    })
    if overlapping:
        raise HTTPException(status_code=409, detail="This permission overlaps an existing pending or approved request.")
    now = datetime.now(timezone.utc)
    record = {
        "user_id": str(user["_id"]), "reporting_manager_id": manager_id,
        "permission_date": payload.permission_date.isoformat(), "start_time": payload.start_time,
        "end_time": payload.end_time, "reason": payload.reason, "status": "pending",
        "created_at": now, "updated_at": now,
    }
    result = permission_requests_collection.insert_one(record)
    record["_id"] = result.inserted_id
    manager = users_collection.find_one({"_id": ObjectId(manager_id), "is_active": True})
    if manager:
        _permission_notify(manager_id, "Permission approval required", f"{user.get('name', 'Employee')} requested permission on {payload.permission_date.isoformat()} from {payload.start_time} to {payload.end_time}.", str(result.inserted_id))
    _audit(user, "PERMISSION_SUBMITTED", str(result.inserted_id), {"permission_date": payload.permission_date.isoformat()})
    return _permission_response(record)


@router.get("/permissions/my-requests")
def my_permission_requests(authorization: str | None = Header(default=None)):
    user = _current_user(authorization)
    records = permission_requests_collection.find({"user_id": str(user["_id"])}).sort("created_at", -1)
    return [_permission_response(record) for record in records]


@router.get("/permissions/pending")
def pending_permission_requests(authorization: str | None = Header(default=None)):
    user = _current_user(authorization)
    role = str(user.get("role", "")).strip().casefold()
    query: dict[str, Any] = {"status": "pending", "reporting_manager_id": str(user["_id"])}
    if role in APPROVER_ROLES:
        query = {"status": "pending"}
    records = permission_requests_collection.find(query).sort("created_at", 1)
    return {"can_approve": bool(role in APPROVER_ROLES or permission_requests_collection.count_documents(query)), "requests": [_permission_response(record) for record in records]}


def _decide_permission(request_id: str, approved: bool, approver: dict[str, Any], remarks: str | None) -> dict[str, Any]:
    record = permission_requests_collection.find_one({"_id": _parse_object_id(request_id, "permission request")})
    if not record:
        raise HTTPException(status_code=404, detail="Permission request not found")
    if record.get("status") != "pending":
        raise HTTPException(status_code=409, detail="This permission request has already been decided.")
    role = str(approver.get("role", "")).strip().casefold()
    if str(record.get("reporting_manager_id")) != str(approver["_id"]) and role not in APPROVER_ROLES:
        raise HTTPException(status_code=403, detail="Only the assigned reporting manager or authorized HR role can decide this request.")
    now = datetime.now(timezone.utc)
    new_status = "approved" if approved else "rejected"
    result = permission_requests_collection.update_one(
        {"_id": record["_id"], "status": "pending"},
        {"$set": {"status": new_status, "decided_by": str(approver["_id"]), "decision_remarks": remarks, "decided_at": now, "updated_at": now}},
    )
    if result.modified_count != 1:
        raise HTTPException(status_code=409, detail="This permission request was already processed.")
    employee_id = str(record["user_id"])
    employee = users_collection.find_one({"_id": _parse_object_id(employee_id, "employee user")})
    if approved:
        # Preserve original biometric/check-in/out data. Attach the approved interval
        # as auditable metadata to the matching attendance document when one exists.
        attendance = db["attendance"]
        attendance.update_one(
            {"user_id": employee_id, "attendance_date": record["permission_date"]},
            {"$set": {"approved_permission": {"request_id": request_id, "start_time": record["start_time"], "end_time": record["end_time"], "reason": record["reason"], "approved_by": str(approver["_id"]), "approved_at": now}}},
        )
        _permission_notify(employee_id, "Permission approved", f"Your permission for {record['permission_date']} from {record['start_time']} to {record['end_time']} was approved.", request_id)
    else:
        _permission_notify(employee_id, "Permission rejected", f"Your permission request for {record['permission_date']} from {record['start_time']} to {record['end_time']} was rejected.", request_id)
    _audit(approver, "PERMISSION_APPROVED" if approved else "PERMISSION_REJECTED", request_id, {"employee_user_id": employee_id, "remarks": remarks})
    updated = permission_requests_collection.find_one({"_id": record["_id"]})
    return _permission_response(updated)


@router.put("/permissions/{request_id}/approve")
def approve_permission(request_id: str, payload: LeaveDecisionPayload, authorization: str | None = Header(default=None)):
    return _decide_permission(request_id, True, _current_user(authorization), payload.remarks)


@router.put("/permissions/{request_id}/reject")
def reject_permission(request_id: str, payload: LeaveDecisionPayload, authorization: str | None = Header(default=None)):
    return _decide_permission(request_id, False, _current_user(authorization), payload.remarks)
