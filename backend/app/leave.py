from __future__ import annotations

import csv
import io
import calendar
import base64
import binascii
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from bson import ObjectId
from bson.binary import Binary
from fastapi import APIRouter, Header, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, field_validator
from pymongo.errors import DuplicateKeyError
from pymongo import ReturnDocument

from app.attendance import (
    CALENDAR_FORWARD_DAYS,
    COMPANY_START_TIME,
    WEEKDAY_NAMES,
    company_now,
    company_weekly_off_weekdays,
    employee_joining_date,
    leave_cancel_deadline,
    remove_leave_attendance,
    sync_leave_for_today,
)
from app.db.mongodb import db
from app.session import authenticate_token

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
leave_ml_carryforwards_collection = db["leave_ml_carryforwards"]
leave_ml_cycles_collection = db["leave_ml_cycles"]
# Atomic per-employee/per-month CL request claims prevent concurrent duplicate reservations.
leave_cl_monthly_claims_collection = db["leave_cl_monthly_claims"]
logger = logging.getLogger(__name__)

APPROVER_ROLES = {"hr admin", "administrator", "hr manager"}
# Medical Leave (code ML) supports policy-controlled supporting documents; allocation is policy-configurable.
MEDICAL_LEAVE_CODE = "ML"
# Legacy attendance-correction requests retain their one-day/month ML correction cap.
# New CL/ML leave applications use the Leave Management balance and six-month ML ledger.
MEDICAL_LEAVE_DAYS_PER_MONTH = 1
HR_ADMIN_ROLE = "hr admin"
# The HOD role keeps its stored identifier "HR Manager" (permissions and
# role_id depend on it); only the displayed label is "HOD".
HOD_ROLE = "hr manager"
PENDING_STATUSES = ("pending", "pending_hr_admin")
# Approval stage -> (assignment field on the user, required approver role, label).
APPROVAL_STAGES: dict[str, tuple[str, str | None, str]] = {
    "reporting_manager": ("reporting_manager_id", None, "Reporting Manager"),
    "hod": ("hod_id", HOD_ROLE, "HOD"),
    "hr_admin": ("hr_admin_id", HR_ADMIN_ROLE, "HR Admin"),
}


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
    annual_entitlement: float = Field(default=0, ge=0)
    allocation_period: str = "annual"
    monthly_entitlement: float = Field(default=0, ge=0)
    is_unlimited: bool = False
    eligible_work_stages: list[str] = Field(default_factory=lambda: ["Probation", "Employee"])
    carry_forward_allowed: bool = False
    max_carry_forward: float = Field(default=0, ge=0)
    accrual_enabled: bool = False
    accrual_frequency: str = "annual"
    is_active: bool = True

    @field_validator("allocation_period")
    @classmethod
    def valid_allocation_period(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"monthly", "annual", "unlimited"}:
            raise ValueError("Allocation period must be monthly, annual or unlimited")
        return value

    @field_validator("eligible_work_stages")
    @classmethod
    def valid_eligible_stages(cls, value: list[str]) -> list[str]:
        allowed = {"Probation", "Employee", "Internship"}
        cleaned = list(dict.fromkeys(str(item).strip().title() for item in value if str(item).strip()))
        if not set(cleaned).issubset(allowed):
            raise ValueError("Eligible Work Stages may contain only Probation, Employee, or Internship.")
        return cleaned

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
    allocation_period: str | None = None
    monthly_entitlement: float | None = Field(default=None, ge=0)
    is_unlimited: bool | None = None
    eligible_work_stages: list[str] | None = None
    carry_forward_allowed: bool | None = None
    max_carry_forward: float | None = Field(default=None, ge=0)
    accrual_enabled: bool | None = None
    accrual_frequency: str | None = None
    is_active: bool | None = None

    @field_validator("allocation_period")
    @classmethod
    def valid_update_allocation_period(cls, value: str | None) -> str | None:
        if value is None: return None
        value = value.strip().lower()
        if value not in {"monthly", "annual", "unlimited"}:
            raise ValueError("Allocation period must be monthly, annual or unlimited")
        return value

    @field_validator("eligible_work_stages")
    @classmethod
    def valid_update_eligible_stages(cls, value: list[str] | None) -> list[str] | None:
        if value is None: return None
        allowed = {"Probation", "Employee", "Internship"}
        cleaned = list(dict.fromkeys(str(item).strip().title() for item in value if str(item).strip()))
        if not set(cleaned).issubset(allowed):
            raise ValueError("Eligible Work Stages may contain only Probation, Employee, or Internship.")
        return cleaned

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
    weekly_offs_per_month: int = Field(default=4, ge=1, le=4)
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
    leave_ml_carryforwards_collection.create_index([("user_id", 1), ("month", 1)], unique=True)
    leave_ml_cycles_collection.create_index([("user_id", 1), ("cycle_start", 1)], unique=True)
    leave_cl_monthly_claims_collection.create_index([("user_id", 1), ("leave_type_id", 1), ("month", 1)], unique=True)
    leave_cl_monthly_claims_collection.create_index("request_id")
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
    # EL is retired from the live module. Historical requests/balances remain intact.
    leave_types_collection.update_many({"code": "EL"}, {"$set": {"is_active": False, "updated_at": datetime.now(timezone.utc)}})
    leave_types_collection.update_many({"code": {"$in": ["CL", "ML"]}}, {"$set": {"is_paid": True}})
    leave_types_collection.update_many({"code": "LOP"}, {"$set": {"is_paid": False}})
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
        {"code": MEDICAL_LEAVE_CODE, "name": "Medical Leave", "description": "Medical leave funded by unused monthly Casual Leave within a six-month cycle", "is_paid": True, "allow_half_day": True, "requires_attachment": False, "is_active": True},
        {"code": "LOP", "name": "Loss of Pay", "description": "Leave without pay", "is_paid": False, "allow_half_day": True, "requires_attachment": False, "is_active": True},
    ]
    default_entitlements = {"CL": 12, MEDICAL_LEAVE_CODE: 0, "LOP": 0}
    now = datetime.now(timezone.utc)
    _migrate_sl_to_medical_leave(now)
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
        defaults_for_type = {
            "leave_type_id": leave_type_id, "annual_entitlement": entitlement,
            "carry_forward_allowed": False, "max_carry_forward": 0,
            "accrual_enabled": False, "accrual_frequency": "annual",
            "is_active": True, "created_at": now, "updated_at": now,
        }
        if code == "CL":
            defaults_for_type.update({"allocation_period": "monthly", "monthly_entitlement": max(entitlement / 12, 0)})
        elif code == MEDICAL_LEAVE_CODE:
            defaults_for_type.update({"allocation_period": "annual", "monthly_entitlement": 0})
        leave_policies_collection.update_one(
            {"leave_type_id": leave_type_id},
            {"$setOnInsert": defaults_for_type},
            upsert=True,
        )
        if code == "CL":
            existing_policy = leave_policies_collection.find_one({"leave_type_id": leave_type_id}) or {}
            if existing_policy.get("allocation_period") != "monthly" or "monthly_entitlement" not in existing_policy:
                monthly = float(existing_policy.get("monthly_entitlement", 0) or 0)
                if "monthly_entitlement" not in existing_policy or monthly <= 0:
                    annual_value = float(existing_policy.get("annual_entitlement", entitlement) or entitlement)
                    monthly = round(annual_value / 12, 2)
                leave_policies_collection.update_one(
                    {"leave_type_id": leave_type_id},
                    {"$set": {"allocation_period": "monthly", "monthly_entitlement": monthly, "updated_at": now}},
                )


def _migrate_sl_to_medical_leave(now: datetime) -> None:
    """Replace the retired SL leave type with ML (Medical Leave). Idempotent."""
    medical = leave_types_collection.find_one({"code": MEDICAL_LEAVE_CODE})
    for retired in list(leave_types_collection.find({"code": "SL"})):
        if not medical:
            # Keep the same _id so balances, policies and requests stay linked.
            leave_types_collection.update_one(
                {"_id": retired["_id"]},
                {"$set": {"code": MEDICAL_LEAVE_CODE, "name": "Medical Leave", "description": "Medical leave - 1 day per month", "updated_at": now}},
            )
            medical = leave_types_collection.find_one({"_id": retired["_id"]})
            continue
        retired_id, medical_id = str(retired["_id"]), str(medical["_id"])
        leave_requests_collection.update_many({"leave_type_id": retired_id}, {"$set": {"leave_type_id": medical_id}})
        for balance in leave_balances_collection.find({"leave_type_id": retired_id}):
            used, pending = float(balance.get("used", 0)), float(balance.get("pending", 0))
            if used or pending:
                _ensure_balance(balance["user_id"], medical_id, int(balance["year"]))
                leave_balances_collection.update_one(
                    {"user_id": balance["user_id"], "leave_type_id": medical_id, "year": balance["year"]},
                    {"$inc": {"used": used, "pending": pending}, "$set": {"updated_at": now}},
                )
            leave_balances_collection.delete_one({"_id": balance["_id"]})
        leave_policies_collection.delete_many({"leave_type_id": retired_id})
        leave_types_collection.delete_one({"_id": retired["_id"]})
    db["attendance"].update_many({"leave_type_code": "SL"}, {"$set": {"leave_type_code": MEDICAL_LEAVE_CODE}})


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
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.casefold() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="Authentication required")
    user = authenticate_token(token.strip())
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


def _applicant_category(user: dict[str, Any]) -> str:
    """Role precedence for routing: HR Admin > HOD > Reporting Manager > employee.

    A Reporting Manager is anyone with at least one active direct report
    (the existing Team Members rule), whatever their stored role.
    """
    if _is_hr_admin(user):
        return "hr_admin"
    if str(user.get("role", "")).strip().lower() == HOD_ROLE:
        return "hod"
    if _direct_report_ids(str(user["_id"])):
        return "reporting_manager"
    return "employee"


def _required_approval_stage(category: str, days: float) -> str:
    """Approval level for an applicant category and leave duration (working days)."""
    if category in {"hr_admin", "hod"}:
        return "hr_admin"
    if category == "reporting_manager":
        return "hod" if days <= 1 else "hr_admin"
    if days <= 1:
        return "reporting_manager"
    if days <= 2:
        return "hod"
    return "hr_admin"


# Permission requests: one approver by applicant category (duration does not matter).
PERMISSION_STAGES = {
    "employee": "reporting_manager",
    "reporting_manager": "hod",
    "hod": "hr_admin",
    "hr_admin": "hr_admin",
}


def resolve_leave_approver(user: dict[str, Any], days: float) -> tuple[str, dict[str, Any]]:
    """The single approver for this applicant and duration, validated.

    Raises HTTP 400 with a clear message when the assigned approver is
    missing, inactive, lacks the required role or is the applicant.
    """
    stage = _required_approval_stage(_applicant_category(user), days)
    return stage, _resolve_stage_approver(user, stage, f"{days:g} day(s) of leave")


def resolve_permission_approver(user: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Validated approver for a permission request."""
    stage = PERMISSION_STAGES[_applicant_category(user)]
    return stage, _resolve_stage_approver(user, stage, "permission requests", status_code=422)


def _resolve_stage_approver(user: dict[str, Any], stage: str, duration: str, status_code: int = 400) -> dict[str, Any]:
    field, required_role, label = APPROVAL_STAGES[stage]
    approver_id = (
        _get_reporting_manager_id(user)
        if stage == "reporting_manager"
        else (str(user[field]) if user.get(field) else None)
    )
    if not approver_id or not ObjectId.is_valid(approver_id):
        raise HTTPException(
            status_code=status_code,
            detail=f"No {label} is assigned to approve {duration}. Please ask HR Admin to assign a valid {label} in your employee details.",
        )
    approver = users_collection.find_one({"_id": ObjectId(approver_id), "is_active": True})
    if (
        not approver
        or approver_id == str(user["_id"])
        or (required_role and str(approver.get("role", "")).strip().lower() != required_role)
    ):
        raise HTTPException(
            status_code=status_code,
            detail=f"The assigned {label} cannot approve {duration}: the account is inactive, does not have the {label} role, or is the applicant. Please ask HR Admin to assign a valid {label} in your employee details.",
        )
    return approver


def _history_entry(action: str, stage: str | None, approver: dict[str, Any] | None, now: datetime, **extra: Any) -> dict[str, Any]:
    return {
        "action": action,
        "stage": stage,
        "approver_id": str(approver["_id"]) if approver else None,
        "approver_name": approver.get("name") if approver else None,
        "at": now,
        **extra,
    }


def _reroute_pending_request(record: dict[str, Any]) -> str:
    """Recalculate the approver of one pending request. Returns the outcome."""
    request_id = str(record["_id"])
    employee_id = str(record.get("user_id", ""))
    employee = users_collection.find_one({"_id": ObjectId(employee_id)}) if ObjectId.is_valid(employee_id) else None
    now = datetime.now(timezone.utc)
    match = {"_id": record["_id"], "status": record.get("status"), "approver_id": record.get("approver_id")}
    try:
        if not employee:
            raise HTTPException(status_code=400, detail="The applicant account is missing.")
        stage, approver = resolve_leave_approver(employee, float(record.get("days", 0)))
    except HTTPException as exc:
        # Never auto-approve, reject or hand the request to an arbitrary user.
        if record.get("approver_id") or record.get("routing_problem") != exc.detail:
            leave_requests_collection.update_one(match, {
                "$set": {"approver_id": None, "routing_problem": exc.detail, "updated_at": now},
                "$push": {"approval_history": _history_entry("unassigned", None, None, now, reason=exc.detail)},
            })
            logger.warning("Leave request %s has no valid approver: %s", request_id, exc.detail)
        return "unassigned"
    approver_id = str(approver["_id"])
    if record.get("approver_id") == approver_id and record.get("approval_stage") == stage and record.get("status") == "pending":
        if record.get("routing_problem"):
            leave_requests_collection.update_one(match, {"$unset": {"routing_problem": ""}})
        return "unchanged"
    result = leave_requests_collection.update_one(match, {
        "$set": {"status": "pending", "approval_stage": stage, "approver_id": approver_id, "updated_at": now},
        "$unset": {"routing_problem": ""},
        "$push": {"approval_history": _history_entry(
            "reassigned" if record.get("approver_id") else "assigned", stage, approver, now,
            previous_approver_id=record.get("approver_id"),
        )},
    })
    if result.matched_count:
        name = employee.get("name", "Employee")
        _notify(
            approver_id,
            "Leave Request Assigned for Approval",
            f"{name}'s {float(record.get('days', 0)):g} day(s) leave request from {record.get('from_date')} is now assigned to you for {APPROVAL_STAGES[stage][2]} approval.",
            request_id=request_id,
        )
        return "reassigned"
    return "conflict"


def reroute_pending_leave_requests(user_ids: list[str] | set[str]) -> int:
    """Re-evaluate pending leave of these applicants and of requests they approve.

    Called when HR Admin changes reporting assignments, roles or account status.
    Completed requests are never touched.
    """
    ids = sorted({str(value) for value in user_ids if value})
    if not ids:
        return 0
    changed = 0
    for record in list(leave_requests_collection.find({
        "status": {"$in": list(PENDING_STATUSES)},
        "$or": [{"user_id": {"$in": ids}}, {"approver_id": {"$in": ids}}],
    })):
        changed += _reroute_pending_request(record) == "reassigned"
    return changed


def _reconcile_pending_leave_routing() -> None:
    """Startup migration: give every legacy pending request a single valid approver."""
    for record in list(leave_requests_collection.find({
        "status": {"$in": list(PENDING_STATUSES)},
        "approver_id": {"$exists": False},
    })):
        _reroute_pending_request(record)


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
    if old_status not in {"pending", "pending_hr_admin", "approved"}:
        return
    # Only the approver this request was assigned to (or who approved it).
    if old_status == "approved":
        approver_id = record.get("approved_by")
    else:
        approver_id = record.get("approver_id")
    if not approver_id and "approver_id" not in record:
        # Requests created before single-approver routing.
        approver_id = record.get("reporting_manager_id")
        if not approver_id and employee:
            approver_id = _get_reporting_manager_id({"_id": ObjectId(employee_id), **employee})
    approver_id = str(approver_id) if approver_id else ""
    if ObjectId.is_valid(approver_id) and approver_id != employee_id:
        approver = users_collection.find_one({"_id": ObjectId(approver_id), "is_active": True}, {"_id": 1})
        if approver:
            _notify(str(approver["_id"]), "Leave Request Cancelled", message, request_id=request_id)


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


def _employee_work_stage(user: dict[str, Any]) -> str:
    details = employee_details_collection.find_one({"user_id": str(user.get("_id"))}, {"work_stage": 1}) or {}
    value = str(details.get("work_stage") or user.get("work_stage") or "Employee").strip().title()
    return value if value in {"Probation", "Employee", "Internship"} else "Employee"


def _leave_allocation_period(leave_type: dict[str, Any], policy: dict[str, Any] | None) -> str:
    if str(leave_type.get("code", "")).upper() == "LOP":
        return "unlimited"
    if policy and policy.get("allocation_period") in {"monthly", "annual", "unlimited"}:
        return str(policy["allocation_period"])
    if policy and policy.get("accrual_enabled") and policy.get("accrual_frequency") == "monthly":
        return "monthly"
    return "annual"


def _leave_stage_allowed(user: dict[str, Any], leave_type: dict[str, Any]) -> bool:
    policy = leave_policies_collection.find_one({"leave_type_id": str(leave_type["_id"])}) or {}
    eligible = policy.get("eligible_work_stages")
    if eligible is None:
        eligible = ["Probation", "Employee"]
    return _employee_work_stage(user) in eligible


def _monthly_leave_usage(
    user_id: str,
    leave_type_id: str,
    year: int,
    month: int,
    exclude_attendance_request_id: str | None = None,
) -> tuple[float, float]:
    start = date(year, month, 1)
    end = date(year + (month == 12), (month % 12) + 1, 1)
    used = pending = 0.0
    for request in leave_requests_collection.find({
        "user_id": user_id, "leave_type_id": leave_type_id,
        "status": {"$in": ["approved", "pending", "pending_hr_admin"]},
        "from_date": {"$lt": end.isoformat()}, "to_date": {"$gte": start.isoformat()},
    }):
        dates = request.get("leave_dates") or []
        month_dates = [str(value)[:10] for value in dates if str(value)[:7] == f"{year:04d}-{month:02d}"]
        if not month_dates:
            continue
        day_value = 0.5 if request.get("day_part") in {"first_half", "second_half"} else 1.0
        amount = len(month_dates) * day_value
        if request.get("status") == "approved":
            used += amount
        else:
            pending += amount
    leave_type = leave_types_collection.find_one({"_id": ObjectId(leave_type_id)}, {"code": 1})
    leave_code = str((leave_type or {}).get("code") or "").upper()
    if leave_code in {"CL", MEDICAL_LEAVE_CODE}:
        for record in db["attendance"].find({
            "user_id": user_id,
            "attendance_date": {"$gte": start.isoformat(), "$lt": end.isoformat()},
            "leave_type_code": leave_code,
            "attendance_source": {"$in": ["HISTORICAL_ATTENDANCE_CORRECTION", "ATTENDANCE_REQUEST"]},
            "leave_request_id": {"$in": [None, ""]},
        }, {"leave_day_part": 1}):
            used += 0.5 if record.get("leave_day_part") in {"first_half", "second_half"} else 1.0
        query: dict[str, Any] = {
            "user_id": user_id,
            "status": {"$in": ["pending", "processing"]},
            "request_type": {"$ne": "weekly_off_selection"},
            "periods": start.strftime("%Y-%m"),
        }
        if exclude_attendance_request_id and ObjectId.is_valid(exclude_attendance_request_id):
            query["_id"] = {"$ne": ObjectId(exclude_attendance_request_id)}
        requested_status = "Casual Leave" if leave_code == "CL" else "Medical Leave"
        for request in db["attendance_requests"].find(query, {"dates": 1}):
            pending += sum(
                1.0 for item in request.get("dates", [])
                if str(item.get("attendance_date", "")).startswith(start.strftime("%Y-%m"))
                and item.get("requested_status") == requested_status
            )
    return round(used, 2), round(pending, 2)


def _reserve_cl_monthly_claims(user_id: str, leave_type_id: str, working_dates: list[date], request_id: str) -> None:
    """Reserve one CL request per employee/month, including concurrent submissions."""
    months = sorted({value.strftime("%Y-%m") for value in working_dates})
    reserved_months: list[str] = []
    try:
        for month_key in months:
            claim = {
                "user_id": user_id, "leave_type_id": leave_type_id,
                "month": month_key, "request_id": request_id,
                "status": "pending", "created_at": datetime.now(timezone.utc),
            }
            leave_cl_monthly_claims_collection.insert_one(claim)
            reserved_months.append(month_key)
    except DuplicateKeyError as error:
        if reserved_months:
            leave_cl_monthly_claims_collection.delete_many({"request_id": request_id, "month": {"$in": reserved_months}})
        raise HTTPException(
            status_code=409,
            detail=f"You have already submitted or used your Casual Leave for {month_key}. Only one CL request is allowed per calendar month.",
        ) from error
    except Exception:
        if reserved_months:
            leave_cl_monthly_claims_collection.delete_many({"request_id": request_id, "month": {"$in": reserved_months}})
        raise


def _release_cl_monthly_claims(request_id: str) -> None:
    if request_id:
        leave_cl_monthly_claims_collection.delete_many({"request_id": request_id})


def _commit_cl_monthly_claims(request_id: str) -> None:
    if request_id:
        leave_cl_monthly_claims_collection.update_many(
            {"request_id": request_id},
            {"$set": {"status": "approved", "approved_at": datetime.now(timezone.utc)}},
        )


def _employee_id_for_user(user: dict[str, Any]) -> str | None:
    value = user.get("employee_id")
    return str(value).strip() if value else None


def _joining_date(user: dict[str, Any]) -> date | None:
    return employee_joining_date(user)


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _is_medical_leave_type(leave_type: dict[str, Any]) -> bool:
    return str(leave_type.get("code", "")).strip().upper() == MEDICAL_LEAVE_CODE


def _holiday_dates(user: dict[str, Any], start: date, end: date) -> set[date]:
    return set(_holiday_names(user, start, end))


def _holiday_names(user: dict[str, Any], start: date, end: date) -> dict[date, str]:
    """Holiday dates with names: published religion calendar plus assigned calendar."""
    from app.religion_holidays import published_religion_holidays

    result = {
        day: str(holiday.get("holiday_name") or "Holiday")
        for day, holiday in published_religion_holidays(user, start, end).items()
    }
    for day in _assigned_holiday_dates(user, start, end):
        result.setdefault(day, "Holiday")
    return result


def _assigned_holiday_dates(user: dict[str, Any], start: date, end: date) -> set[date]:
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
    leave_type = leave_types_collection.find_one({"_id": _parse_object_id(leave_type_id, "leave type")})
    if (leave_type and _is_medical_leave_type(leave_type)) or not policy.get("carry_forward_allowed") or year <= 2000:
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
    if (not policy or policy.get("allocation_period") in {"monthly", "annual", "unlimited"} or not policy.get("accrual_enabled") or policy.get("accrual_frequency") == "annual" or year != datetime.now(timezone.utc).year):
        return base + carry
    now = datetime.now(timezone.utc)
    if policy.get("accrual_frequency") == "monthly":
        accrued = base * now.month / 12
    else:
        accrued = base * ((now.month - 1) // 3 + 1) / 4
    return accrued + carry


def _six_month_cycle(joined: date, as_of: date) -> tuple[date, date]:
    """Return the joining-month anchored six-month cycle [start, end)."""
    joined = date(joined.year, joined.month, 1)
    if as_of < joined:
        return joined, _add_months(joined, 6)
    months = (as_of.year - joined.year) * 12 + as_of.month - joined.month
    cycle_number = max(months, 0) // 6
    start = _add_months(joined, cycle_number * 6)
    if start > as_of:
        cycle_number -= 1
        start = _add_months(joined, max(cycle_number, 0) * 6)
    return start, _add_months(start, 6)


def _ml_cycle_balance(
    user: dict[str, Any],
    as_of: date,
    process_rollovers: bool = True,
    exclude_request_id: str | None = None,
) -> dict[str, Any]:
    """Persist unused monthly CL credits and the employee's six-month ML ledgers."""
    user_id = str(user["_id"])
    joined = _joining_date(user) or as_of
    cycle_start, cycle_end = _six_month_cycle(joined, as_of)
    cl_type = leave_types_collection.find_one({"code": "CL", "is_active": True})
    ml_type = leave_types_collection.find_one({"code": MEDICAL_LEAVE_CODE, "is_active": True})
    if not ml_type:
        return {"cycle_start": cycle_start.isoformat(), "cycle_end": cycle_end.isoformat(), "cycle_end_display": (cycle_end - timedelta(days=1)).isoformat(), "credited": 0.0, "used": 0.0, "pending": 0.0, "available": 0.0}
    ml_id = str(ml_type["_id"])
    now = datetime.now(timezone.utc)

    def ensure_cycle(start: date, end: date) -> dict[str, Any]:
        key = {"user_id": user_id, "cycle_start": start.isoformat()}
        try:
            leave_ml_cycles_collection.update_one(
                key,
                {"$setOnInsert": {
                    "user_id": user_id, "employee_id": _employee_id_for_user(user),
                    "leave_type_id": ml_id, "cycle_start": start.isoformat(),
                    "cycle_end": end.isoformat(), "credited": 0.0,
                    "created_at": now,
                }, "$set": {"updated_at": now}},
                upsert=True,
            )
        except DuplicateKeyError:
            pass
        return leave_ml_cycles_collection.find_one(key) or {}

    cycle_doc = ensure_cycle(cycle_start, cycle_end)
    if process_rollovers and cl_type:
        cl_id = str(cl_type["_id"])
        cl_policy = _get_policy(cl_id) or {}
        # Company policy may configure the monthly amount, but CL is capped at one day per month.
        monthly_limit = min(float(cl_policy.get("monthly_entitlement", 0) or 0), 1.0)
        month_cursor = date(joined.year, joined.month, 1)
        current_month = date(as_of.year, as_of.month, 1)
        while month_cursor < current_month:
            month_key = month_cursor.strftime("%Y-%m")
            month_cycle_start, month_cycle_end = _six_month_cycle(joined, month_cursor)
            ensure_cycle(month_cycle_start, month_cycle_end)
            month_used, month_pending = _monthly_leave_usage(user_id, cl_id, month_cursor.year, month_cursor.month)
            # Wait for every in-month CL request to be approved/rejected before
            # finalizing the rollover; otherwise a pending reservation could be
            # lost or later counted twice.
            if month_pending > 0:
                month_cursor = _add_months(month_cursor, 1)
                continue
            unused = max(monthly_limit - month_used, 0.0)
            event = {
                "user_id": user_id, "employee_id": _employee_id_for_user(user),
                "month": month_key, "cycle_start": month_cycle_start.isoformat(),
                "cl_leave_type_id": cl_id, "ml_leave_type_id": ml_id,
                "allocated": round(monthly_limit, 2), "used": round(month_used, 2),
                "pending_reserved": round(month_pending, 2), "credited": round(unused, 2),
                "created_at": now,
            }
            try:
                leave_ml_carryforwards_collection.insert_one(event)
            except DuplicateKeyError:
                # Existing month event is authoritative: never credit a month twice.
                pass
            month_cursor = _add_months(month_cursor, 1)

        # Rebuild each stored cycle total from its immutable monthly events.
        # This also repairs a process interruption between event insertion and
        # balance update without risking duplicate credits.
        for stored_cycle in leave_ml_cycles_collection.find({"user_id": user_id}, {"cycle_start": 1, "cycle_end": 1}):
            cycle_key = stored_cycle.get("cycle_start")
            if not cycle_key:
                continue
            credits = sum(
                float(item.get("credited", 0) or 0)
                for item in leave_ml_carryforwards_collection.find(
                    {"user_id": user_id, "cycle_start": cycle_key}, {"credited": 1}
                )
            )
            leave_ml_cycles_collection.update_one(
                {"user_id": user_id, "cycle_start": cycle_key},
                {"$set": {"credited": round(credits, 2), "updated_at": now}},
            )
        cycle_doc = leave_ml_cycles_collection.find_one({"user_id": user_id, "cycle_start": cycle_start.isoformat()}) or cycle_doc

    # Count ML requests in this exact six-month cycle, including pending reservations.
    used = pending = 0.0
    for request in leave_requests_collection.find({
        "user_id": user_id, "leave_type_id": ml_id,
        "status": {"$in": ["approved", "pending", "pending_hr_admin"]},
    }, {"leave_dates": 1, "day_part": 1, "status": 1}):
        dates = [date.fromisoformat(str(value)[:10]) for value in (request.get("leave_dates") or []) if value]
        count = sum(cycle_start <= selected_date < cycle_end for selected_date in dates)
        amount = count * (0.5 if request.get("day_part") in {"first_half", "second_half"} else 1.0)
        if request.get("status") == "approved":
            used += amount
        else:
            pending += amount
    for record in db["attendance"].find({
        "user_id": user_id,
        "attendance_date": {"$gte": cycle_start.isoformat(), "$lt": cycle_end.isoformat()},
        "leave_type_code": MEDICAL_LEAVE_CODE,
        "attendance_source": {"$in": ["HISTORICAL_ATTENDANCE_CORRECTION", "ATTENDANCE_REQUEST"]},
        "leave_request_id": {"$in": [None, ""]},
    }, {"leave_day_part": 1}):
        used += 0.5 if record.get("leave_day_part") in {"first_half", "second_half"} else 1.0
    attendance_request_query: dict[str, Any] = {
        "user_id": user_id,
        "status": {"$in": ["pending", "processing"]},
        "request_type": {"$ne": "weekly_off_selection"},
    }
    if exclude_request_id and ObjectId.is_valid(exclude_request_id):
        attendance_request_query["_id"] = {"$ne": ObjectId(exclude_request_id)}
    for request in db["attendance_requests"].find(attendance_request_query, {"dates": 1}):
        pending += sum(
            1.0 for item in request.get("dates", [])
            if cycle_start.isoformat() <= str(item.get("attendance_date", "")) < cycle_end.isoformat()
            and item.get("requested_status") == "Medical Leave"
        )
    credited = float(cycle_doc.get("credited", 0) or 0)
    return {
        "cycle_start": cycle_start.isoformat(), "cycle_end": cycle_end.isoformat(),
        "cycle_end_display": (cycle_end - timedelta(days=1)).isoformat(),
        "credited": round(credited, 2), "used": round(used, 2),
        "pending": round(pending, 2), "available": round(max(credited - used - pending, 0.0), 2),
    }


def _balance_response(balance: dict[str, Any], leave_type: dict[str, Any], user: dict[str, Any], as_of_date: date | None = None) -> dict[str, Any]:
    policy = _get_policy(str(balance["leave_type_id"]))
    entitled = _effective_entitled(balance, policy, int(balance["year"]))
    used = float(balance.get("used", 0))
    pending = float(balance.get("pending", 0))
    carry_forward = float(balance.get("carry_forward", 0))
    displayed_entitled = entitled
    available = max(entitled - used - pending, 0.0)
    allocation_period = _leave_allocation_period(leave_type, policy)
    code = str(leave_type.get("code") or "").upper()
    cycle_start = cycle_end = None
    if code == MEDICAL_LEAVE_CODE:
        ml = _ml_cycle_balance(user, company_now().date())
        cycle_start, cycle_end = ml["cycle_start"], ml["cycle_end_display"]
        displayed_entitled = ml["credited"]
        used, pending = ml["used"], ml["pending"]
        available = ml["available"]
        carry_forward = ml["credited"]
        allocation_period = "six_month_cycle"
    elif code == "CL":
        now_local = as_of_date or company_now().date()
        monthly_entitlement = min(float((policy or {}).get("monthly_entitlement", 0) or 0), 1.0)
        used, pending = _monthly_leave_usage(str(user["_id"]), str(balance["leave_type_id"]), now_local.year, now_local.month)
        displayed_entitled = monthly_entitlement
        available = max(displayed_entitled - used - pending, 0.0)
    monthly_entitlement = float((policy or {}).get("monthly_entitlement", 0) or 0)
    if code == "CL":
        monthly_entitlement = min(monthly_entitlement, 1.0)
    if allocation_period == "monthly" and code not in {"CL", MEDICAL_LEAVE_CODE}:
        now_local = company_now().date()
        used, pending = _monthly_leave_usage(str(user["_id"]), str(balance["leave_type_id"]), now_local.year, now_local.month)
        displayed_entitled = monthly_entitlement
        available = max(displayed_entitled - used - pending, 0.0)
    elif allocation_period == "unlimited":
        displayed_entitled = "Unlimited"
        available = "Unlimited"
    return {"id": str(balance["_id"]), "leave_type_id": str(balance["leave_type_id"]), "code": leave_type.get("code"), "name": leave_type.get("name"), "is_paid": leave_type.get("is_paid", True), "allocation_period": allocation_period, "monthly_entitlement": monthly_entitlement, "entitled": displayed_entitled if isinstance(displayed_entitled, str) else round(displayed_entitled, 2), "allocated": displayed_entitled if isinstance(displayed_entitled, str) else round(displayed_entitled, 2), "annual_entitlement": float(balance.get("annual_entitlement", entitled)), "carry_forward": carry_forward, "used": round(used, 2), "pending": round(pending, 2), "available": available if isinstance(available, str) else round(available, 2), "year": balance["year"], "accrual_enabled": bool(policy and policy.get("accrual_enabled")), "cycle_start": cycle_start, "cycle_end": cycle_end, "eligible_work_stages": (policy or {}).get("eligible_work_stages", ["Probation", "Employee"])}


def _can_cancel(record: dict[str, Any], viewer: dict[str, Any] | None) -> bool:
    """Employee can cancel until company start time on the leave start date.
    HR / Admin are exempt from the deadline only for other employees' requests."""
    if record.get("status") not in {"pending", "approved"}:
        return False
    if (record.get("cancellation") or {}).get("status") == "pending":
        return False
    # HR / Admin may cancel other employees' leave after the deadline, but the
    # deadline always applies to their own leave requests.
    if viewer and _is_admin_or_hr(viewer) and str(record.get("user_id")) != str(viewer.get("_id")):
        return True
    if not record.get("from_date"):
        return False
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
    approver_id = str(record.get("approver_id") or "")
    assigned = users_collection.find_one({"_id": ObjectId(approver_id)}, {"name": 1}) if ObjectId.is_valid(approver_id) else None
    result["assigned_approver_name"] = assigned.get("name") if assigned else None
    stage = record.get("approved_stage") if record.get("approval_stage") == "completed" else record.get("approval_stage")
    result["approval_level"] = APPROVAL_STAGES.get(stage or "", (None, None, None))[2]
    result.pop("approval_history", None)
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


def _safe_notify(send: Any, *args: Any, **kwargs: Any) -> bool:
    """Send one notification; the saved request stays authoritative if it fails."""
    try:
        send(*args, **kwargs)
        return True
    except Exception:  # noqa: BLE001 - delivery failure must not undo a saved request
        logger.exception("Notification could not be created")
        return False


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


@router.on_event("startup")
def startup_leave_module():
    ensure_leave_indexes()
    _reconcile_pending_leave_routing()


@router.get("/types")
def list_leave_types(authorization: str | None = Header(default=None)):
    user = _current_user(authorization)
    records = list(leave_types_collection.find({"is_active": True, "code": {"$ne": "EL"}}).sort("name", 1))
    if not _is_admin_or_hr(user):
        records = [record for record in records if _leave_stage_allowed(user, record)]
    return [_serialize(record) for record in records]


@router.get("/balance")
def get_leave_balance(authorization: str | None = Header(default=None), year: int | None = None, month: int | None = None):
    user = _current_user(authorization)
    target_year = year or company_now().year
    if target_year < 2000 or target_year > 2100:
        raise HTTPException(status_code=400, detail="Invalid year")
    if month is not None and not 1 <= month <= 12:
        raise HTTPException(status_code=400, detail="Invalid month")
    as_of_date = date(target_year, month, 1) if month is not None else company_now().date()
    response_data = []
    for leave_type in leave_types_collection.find({"is_active": True, "code": {"$ne": "EL"}}).sort("name", 1):
        if not _is_admin_or_hr(user) and not _leave_stage_allowed(user, leave_type):
            continue
        balance = _ensure_balance(str(user["_id"]), str(leave_type["_id"]), target_year)
        response_data.append(_balance_response(balance, leave_type, user, as_of_date=as_of_date))
    return response_data


# Permission can be applied for today and the next 14 days.
PERMISSION_FORWARD_DAYS = 14


def _validate_leave_window(from_date: date, to_date: date) -> None:
    """Leave dates must fall between today and the 90th day (today + 89)."""
    today = company_now().date()
    last_day = today + timedelta(days=CALENDAR_FORWARD_DAYS)
    if from_date < today:
        raise HTTPException(status_code=400, detail="Leave cannot be applied for a past date. Choose today or a later date.")
    if to_date > last_day:
        raise HTTPException(status_code=400, detail=f"Leave can be applied only up to {last_day.isoformat()} (90 days including today).")


@router.get("/approval-preview")
def leave_approval_preview(
    from_date: date,
    to_date: date,
    day_part: str = Query(default="full_day", pattern="^(full_day|first_half|second_half)$"),
    authorization: str | None = Header(default=None),
):
    """Duration and approver the backend would use; informational only."""
    user = _current_user(authorization)
    if day_part != "full_day" and from_date != to_date:
        raise HTTPException(status_code=400, detail="Half-day leave can be applied for a single date only")
    try:
        _validate_leave_window(from_date, to_date)
    except HTTPException as exc:
        return {"days": 0, "approval_stage": None, "approval_level": None, "approver_name": None, "problem": exc.detail}
    working_dates = _working_dates(user, from_date, to_date)
    days = float(len(working_dates)) if day_part == "full_day" else (0.5 if working_dates else 0.0)
    if not days:
        return {"days": 0, "approval_stage": None, "approval_level": None, "approver_name": None, "problem": "Selected dates contain no working days"}
    try:
        stage, approver = resolve_leave_approver(user, days)
    except HTTPException as exc:
        return {"days": days, "approval_stage": None, "approval_level": None, "approver_name": None, "problem": exc.detail}
    return {"days": days, "approval_stage": stage, "approval_level": APPROVAL_STAGES[stage][2], "approver_name": approver.get("name"), "problem": None}


@router.post("/apply", status_code=status.HTTP_201_CREATED)
def apply_leave(payload: LeaveApplicationCreate, authorization: str | None = Header(default=None)):
    user = _current_user(authorization)
    leave_type = _get_leave_type(payload.leave_type_id)
    _validate_leave_window(payload.from_date, payload.to_date)
    if payload.day_part != "full_day":
        if not leave_type.get("allow_half_day", True):
            raise HTTPException(status_code=400, detail="Half-day leave is not allowed for this leave type")
        if payload.from_date != payload.to_date:
            raise HTTPException(status_code=400, detail="Half-day leave can be applied for a single date only")
    company_policy = _company_policy()
    is_medical_leave = _is_medical_leave_type(leave_type)
    attachment_bytes = None
    attachment_mime_type = None
    has_attachment_metadata = bool(
        payload.attachment_content_base64 or payload.attachment_content_type or payload.attachment_name
    )
    requires_supporting_document = is_medical_leave and bool(company_policy.get("medical_certificate_required", True))

    # The company rule controls whether ML requires a supporting document.
    # Validate the actual binary signature instead of trusting browser MIME metadata.
    if requires_supporting_document and not payload.attachment_content_base64:
        raise HTTPException(status_code=400, detail="A supporting document is required for Medical Leave. Please upload a PDF, PNG or JPEG file.")
    if has_attachment_metadata:
        if not is_medical_leave:
            raise HTTPException(status_code=400, detail="Supporting documents can only be uploaded with ML - Medical Leave requests.")
        if not payload.attachment_content_base64 or not payload.attachment_name:
            raise HTTPException(status_code=400, detail="Please select a complete supporting document file.")
        try:
            attachment_bytes = base64.b64decode(payload.attachment_content_base64, validate=True)
        except (binascii.Error, ValueError) as error:
            raise HTTPException(status_code=400, detail="Supporting document data is invalid. Please upload the file again.") from error
        if not attachment_bytes or len(attachment_bytes) > 5 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Supporting document must be a non-empty file smaller than 5 MB.")

        # Detect MIME from file signatures. PNG/JPEG signatures use actual byte
        # escapes (not literal backslash characters), fixing the previous PNG rejection.
        if attachment_bytes.startswith(b"%PDF-"):
            detected_mime = "application/pdf"
        elif attachment_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            detected_mime = "image/png"
        elif attachment_bytes.startswith(b"\xff\xd8\xff"):
            detected_mime = "image/jpeg"
        else:
            raise HTTPException(status_code=400, detail="Unsupported or invalid file content. Upload a genuine PDF, PNG or JPEG document.")

        declared_mime = (payload.attachment_content_type or "").split(";", 1)[0].strip().lower()
        mime_aliases = {"image/jpg": "image/jpeg", "image/pjpeg": "image/jpeg", "image/x-png": "image/png", "application/x-pdf": "application/pdf", "": detected_mime, "application/octet-stream": detected_mime}
        declared_mime = mime_aliases.get(declared_mime, declared_mime)
        if declared_mime != detected_mime:
            raise HTTPException(status_code=400, detail="The selected file content does not match its declared file type. Please select the original file again.")
        attachment_mime_type = detected_mime
    working_dates = _working_dates(user, payload.from_date, payload.to_date)
    if not working_dates:
        raise HTTPException(status_code=400, detail="Selected dates contain no working days")
    # ML entitlement is the employee's accumulated unused CL within the active
    # six-month joining-date cycle; it is not subject to the legacy one-day/month cap.
    days = float(len(working_dates)) if payload.day_part == "full_day" else 0.5
    user_id = str(user["_id"])
    if _overlap_exists(user_id, payload.from_date, payload.to_date):
        raise HTTPException(status_code=409, detail="You already have pending or approved leave overlapping these dates")
    if not _leave_stage_allowed(user, leave_type):
        raise HTTPException(status_code=403, detail=f"{leave_type.get('name', 'This leave type')} is not available for your current Work Stage ({_employee_work_stage(user)}). Contact HR Admin if your Work Stage is incorrect.")
    balance = _ensure_balance(user_id, payload.leave_type_id, payload.from_date.year)
    policy = _get_policy(payload.leave_type_id) or {}
    if _is_medical_leave_type(leave_type):
        cycle = _ml_cycle_balance(user, company_now().date())
        if any(not (date.fromisoformat(cycle["cycle_start"]) <= value < date.fromisoformat(cycle["cycle_end"])) for value in working_dates):
            raise HTTPException(status_code=400, detail="Medical Leave dates must fall within the current six-month cycle.")
        if days > cycle["available"] + 1e-9:
            raise HTTPException(status_code=400, detail=f"Insufficient Medical Leave balance for the current six-month cycle. Available: {cycle['available']:g} day(s).")
    allocation_period = _leave_allocation_period(leave_type, policy)
    if leave_type.get("is_paid", True) and allocation_period == "monthly":
        monthly_limit = min(float(policy.get("monthly_entitlement", 0) or 0), 1.0) if str(leave_type.get("code") or "").upper() == "CL" else float(policy.get("monthly_entitlement", 0) or 0)
        if monthly_limit <= 0:
            raise HTTPException(status_code=400, detail=f"A monthly allocation must be configured for {leave_type.get('name', 'this leave type')}.")
        requested_by_month: dict[tuple[int, int], float] = {}
        for selected_day in working_dates:
            month_key = (selected_day.year, selected_day.month)
            requested_by_month[month_key] = requested_by_month.get(month_key, 0.0) + (0.5 if payload.day_part != "full_day" else 1.0)
        for (selected_year, selected_month), requested_month_days in requested_by_month.items():
            used_month, pending_month = _monthly_leave_usage(user_id, payload.leave_type_id, selected_year, selected_month)
            if str(leave_type.get("code") or "").upper() == "CL" and used_month + pending_month > 1e-9:
                raise HTTPException(status_code=409, detail=f"You have already submitted or used your Casual Leave for {calendar.month_name[selected_month]} {selected_year}. Only one CL request is allowed per calendar month.")
            if used_month + pending_month + requested_month_days > monthly_limit + 1e-9:
                available_month = max(monthly_limit - used_month - pending_month, 0.0)
                raise HTTPException(status_code=400, detail=f"Insufficient {leave_type.get('name', 'leave')} balance for {calendar.month_name[selected_month]} {selected_year}. Available: {available_month:g} day(s).")
    elif not _is_medical_leave_type(leave_type):
        entitled = _effective_entitled(balance, policy, payload.from_date.year)
        available = entitled - float(balance.get("used", 0)) - float(balance.get("pending", 0))
        if leave_type.get("is_paid", True) and allocation_period != "unlimited" and days > available:
            raise HTTPException(status_code=400, detail=f"Insufficient {leave_type.get('name', 'leave')} balance. Available: {available:g} day(s).")
    now = datetime.now(timezone.utc)
    # Single approver from the applicant's role, assignments and working-day count.
    approval_stage, approver = resolve_leave_approver(user, days)
    approver_id = str(approver["_id"])
    manager_id = _get_reporting_manager_id(user)
    initial_status = "pending"
    record = {
        "user_id": user_id, "leave_type_id": payload.leave_type_id,
        "from_date": payload.from_date.isoformat(), "to_date": payload.to_date.isoformat(),
        "days": days, "day_part": payload.day_part,
        # working days (weekends + holidays removed) - used by Attendance integration
        "leave_dates": [d.isoformat() for d in working_dates],
        "reason": payload.reason.strip(), "attachment_name": payload.attachment_name,
        "status": initial_status, "approval_stage": approval_stage,
        "approver_id": approver_id,
        "reporting_manager_id": manager_id,
        "approval_history": [_history_entry("assigned", approval_stage, approver, now)],
        "created_at": now, "updated_at": now,
    }
    if attachment_bytes is not None:
        record["attachment_data"] = Binary(attachment_bytes)
        record["attachment_content_type"] = attachment_mime_type
    if str(leave_type.get("code") or "").upper() == "CL":
        request_id = str(ObjectId())
        record["_id"] = ObjectId(request_id)
        leave_requests_collection.insert_one(record)
        try:
            # The request exists before its unique month claims are acquired, so
            # a process restart cannot leave an orphan reservation without a request.
            _reserve_cl_monthly_claims(user_id, payload.leave_type_id, working_dates, request_id)
        except Exception:
            leave_requests_collection.delete_one({"_id": ObjectId(request_id), "status": "pending"})
            raise
        inserted_id = record["_id"]
    else:
        result = leave_requests_collection.insert_one(record)
        record["_id"] = result.inserted_id
        inserted_id = result.inserted_id
    _update_balance(user_id, payload.leave_type_id, payload.from_date.year, pending_delta=days)
    record["approver_notified"] = _safe_notify(_notify, approver_id, "New Leave Request", f"{user.get('name', 'Employee')} submitted {days:g} day(s) of {leave_type.get('name')} for your {APPROVAL_STAGES[approval_stage][2]} approval.", request_id=str(inserted_id))
    _audit(user, "LEAVE_APPLIED", str(inserted_id), {"days": days, "leave_type": leave_type.get("code"), "approval_stage": approval_stage, "approver_id": approver_id})
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
    if record["user_id"] != str(user["_id"]) and not _is_admin_or_hr(user) and record.get("approver_id") != str(user["_id"]):
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
    if (
        record.get("user_id") != str(user["_id"])
        and record.get("approver_id") != str(user["_id"])
        and not _is_admin_or_hr(user)
    ):
        raise HTTPException(status_code=403, detail="Access denied")
    attachment_data = record.get("attachment_data")
    if not attachment_data:
        raise HTTPException(status_code=404, detail="No supporting document is available for this leave request")
    filename = (
        str(record.get("attachment_name") or "supporting-document")
        .replace("\\", "/")
        .rsplit("/", 1)[-1]
        .replace('"', "")
        .replace("\r", "")
        .replace("\n", "")
    )
    return Response(
        content=bytes(attachment_data),
        media_type=record.get("attachment_content_type") or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.get("/pending")
def pending_leave_requests(authorization: str | None = Header(default=None)):
    """Pending requests assigned to the current user as the single approver."""
    user = _current_user(authorization)
    user_id = str(user["_id"])
    records = list(leave_requests_collection.find({
        "status": {"$in": list(PENDING_STATUSES)},
        "approver_id": user_id,
        "user_id": {"$ne": user_id},
    }).sort("created_at", 1))
    role = str(user.get("role", "")).strip().lower()
    if _is_hr_admin(user):
        approval_role = "hr_admin"
    elif role == HOD_ROLE:
        approval_role = "hod"
    elif records or _direct_report_ids(user_id):
        approval_role = "reporting_manager"
    else:
        approval_role = None
    # Cancellation requests for leave this user originally approved.
    cancellations = list(leave_requests_collection.find({
        "status": "approved", "cancellation.status": "pending", "cancellation.approver_id": user_id,
    }).sort("cancellation.requested_at", 1))
    return {
        "can_approve": bool(records or cancellations or approval_role),
        "approval_role": approval_role,
        "requests": [_request_response(record, user) for record in records],
        "cancellation_requests": [_request_response(record, user) for record in cancellations],
    }


def _assert_assigned_approver(record: dict[str, Any], approver: dict[str, Any], action: str) -> None:
    """Only the request's assigned approver may decide, never the applicant."""
    if record.get("status") not in PENDING_STATUSES:
        raise HTTPException(status_code=409, detail=f"This leave request is already {record.get('status')} and cannot be {action}.")
    approver_id = str(approver["_id"])
    if str(record.get("user_id")) == approver_id:
        raise HTTPException(status_code=403, detail="You cannot approve or reject your own leave request.")
    if not record.get("approver_id"):
        raise HTTPException(status_code=409, detail=record.get("routing_problem") or "This leave request has no valid approver yet. HR Admin must assign one in the employee details.")
    if str(record.get("approver_id")) != approver_id:
        raise HTTPException(status_code=403, detail="This leave request is assigned to another approver.")


@router.put("/requests/{request_id}/approve")
def approve_leave(request_id: str, payload: LeaveDecisionPayload, authorization: str | None = Header(default=None)):
    approver = _current_user(authorization)
    object_id = _parse_object_id(request_id, "leave request")
    record = leave_requests_collection.find_one({"_id": object_id})
    if not record:
        raise HTTPException(status_code=404, detail="Leave request not found")
    _assert_assigned_approver(record, approver, "approved")

    if _overlap_exists(record["user_id"], date.fromisoformat(record["from_date"]), date.fromisoformat(record["to_date"]), request_id):
        raise HTTPException(status_code=409, detail="Another pending or approved leave overlaps this request")

    now = datetime.now(timezone.utc)
    days = float(record["days"])
    stage = record.get("approval_stage")
    updated = leave_requests_collection.update_one(
        # Atomic: still pending and still assigned to this approver.
        {"_id": object_id, "status": record.get("status"), "approver_id": str(approver["_id"])},
        {"$set": {
            "status": "approved",
            "approval_stage": "completed",
            "approved_stage": stage,
            "approved_by": str(approver["_id"]),
            "approver_name": approver.get("name"),
            "remarks": payload.remarks,
            "decided_at": now,
            "updated_at": now,
        }, "$push": {"approval_history": _history_entry("approved", stage, approver, now, remarks=payload.remarks)}},
    )
    if updated.matched_count == 0:
        raise HTTPException(status_code=409, detail="Leave request was already processed")

    year = int(record["from_date"][:4])
    _update_balance(record["user_id"], record["leave_type_id"], year, used_delta=days, pending_delta=-days)
    if str((leave_types_collection.find_one({"_id": _parse_object_id(record["leave_type_id"], "leave type")}) or {}).get("code") or "").upper() == "CL":
        _commit_cl_monthly_claims(request_id)
    approved_record = leave_requests_collection.find_one({"_id": object_id})
    sync_leave_for_today(approved_record)
    _notify(record["user_id"], "Leave Approved", f"Your {days:g} day(s) leave request has been approved by {approver.get('name', 'your approver')}.", request_id=request_id)
    _audit(approver, "LEAVE_APPROVED", request_id, {"employee_user_id": record["user_id"], "days": days, "stage": stage})
    return _request_response(approved_record, approver)


@router.put("/requests/{request_id}/reject")
def reject_leave(request_id: str, payload: LeaveDecisionPayload, authorization: str | None = Header(default=None)):
    approver = _current_user(authorization)
    object_id = _parse_object_id(request_id, "leave request")
    record = leave_requests_collection.find_one({"_id": object_id})
    if not record:
        raise HTTPException(status_code=404, detail="Leave request not found")
    _assert_assigned_approver(record, approver, "rejected")

    now = datetime.now(timezone.utc)
    stage = record.get("approval_stage")
    updated = leave_requests_collection.update_one(
        {"_id": object_id, "status": record.get("status"), "approver_id": str(approver["_id"])},
        {"$set": {
            "status": "rejected",
            "approval_stage": "completed",
            "approved_stage": stage,
            "approved_by": str(approver["_id"]),
            "approver_name": approver.get("name"),
            "remarks": payload.remarks,
            "decided_at": now,
            "updated_at": now,
        }, "$push": {"approval_history": _history_entry("rejected", stage, approver, now, remarks=payload.remarks)}},
    )
    if updated.matched_count == 0:
        raise HTTPException(status_code=409, detail="Leave request was already processed")
    year = int(record["from_date"][:4])
    _update_balance(record["user_id"], record["leave_type_id"], year, pending_delta=-float(record["days"]))
    if str((leave_types_collection.find_one({"_id": _parse_object_id(record["leave_type_id"], "leave type")}) or {}).get("code") or "").upper() == "CL":
        _release_cl_monthly_claims(request_id)
    _notify(record["user_id"], "Leave Rejected", f"Your leave request was rejected. {payload.remarks or ''}".strip(), request_id=request_id)
    _audit(approver, "LEAVE_REJECTED", request_id, {"employee_user_id": record["user_id"], "days": record["days"], "stage": stage})
    return _request_response(leave_requests_collection.find_one({"_id": object_id}), approver)


class CancellationRequestPayload(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)


@router.put("/requests/{request_id}/cancel")
def cancel_leave(
    request_id: str,
    authorization: str | None = Header(default=None),
    payload: CancellationRequestPayload | None = None,
):
    """Pending leave is cancelled at once. The applicant's approved leave needs the
    original approver's approval (see decide_leave_cancellation)."""
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
    if (record.get("cancellation") or {}).get("status") == "pending":
        raise HTTPException(status_code=409, detail="A cancellation request for this leave is already waiting for approval.")
    if not _can_cancel(record, user):
        raise HTTPException(status_code=400, detail=f"Leave can be cancelled only until {COMPANY_START_TIME} on the leave start date ({record['from_date']}).")
    now = datetime.now(timezone.utc)
    if record["user_id"] == user_id and record["status"] == "approved":
        # Nothing is reversed yet: the original approver decides the cancellation.
        approver = _original_approver(record.get("approved_by"), user_id)
        _cancellation_request(leave_requests_collection, record, approver, payload.reason if payload else None, now)
        notified = _safe_notify(
            _notify, str(approver["_id"]), "Leave Cancellation Requested",
            f"{user.get('name', 'Employee')} asked to cancel the approved {float(record['days']):g} day(s) leave from {record['from_date']}.",
            request_id=request_id,
        )
        _audit(user, "LEAVE_CANCELLATION_REQUESTED", request_id, {"approver_id": str(approver["_id"])})
        response = _request_response(leave_requests_collection.find_one({"_id": object_id}), user)
        response["approver_notified"] = notified
        return response
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
    if str((leave_types_collection.find_one({"_id": _parse_object_id(record["leave_type_id"], "leave type")}) or {}).get("code") or "").upper() == "CL":
        _release_cl_monthly_claims(request_id)
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
    if code == "EL":
        raise HTTPException(status_code=400, detail="EL - Earned Leave is retired and cannot be created.")
    if leave_types_collection.find_one({"code": code}):
        raise HTTPException(status_code=409, detail="Leave type code already exists")
    now = datetime.now(timezone.utc)
    record = {**payload.model_dump(), "code": code, "created_at": now, "updated_at": now}
    if code in {"CL", "EL", "ML"}: record["is_paid"] = True
    if code == "LOP": record["is_paid"] = False
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
    existing_type = leave_types_collection.find_one({"_id": object_id})
    if not existing_type:
        raise HTTPException(status_code=404, detail="Leave type not found")
    if str(existing_type.get("code") or "").upper() == "EL":
        raise HTTPException(status_code=400, detail="EL - Earned Leave is retired and cannot be modified.")
    values = {k: v for k, v in payload.model_dump().items() if v is not None}
    code = str(leave_types_collection.find_one({"_id": object_id}, {"code": 1}).get("code") or "").upper()
    if code in {"CL", "EL", "ML"}: values["is_paid"] = True
    if code == "LOP": values["is_paid"] = False
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
    selected_type = _get_leave_type(leave_type_id, active_only=False)
    if str(selected_type.get("code") or "").upper() == "EL":
        raise HTTPException(status_code=404, detail="Leave type not found")
    policy = leave_policies_collection.find_one({"leave_type_id": leave_type_id})
    if not policy:
        raise HTTPException(status_code=404, detail="Leave policy not found")
    return _serialize(policy)


@router.post("/policies", status_code=status.HTTP_201_CREATED)
def create_or_update_policy(payload: LeavePolicyCreate, authorization: str | None = Header(default=None)):
    user = _current_user(authorization)
    if not _is_admin_or_hr(user):
        raise HTTPException(status_code=403, detail="Only HR/Admin can manage leave policies")
    selected_type = _get_leave_type(payload.leave_type_id, active_only=False)
    if str(selected_type.get("code") or "").upper() == "EL":
        raise HTTPException(status_code=404, detail="Leave type not found")
    now = datetime.now(timezone.utc)
    values = payload.model_dump()
    if values.get("allocation_period") == "monthly" and float(values.get("monthly_entitlement", 0) or 0) <= 0:
        raise HTTPException(status_code=422, detail="Configure a monthly leave allocation greater than zero.")
    if str(selected_type.get("code", "")).upper() == "LOP":
        values.update({"allocation_period": "unlimited", "is_unlimited": True, "annual_entitlement": 0, "monthly_entitlement": 0})
    else:
        values["is_unlimited"] = False
        if values.get("allocation_period") == "monthly":
            values.update({"carry_forward_allowed": False, "max_carry_forward": 0, "accrual_enabled": False})
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
    selected_type = _get_leave_type(leave_type_id, active_only=False)
    if str(selected_type.get("code") or "").upper() == "EL":
        raise HTTPException(status_code=404, detail="Leave type not found")
    values = {k: v for k, v in payload.model_dump().items() if v is not None}
    current_policy = leave_policies_collection.find_one({"leave_type_id": leave_type_id}) or {}
    if values.get("allocation_period", current_policy.get("allocation_period")) == "monthly":
        monthly_value = values.get("monthly_entitlement", current_policy.get("monthly_entitlement", 0))
        if float(monthly_value or 0) <= 0:
            raise HTTPException(status_code=422, detail="Configure a monthly leave allocation greater than zero.")
    if str(selected_type.get("code", "")).upper() == "LOP":
        values.update({"allocation_period": "unlimited", "is_unlimited": True, "annual_entitlement": 0, "monthly_entitlement": 0})
    elif "allocation_period" in values:
        values["is_unlimited"] = False
        if values.get("allocation_period") == "monthly":
            values.update({"carry_forward_allowed": False, "max_carry_forward": 0, "accrual_enabled": False})
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
        if state == "approved":
            by_type[code] = by_type.get(code, 0) + days
    summary["total_days"] = round(summary["total_days"], 2)
    summary["approved_days"] = round(summary["approved_days"], 2)
    balance_rows = []
    for leave_type in leave_types_collection.find({"is_active": True, "code": {"$ne": "EL"}}).sort("name", 1):
        if not _leave_stage_allowed(user, leave_type):
            continue
        balance = _ensure_balance(str(user["_id"]), str(leave_type["_id"]), target_year)
        balance_rows.append(_balance_response(balance, leave_type, user))
    paid_rows = [row for row in balance_rows if row.get("is_paid")]
    unpaid_used = sum(float(row.get("used", 0) or 0) for row in balance_rows if not row.get("is_paid"))
    def numeric(value):
        try: return float(value)
        except (TypeError, ValueError): return 0.0
    paid_allocated = sum(numeric(row.get("allocated")) for row in paid_rows)
    paid_used = sum(numeric(row.get("used")) for row in paid_rows)
    paid_pending = sum(numeric(row.get("pending")) for row in balance_rows)
    paid_available = sum(numeric(row.get("available")) for row in paid_rows)
    summary["paid_leave_allocated"] = round(paid_allocated, 2)
    summary["paid_leave_used"] = round(paid_used, 2)
    summary["pending_leave_days"] = round(paid_pending, 2)
    summary["remaining_paid_leave"] = round(paid_available, 2)
    summary["unpaid_leave_used"] = round(unpaid_used, 2)
    return {"year": target_year, "summary": summary, "days_by_leave_type": by_type, "leave_type_balances": balance_rows}


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
    approver_id = _permission_approver_id(record)
    approver = users_collection.find_one({"_id": ObjectId(approver_id)}, {"name": 1}) if ObjectId.is_valid(approver_id) else None
    result["approver_name"] = approver.get("name") if approver else None
    result["approval_level"] = APPROVAL_STAGES.get(record.get("approval_stage") or "reporting_manager", (None, None, None))[2]
    cancellation = record.get("cancellation") or {}
    result["can_request_cancellation"] = (
        record.get("status") == "approved"
        and cancellation.get("status") != "pending"
        and str(record.get("permission_date", "")) >= company_now().date().isoformat()
    )
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
    today = company_now().date()
    if payload.permission_date < today:
        raise HTTPException(status_code=422, detail="Permission cannot be applied for a past date.")
    if payload.permission_date == today:
        current_time = company_now().strftime("%H:%M")
        if payload.start_time < current_time:
            raise HTTPException(status_code=422, detail=f"Permission for today must start at the current time ({current_time}) or later.")
    if payload.permission_date > today + timedelta(days=PERMISSION_FORWARD_DAYS):
        raise HTTPException(status_code=422, detail=f"Permission can be applied only from today to {(today + timedelta(days=PERMISSION_FORWARD_DAYS)).isoformat()} (the next {PERMISSION_FORWARD_DAYS} days).")
    # Employee -> Reporting Manager, Reporting Manager -> HOD, HOD / HR Admin -> HR Admin.
    approval_stage, approver = resolve_permission_approver(user)
    approver_id = str(approver["_id"])
    overlapping = permission_requests_collection.find_one({
        "user_id": str(user["_id"]), "permission_date": payload.permission_date.isoformat(),
        "status": {"$in": ["pending", "approved"]},
        "start_time": {"$lt": payload.end_time}, "end_time": {"$gt": payload.start_time},
    })
    if overlapping:
        raise HTTPException(status_code=409, detail="This permission overlaps an existing pending or approved request.")
    now = datetime.now(timezone.utc)
    record = {
        "user_id": str(user["_id"]), "reporting_manager_id": _get_reporting_manager_id(user),
        "approver_id": approver_id, "approval_stage": approval_stage,
        "permission_date": payload.permission_date.isoformat(), "start_time": payload.start_time,
        "end_time": payload.end_time, "reason": payload.reason, "status": "pending",
        "created_at": now, "updated_at": now,
    }
    result = permission_requests_collection.insert_one(record)
    record["_id"] = result.inserted_id
    record["approver_notified"] = _safe_notify(
        _permission_notify, approver_id, "Permission approval required",
        f"{user.get('name', 'Employee')} requested permission on {payload.permission_date.isoformat()} from {payload.start_time} to {payload.end_time} for your {APPROVAL_STAGES[approval_stage][2]} approval.",
        str(result.inserted_id),
    )
    _audit(user, "PERMISSION_SUBMITTED", str(result.inserted_id), {"permission_date": payload.permission_date.isoformat(), "approver_id": approver_id})
    return _permission_response(record)


@router.get("/permissions/my-requests")
def my_permission_requests(authorization: str | None = Header(default=None)):
    user = _current_user(authorization)
    records = permission_requests_collection.find({"user_id": str(user["_id"])}).sort("created_at", -1)
    return [_permission_response(record) for record in records]


@router.get("/permissions/pending")
def pending_permission_requests(authorization: str | None = Header(default=None)):
    """Permission requests and cancellation requests assigned to the current user."""
    user = _current_user(authorization)
    user_id = str(user["_id"])
    query: dict[str, Any] = {
        "status": "pending",
        "user_id": {"$ne": user_id},
        "$or": [
            {"approver_id": user_id},
            # Records created before per-role routing (and WFH late check-in).
            {"approver_id": {"$exists": False}, "reporting_manager_id": user_id},
        ],
    }
    records = list(permission_requests_collection.find(query).sort("created_at", 1))
    cancellations = list(permission_requests_collection.find({
        "status": "approved", "cancellation.status": "pending", "cancellation.approver_id": user_id,
    }).sort("cancellation.requested_at", 1))
    role = str(user.get("role", "")).strip().lower()
    return {
        "can_approve": bool(records or cancellations or _direct_report_ids(user_id) or role in {HOD_ROLE, HR_ADMIN_ROLE}),
        "requests": [_permission_response(record) for record in records],
        "cancellation_requests": [_permission_response(record) for record in cancellations],
    }


def _permission_approver_id(record: dict[str, Any]) -> str:
    return str(record.get("approver_id") or record.get("reporting_manager_id") or "")


def _decide_permission(request_id: str, approved: bool, approver: dict[str, Any], remarks: str | None) -> dict[str, Any]:
    record = permission_requests_collection.find_one({"_id": _parse_object_id(request_id, "permission request")})
    if not record:
        raise HTTPException(status_code=404, detail="Permission request not found")
    if record.get("status") != "pending":
        raise HTTPException(status_code=409, detail="This permission request has already been decided.")
    if str(record.get("user_id")) == str(approver["_id"]):
        raise HTTPException(status_code=403, detail="You cannot approve or reject your own permission request.")
    if _permission_approver_id(record) != str(approver["_id"]):
        raise HTTPException(status_code=403, detail="Only the assigned approver can approve or reject this permission request.")
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



# ---------------------------------------------------------------------------
# Cancellation of approved leave / permission, decided by the original approver
# ---------------------------------------------------------------------------

def _original_approver(approver_id: Any, applicant_id: str) -> dict[str, Any]:
    """The person who approved the request; never re-derived from current assignments."""
    approver_id = str(approver_id or "")
    approver = users_collection.find_one({"_id": ObjectId(approver_id), "is_active": True}) if ObjectId.is_valid(approver_id) else None
    if not approver or approver_id == applicant_id:
        raise HTTPException(
            status_code=409,
            detail="The person who approved this request is no longer available to decide a cancellation. Please contact HR Admin.",
        )
    return approver


def _cancellation_request(collection: Any, record: dict[str, Any], approver: dict[str, Any], reason: str | None, now: datetime) -> None:
    result = collection.update_one(
        {"_id": record["_id"], "status": "approved", "cancellation.status": {"$ne": "pending"}},
        {"$set": {
            "cancellation": {
                "status": "pending", "reason": (reason or "").strip() or None,
                "requested_at": now, "approver_id": str(approver["_id"]), "approver_name": approver.get("name"),
            },
            "updated_at": now,
        }},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=409, detail="A cancellation is already pending, or the request is no longer approved.")


def _assert_cancellation_approver(record: dict[str, Any], approver: dict[str, Any]) -> None:
    cancellation = record.get("cancellation") or {}
    if record.get("status") != "approved" or cancellation.get("status") != "pending":
        raise HTTPException(status_code=409, detail="There is no pending cancellation request for this record.")
    if str(record.get("user_id")) == str(approver["_id"]):
        raise HTTPException(status_code=403, detail="You cannot decide the cancellation of your own request.")
    if cancellation.get("approver_id") != str(approver["_id"]):
        raise HTTPException(status_code=403, detail="Only the original approver can decide this cancellation.")


def _cancellation_update(approved: bool, remarks: str | None, now: datetime) -> dict[str, Any]:
    return {
        "cancellation.status": "approved" if approved else "rejected",
        "cancellation.decided_at": now,
        "cancellation.remarks": remarks,
        "updated_at": now,
    }


def _cancellation_decision_filter(record: dict[str, Any], approver: dict[str, Any]) -> dict[str, Any]:
    return {"_id": record["_id"], "status": "approved", "cancellation.status": "pending", "cancellation.approver_id": str(approver["_id"])}


@router.put("/requests/{request_id}/cancellation/{decision}")
def decide_leave_cancellation(
    request_id: str,
    decision: str,
    payload: LeaveDecisionPayload,
    authorization: str | None = Header(default=None),
):
    if decision not in {"approve", "reject"}:
        raise HTTPException(status_code=404, detail="Unknown decision")
    approver = _current_user(authorization)
    object_id = _parse_object_id(request_id, "leave request")
    record = leave_requests_collection.find_one({"_id": object_id})
    if not record:
        raise HTTPException(status_code=404, detail="Leave request not found")
    _assert_cancellation_approver(record, approver)
    now = datetime.now(timezone.utc)
    days = float(record["days"])
    approved = decision == "approve"
    update = _cancellation_update(approved, payload.remarks, now)
    if approved:
        update.update({"status": "cancelled", "cancelled_by": str(record["user_id"]), "cancelled_at": now})
    action = "cancellation_approved" if approved else "cancellation_rejected"
    result = leave_requests_collection.update_one(_cancellation_decision_filter(record, approver), {
        "$set": update,
        "$push": {"approval_history": _history_entry(action, None, approver, now, remarks=payload.remarks)},
    })
    if result.matched_count == 0:
        raise HTTPException(status_code=409, detail="This cancellation was already decided.")
    if approved:
        # Reversed exactly once: only the update that moved approved -> cancelled gets here.
        _update_balance(record["user_id"], record["leave_type_id"], int(record["from_date"][:4]), used_delta=-days)
        remove_leave_attendance(request_id)
        if str((leave_types_collection.find_one({"_id": _parse_object_id(record["leave_type_id"], "leave type")}) or {}).get("code") or "").upper() == "CL":
            _release_cl_monthly_claims(request_id)
        _safe_notify(_notify, record["user_id"], "Leave Cancellation Approved", f"Your cancellation of {days:g} day(s) leave from {record['from_date']} was approved by {approver.get('name', 'your approver')}.", request_id=request_id)
    else:
        _safe_notify(_notify, record["user_id"], "Leave Cancellation Rejected", f"Your cancellation request for the leave from {record['from_date']} was rejected; the leave remains approved. {payload.remarks or ''}".strip(), request_id=request_id)
    _audit(approver, "LEAVE_CANCELLATION_APPROVED" if approved else "LEAVE_CANCELLATION_REJECTED", request_id, {"employee_user_id": record["user_id"], "days": days})
    return _request_response(leave_requests_collection.find_one({"_id": object_id}), approver)


@router.put("/permissions/{request_id}/cancel")
def request_permission_cancellation(
    request_id: str,
    payload: CancellationRequestPayload | None = None,
    authorization: str | None = Header(default=None),
):
    """Employee asks the original approver to cancel an approved permission."""
    user = _current_user(authorization)
    record = permission_requests_collection.find_one({"_id": _parse_object_id(request_id, "permission request")})
    if not record:
        raise HTTPException(status_code=404, detail="Permission request not found")
    if str(record.get("user_id")) != str(user["_id"]):
        raise HTTPException(status_code=403, detail="You can cancel only your own permission requests.")
    if record.get("status") != "approved":
        raise HTTPException(status_code=409, detail="Only an approved permission can be cancelled through approval.")
    if str(record.get("permission_date", "")) < company_now().date().isoformat():
        raise HTTPException(status_code=409, detail="A permission for a past date cannot be cancelled.")
    approver = _original_approver(record.get("decided_by"), str(user["_id"]))
    now = datetime.now(timezone.utc)
    _cancellation_request(permission_requests_collection, record, approver, payload.reason if payload else None, now)
    notified = _safe_notify(
        _permission_notify, str(approver["_id"]), "Permission cancellation requested",
        f"{user.get('name', 'Employee')} asked to cancel the approved permission on {record['permission_date']} ({record['start_time']}–{record['end_time']}).",
        request_id,
    )
    _audit(user, "PERMISSION_CANCELLATION_REQUESTED", request_id, {"approver_id": str(approver["_id"])})
    response = _permission_response(permission_requests_collection.find_one({"_id": record["_id"]}))
    response["approver_notified"] = notified
    return response


@router.put("/permissions/{request_id}/cancellation/{decision}")
def decide_permission_cancellation(
    request_id: str,
    decision: str,
    payload: LeaveDecisionPayload,
    authorization: str | None = Header(default=None),
):
    if decision not in {"approve", "reject"}:
        raise HTTPException(status_code=404, detail="Unknown decision")
    approver = _current_user(authorization)
    record = permission_requests_collection.find_one({"_id": _parse_object_id(request_id, "permission request")})
    if not record:
        raise HTTPException(status_code=404, detail="Permission request not found")
    _assert_cancellation_approver(record, approver)
    now = datetime.now(timezone.utc)
    approved = decision == "approve"
    update = _cancellation_update(approved, payload.remarks, now)
    if approved:
        update.update({"status": "cancelled", "cancelled_at": now})
    result = permission_requests_collection.update_one(_cancellation_decision_filter(record, approver), {"$set": update})
    if result.matched_count == 0:
        raise HTTPException(status_code=409, detail="This cancellation was already decided.")
    employee_id = str(record["user_id"])
    if approved:
        # Remove the approved-permission metadata this request added to attendance.
        db["attendance"].update_one(
            {"user_id": employee_id, "attendance_date": record["permission_date"], "approved_permission.request_id": request_id},
            {"$unset": {"approved_permission": ""}},
        )
        _safe_notify(_permission_notify, employee_id, "Permission cancellation approved", f"Your permission on {record['permission_date']} ({record['start_time']}–{record['end_time']}) is cancelled.", request_id)
    else:
        _safe_notify(_permission_notify, employee_id, "Permission cancellation rejected", f"Your cancellation request for the permission on {record['permission_date']} was rejected; the permission remains approved.", request_id)
    _audit(approver, "PERMISSION_CANCELLATION_APPROVED" if approved else "PERMISSION_CANCELLATION_REJECTED", request_id, {"employee_user_id": employee_id})
    return _permission_response(permission_requests_collection.find_one({"_id": record["_id"]}))


@router.get("/my-approval-role")
def my_approval_role(authorization: str | None = Header(default=None)):
    """Routing category of the current user (same precedence as the backend routing)."""
    return {"category": _applicant_category(_current_user(authorization))}
