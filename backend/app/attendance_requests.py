"""Employee attendance correction requests with per-date HR Admin decisions.

Employees request a status (and, for Present, check-in/check-out times) for
their own past dates. HR Admin approves (optionally changing the final status
or times) or rejects every date individually; approved decisions are written
to the canonical ``attendance`` collection (one record per employee and date).
Monthly Weekly Off and Medical Leave limits are enforced on every step.
"""

from __future__ import annotations

import logging
import re
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from bson import ObjectId
from fastapi import APIRouter, Header, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app import leave
from app.attendance import (
    CALENDAR_FORWARD_DAYS,
    COMPANY_TZ,
    WEEKDAY_NAMES,
    company_weekly_off_weekdays,
    _approved_leaves_for_date,
    _current_user,
    _get_leave_type_code,
    _leave_covers,
    _require_hr_admin,
    _sync_leave_for_user_range,
    attendance_collection,
    audit_logs_collection,
    company_today,
    users_collection,
    validate_calendar_month,
)
from app.db.mongodb import db

router = APIRouter(prefix="/api/attendance-requests", tags=["Attendance Requests"])
logger = logging.getLogger(__name__)

attendance_requests_collection = db["attendance_requests"]
# One active (pending/processing) claim per employee and date; prevents
# duplicate or conflicting requests even under concurrent submissions.
attendance_request_date_claims_collection = db["attendance_request_date_claims"]
payroll_collection = db["monthly_payroll_records"]
leave_notifications_collection = db["leave_notifications"]
notification_sequences_collection = db["notification_sequences"]

RequestStatus = Literal["Present", "Absent", "Holiday", "Weekly Off", "Medical Leave"]
# HR Admin may also finalize a requested date as Not Applicable (counted as LOP).
FinalStatus = Literal["Present", "Absent", "Holiday", "Weekly Off", "Medical Leave", "Not Applicable"]
NOT_APPLICABLE = "Not Applicable"
BEFORE_JOINING = "Before Joining"
# Permitted corrections by the date's current attendance status. Leave dates
# are owned by the Leave module and future/pre-joining dates are not eligible.
STATUS_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "Not Marked": ("Present", "Absent", "Holiday", "Weekly Off", "Medical Leave"),
    NOT_APPLICABLE: ("Present", "Absent", "Holiday", "Weekly Off", "Medical Leave"),
    "Absent": ("Present", "Holiday", "Weekly Off", "Medical Leave"),
    "Weekly Off": ("Present", "Medical Leave", "Absent"),
    "Present": ("Present", "Absent", "Medical Leave", "Weekly Off"),
    "Holiday": ("Present",),
}
MEDICAL_LEAVE_MONTHLY_LIMIT = leave.MEDICAL_LEAVE_DAYS_PER_MONTH
MEDICAL_LEAVE_CODE = leave.MEDICAL_LEAVE_CODE
DEFAULT_WEEKLY_OFF_MONTHLY_LIMIT = 4  # Used only if the company policy has no value.
REQUEST_STATUSES = ("pending", "processing", "approved", "partially_approved", "rejected", "cancelled")
ACTIVE_REQUEST_STATUSES = ("pending", "processing")
# Payroll statuses from app.payroll: Calculated is a draft that HR can
# disapprove; Approved/Processed/Paid are finalized.
PAYROLL_FINALIZED_STATUSES = {"Approved", "Processed", "Paid"}
PAYROLL_DRAFT_STATUSES = {"Calculated"}
PROCESSING_TIMEOUT = timedelta(minutes=5)
MAX_DATES_PER_REQUEST = 31
TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def ensure_attendance_request_indexes() -> None:
    attendance_request_date_claims_collection.create_index(
        [("user_id", 1), ("attendance_date", 1)], unique=True,
    )
    attendance_request_date_claims_collection.create_index("request_id")
    attendance_requests_collection.create_index("request_number", unique=True)
    attendance_requests_collection.create_index([("user_id", 1), ("submitted_at", -1)])
    attendance_requests_collection.create_index([("status", 1), ("submitted_at", -1)])
    attendance_requests_collection.create_index([("employee_id", 1), ("status", 1), ("periods", 1)])
    attendance_requests_collection.create_index([("user_id", 1), ("status", 1), ("periods", 1)])


@router.on_event("startup")
def startup_attendance_requests() -> None:
    ensure_attendance_request_indexes()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

def _clean_time(value: str | None) -> str | None:
    value = (value or "").strip()
    if not value:
        return None
    if not TIME_PATTERN.match(value):
        raise ValueError("Time must use 24-hour HH:MM format.")
    return value


def _clean_text(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


class AttendanceRequestDateIn(BaseModel):
    attendance_date: date
    requested_status: RequestStatus
    requested_check_in_time: str | None = None
    requested_check_out_time: str | None = None

    _times = field_validator("requested_check_in_time", "requested_check_out_time")(classmethod(lambda cls, value: _clean_time(value)))


class AttendanceRequestCreate(BaseModel):
    dates: list[AttendanceRequestDateIn] = Field(min_length=1, max_length=MAX_DATES_PER_REQUEST)
    remarks: str | None = Field(default=None, max_length=1000)

    _remarks = field_validator("remarks")(classmethod(lambda cls, value: _clean_text(value)))


class AttendanceDecisionIn(BaseModel):
    attendance_date: date
    action: Literal["approve", "reject"] = "approve"
    final_status: FinalStatus | None = None
    final_check_in_time: str | None = None
    final_check_out_time: str | None = None

    _times = field_validator("final_check_in_time", "final_check_out_time")(classmethod(lambda cls, value: _clean_time(value)))


class AttendanceRequestResponsePayload(BaseModel):
    decisions: list[AttendanceDecisionIn] = Field(min_length=1, max_length=MAX_DATES_PER_REQUEST)
    review_comments: str | None = Field(default=None, max_length=1000)

    _comments = field_validator("review_comments")(classmethod(lambda cls, value: _clean_text(value)))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _department(user: dict[str, Any]) -> str:
    employee_master = db["employees"].find_one({"employee_id": str(user.get("employee_id") or "")}) or {}
    employee_details = db["employee_details"].find_one({"user_id": str(user["_id"])}) or {}
    return user.get("department") or employee_master.get("department") or employee_details.get("department") or ""


def _payroll_status(employee_id: str, period: str, cache: dict[str, str | None] | None = None) -> str | None:
    if cache is not None and period in cache:
        return cache[period]
    row = payroll_collection.find_one({"employee_id": employee_id, "period": period}, {"status": 1}) if employee_id else None
    value = row.get("status") if row else None
    if cache is not None:
        cache[period] = value
    return value


def _payroll_lock_reason(period: str, payroll_status: str | None) -> str | None:
    if payroll_status in PAYROLL_FINALIZED_STATUSES:
        return f"Payroll for {period} is {payroll_status}; attendance for this month is locked."
    return None


def _payroll_draft_note(period: str, payroll_status: str | None) -> str | None:
    if payroll_status in PAYROLL_DRAFT_STATUSES:
        return (
            f"Payroll for {period} is already calculated (draft). Saving this decision marks that payroll as "
            "outdated; disapprove and recalculate it before payroll approval."
        )
    return None


def _attendance_category(record: dict[str, Any] | None) -> str | None:
    if not record:
        return None
    if record.get("attendance_type") == "WFH" or record.get("attendance_source") == "WFH_FACE_GPS":
        return "Work From Home"
    if record.get("attendance_type") == "OFFICE" or record.get("attendance_source") in {"ESSL", "MANUAL"}:
        return "Office"
    return None


def _validate_month(year: int, month: int, joining: date | None = None) -> tuple[date, date]:
    return validate_calendar_month(year, month, joining, company_today())


def _upcoming_leave(user_id: str, start: date, end: date, today: date) -> dict[date, dict[str, Any]]:
    """Approved leave on future dates of the range (not yet synced to attendance)."""
    first = max(start, today + timedelta(days=1))
    if first > end:
        return {}
    result: dict[date, dict[str, Any]] = {}
    for request in db["leave_requests"].find({
        "user_id": user_id, "status": "approved",
        "from_date": {"$lte": end.isoformat()}, "to_date": {"$gte": first.isoformat()},
    }):
        leave_type_id = str(request.get("leave_type_id") or "")
        leave_type = db["leave_types"].find_one({"_id": ObjectId(leave_type_id)}, {"name": 1}) if ObjectId.is_valid(leave_type_id) else None
        details = {
            "leave_type_code": _get_leave_type_code(request),
            "leave_type_name": (leave_type or {}).get("name"),
            "day_part": request.get("day_part") or "full_day",
            "from_date": str(request["from_date"])[:10],
            "to_date": str(request["to_date"])[:10],
        }
        current = max(first, date.fromisoformat(details["from_date"]))
        stop = min(end, date.fromisoformat(details["to_date"]))
        while current <= stop:
            if _leave_covers(request, current.isoformat()):
                result[current] = details
            current += timedelta(days=1)
    return result


def _permissions_by_date(user_id: str, start: date, end: date) -> dict[date, list[dict[str, Any]]]:
    """Pending and approved permission requests in the range, per date."""
    if start > end:
        return {}
    result: dict[date, list[dict[str, Any]]] = {}
    for item in db["permission_requests"].find({
        "user_id": user_id, "status": {"$in": ["pending", "approved"]},
        "permission_date": {"$gte": start.isoformat(), "$lte": end.isoformat()},
    }).sort("start_time", 1):
        result.setdefault(date.fromisoformat(str(item["permission_date"])[:10]), []).append({
            "start_time": item.get("start_time"),
            "end_time": item.get("end_time"),
            "status": item.get("status"),
            "reason": item.get("reason"),
        })
    return result


def _month_context(user: dict[str, Any], year: int, month: int) -> dict[str, Any]:
    """Attendance data shared by the calendar, request creation and HR review."""
    start = date(year, month, 1)
    end = date(year, month, monthrange(year, month)[1])
    user_id = str(user["_id"])
    today = company_today()
    # Holiday, leave and permission details are shown only up to the 90th day.
    window_end = today + timedelta(days=CALENDAR_FORWARD_DAYS)
    # Same lazy approved-leave sync as GET /api/attendance/my (idempotent).
    _sync_leave_for_user_range(user_id, start.isoformat(), end.isoformat())
    return {
        "start": start,
        "end": end,
        "records": {
            str(record.get("attendance_date"))[:10]: record
            for record in attendance_collection.find(
                {"user_id": user_id, "attendance_date": {"$gte": start.isoformat(), "$lte": end.isoformat()}}
            )
        },
        "holidays": leave._holiday_names(user, start, end),
        "weekly_offs": company_weekly_off_weekdays(),
        "joining": leave._joining_date(user),
        "today": today,
        "window_end": window_end,
        "upcoming_leave": _upcoming_leave(user_id, start, min(end, window_end), today),
        "permissions": _permissions_by_date(user_id, start, min(end, window_end)),
    }


def _day_status(current: date, record: dict[str, Any] | None, ctx: dict[str, Any]) -> str:
    if record:
        return str(record.get("status") or "Not Marked")
    if current > ctx["today"]:
        # Future dates show known holidays, approved leave and weekly offs.
        if ctx["joining"] and current < ctx["joining"]:
            return BEFORE_JOINING
        if current > ctx.get("window_end", date.max):
            return "Upcoming"
        if current in ctx["holidays"]:
            return "Holiday"
        if current in ctx.get("upcoming_leave", {}):
            return "Leave"
        if current.weekday() in ctx["weekly_offs"]:
            return "Weekly Off"
        return "Upcoming"
    if current in ctx["holidays"]:
        return "Holiday"
    if current.weekday() in ctx["weekly_offs"]:
        return "Weekly Off"
    if ctx["joining"] and current < ctx["joining"]:
        return BEFORE_JOINING
    return "Not Marked"


def _weekly_off_limit() -> int:
    """Weekly Offs per Month from the HR-managed company policy."""
    try:
        return int(leave._company_policy().get("weekly_offs_per_month") or DEFAULT_WEEKLY_OFF_MONTHLY_LIMIT)
    except (TypeError, ValueError):
        return DEFAULT_WEEKLY_OFF_MONTHLY_LIMIT


def _pending_items(user_id: str, period: str, exclude_request_id: str | None = None) -> list[dict[str, Any]]:
    """Requested dates of other active requests; reserved against monthly limits."""
    items = []
    for request in attendance_requests_collection.find(
        {"user_id": user_id, "status": {"$in": list(ACTIVE_REQUEST_STATUSES)}, "periods": period},
        {"dates": 1},
    ):
        if str(request["_id"]) == exclude_request_id:
            continue
        items.extend(item for item in request.get("dates", []) if str(item.get("attendance_date", "")).startswith(period))
    return items


def _medical_leave_types() -> dict[str, str]:
    """Leave-module Medical Leave types as {leave_type_id: code}."""
    return {
        str(item["_id"]): str(item.get("code") or "").upper()
        for item in db["leave_types"].find({}, {"code": 1, "name": 1})
        if leave._is_medical_leave_type(item)
    }


def _month_usage(user: dict[str, Any], ctx: dict[str, Any], exclude_request_id: str | None = None) -> dict[str, float]:
    """Weekly Off and Medical Leave days already used or reserved in a month.

    Weekly Off counts every day of the month whose effective status is Weekly
    Off (a recorded Weekly Off, or an unrecorded configured weekly-off day) plus
    pending requests for Weekly Off. Medical Leave is shared with the Leave
    module: approved Medical Leave in attendance, pending Medical Leave
    applications and pending Medical Leave attendance requests. Rejected and
    cancelled requests never count.
    """
    weekly_off = 0
    medical = 0.0
    medical_types = _medical_leave_types()
    medical_codes = set(medical_types.values())
    whole_month = {**ctx, "today": date.max}
    current = ctx["start"]
    while current <= ctx["end"]:
        record = ctx["records"].get(current.isoformat())
        if _day_status(current, record, whole_month) == "Weekly Off":
            weekly_off += 1
        if record and (record.get("medical_leave") or (
            str(record.get("status") or "").casefold() == "leave"
            and str(record.get("leave_type_code") or "").upper() in medical_codes
        )):
            medical += 0.5 if "half" in str(record.get("leave_day_part") or "") else 1
        current += timedelta(days=1)
    period = ctx["start"].isoformat()[:7]
    for item in _pending_items(str(user["_id"]), period, exclude_request_id):
        weekly_off += item.get("requested_status") == "Weekly Off"
        medical += item.get("requested_status") == "Medical Leave"
    if medical_types:
        for application in db["leave_requests"].find({
            "user_id": str(user["_id"]), "leave_type_id": {"$in": list(medical_types)},
            "status": {"$regex": "^pending", "$options": "i"},
        }, {"leave_dates": 1, "from_date": 1, "day_part": 1}):
            per_day = 0.5 if "half" in str(application.get("day_part") or "") else 1
            dates = application.get("leave_dates") or [application.get("from_date")]
            medical += per_day * sum(1 for value in dates if str(value or "").startswith(period))
    return {"weekly_off_used": weekly_off, "medical_leave_used": medical}


def leave_module_medical_problem(user: dict[str, Any], leave_dates: list[date], day_part: str) -> str | None:
    """Monthly Medical Leave rule for Leave module applications (None if allowed)."""
    weekly_limit = _weekly_off_limit()
    per_day = 0.5 if day_part != "full_day" else 1
    by_month: dict[tuple[int, int], int] = {}
    for value in leave_dates:
        by_month[(value.year, value.month)] = by_month.get((value.year, value.month), 0) + 1
    for (year, month), count in sorted(by_month.items()):
        label = date(year, month, 1).strftime("%B %Y")
        usage = _month_usage(user, _month_context(user, year, month))
        if usage["weekly_off_used"] < weekly_limit:
            return (
                f"Medical Leave for {label} is available only after all {weekly_limit} Weekly Off days of the month "
                f"are used ({usage['weekly_off_used']} of {weekly_limit} used)."
            )
        if usage["medical_leave_used"] + per_day * count > MEDICAL_LEAVE_MONTHLY_LIMIT:
            return (
                f"Medical Leave is limited to {MEDICAL_LEAVE_MONTHLY_LIMIT} day per month per employee; "
                f"{usage['medical_leave_used']:g} day(s) of {label} are already used or pending."
            )
    return None


def _medical_leave_availability(usage: dict[str, int], weekly_limit: int, extra_weekly_offs: int = 0) -> tuple[bool, str | None]:
    """Medical Leave is a paid monthly entitlement of one day per employee.

    It unlocks only once the month's Weekly Off allowance is fully used
    (recorded or requested) and does not depend on leave balances.
    """
    if usage["medical_leave_used"] + 1 > MEDICAL_LEAVE_MONTHLY_LIMIT:
        return False, f"the monthly limit of {MEDICAL_LEAVE_MONTHLY_LIMIT} medical leave has been reached."
    weekly_used = usage["weekly_off_used"] + extra_weekly_offs
    if weekly_used < weekly_limit:
        return False, f"it unlocks after all {weekly_limit} Weekly Off days of the month are used ({weekly_used} of {weekly_limit} used)."
    return True, None


def _status_options(base_status: str, usage: dict[str, int], weekly_limit: int) -> tuple[list[str], list[str]]:
    """Permitted statuses for a date plus explanations for limit-blocked ones."""
    options: list[str] = []
    notes: list[str] = []
    medical = _medical_leave_availability(usage, weekly_limit)
    for option in STATUS_TRANSITIONS.get(base_status, ()):
        if option == "Weekly Off" and usage["weekly_off_used"] >= weekly_limit:
            notes.append(
                f"Weekly Off is unavailable: the monthly limit of {weekly_limit} weekly off day(s) has been reached "
                f"({usage['weekly_off_used']} recorded or requested)."
            )
            continue
        if option == "Medical Leave" and not medical[0]:
            notes.append(f"Medical Leave is unavailable: {medical[1]}")
            continue
        options.append(option)
    return options, notes


def _build_calendar(user: dict[str, Any], year: int, month: int) -> dict[str, Any]:
    """Authoritative per-date status, request eligibility and options for one month."""
    _validate_month(year, month, leave._joining_date(user))
    ctx = _month_context(user, year, month)
    user_id = str(user["_id"])
    employee_id = str(user.get("employee_id") or "")
    today = ctx["today"]
    period = f"{year:04d}-{month:02d}"
    claims = {
        claim["attendance_date"]: claim["request_id"]
        for claim in attendance_request_date_claims_collection.find(
            {"user_id": user_id, "attendance_date": {"$gte": ctx["start"].isoformat(), "$lte": ctx["end"].isoformat()}}
        )
    }
    requests_by_id = {
        str(item["_id"]): item
        for item in attendance_requests_collection.find(
            {"_id": {"$in": [ObjectId(value) for value in set(claims.values()) if ObjectId.is_valid(value)]}},
            {"request_number": 1, "status": 1, "dates": 1},
        )
    } if claims else {}
    payroll_status = _payroll_status(employee_id, period)
    payroll_lock = payroll_status in PAYROLL_FINALIZED_STATUSES
    usage = _month_usage(user, ctx)
    weekly_limit = _weekly_off_limit()
    medical = _medical_leave_availability(usage, weekly_limit)

    days: list[dict[str, Any]] = []
    current = ctx["start"]
    while current <= ctx["end"]:
        iso = current.isoformat()
        record = ctx["records"].get(iso)
        day_status = _day_status(current, record, ctx)
        in_window = current <= ctx["window_end"]
        upcoming_leave = ctx["upcoming_leave"].get(current)
        permissions = ctx["permissions"].get(current, [])

        claim_request = requests_by_id.get(claims.get(iso, ""))
        request_info = None
        if claim_request:
            requested = next((item for item in claim_request.get("dates", []) if item.get("attendance_date") == iso), {})
            request_info = {
                "request_id": str(claim_request["_id"]),
                "request_number": claim_request.get("request_number"),
                "status": claim_request.get("status"),
                "requested_status": requested.get("requested_status"),
            }

        options, option_notes = _status_options(day_status, usage, weekly_limit)
        reason = None
        if current >= today:
            reason = "Requests are for past dates only; today's attendance can still be marked."
        elif day_status == BEFORE_JOINING or (ctx["joining"] and current < ctx["joining"]):
            # Includes pre-joining weekly offs and holidays.
            reason = "This date is before your joining date."
        elif day_status == "Leave":
            reason = "Leave dates are managed in the Leave module."
        elif day_status not in STATUS_TRANSITIONS:
            reason = f"{day_status} dates cannot be corrected through an attendance request."
        elif request_info:
            reason = f"Request {request_info['request_number']} is already {request_info['status']} for this date."
        elif payroll_lock:
            reason = _payroll_lock_reason(period, payroll_status)
        elif not options:
            reason = "No attendance status can be requested for this date. " + " ".join(option_notes)

        days.append({
            "attendance_date": iso,
            "status": day_status,
            "status_label": "Medical Leave" if record and record.get("medical_leave") else day_status,
            "attendance_category": _attendance_category(record),
            "attendance_source": record.get("attendance_source") if record else None,
            "check_in_time": record.get("check_in_time") if record else None,
            "check_out_time": record.get("check_out_time") if record else None,
            "leave_type_code": record.get("leave_type_code") if record else (upcoming_leave or {}).get("leave_type_code"),
            "upcoming_leave": upcoming_leave if day_status == "Leave" else None,
            "permissions": permissions,
            "is_holiday": current in ctx["holidays"] and in_window,
            "holiday_name": ctx["holidays"].get(current) if day_status == "Holiday" else None,
            "is_weekly_off": current.weekday() in ctx["weekly_offs"],
            "is_future": current > today,
            "is_after_window": not in_window,
            # Upcoming holiday / leave / permission dates open a read-only detail view.
            "has_upcoming_details": current > today and in_window and (
                day_status in {"Holiday", "Leave"} or bool(permissions)
            ),
            "is_before_joining": bool(ctx["joining"] and current < ctx["joining"]),
            "has_record": record is not None,
            "request": request_info,
            "eligible_for_request": reason is None,
            "ineligible_reason": reason,
            "request_options": options if reason is None else [],
            "option_notes": option_notes if reason is None else [],
        })
        current += timedelta(days=1)

    return {
        "year": year,
        "month": month,
        "period": period,
        "payroll_status": payroll_status,
        "payroll_locked": payroll_lock,
        "today": today.isoformat(),
        "joining_date": ctx["joining"].isoformat() if ctx["joining"] else None,
        "calendar_end": (today + timedelta(days=CALENDAR_FORWARD_DAYS)).isoformat(),
        "weekly_off_weekdays": [WEEKDAY_NAMES[index] for index in sorted(ctx["weekly_offs"])],
        "weekly_off_configured": bool(ctx["weekly_offs"]),
        "limits": {
            "weekly_off_limit": weekly_limit,
            "weekly_off_used": usage["weekly_off_used"],
            "medical_leave_limit": MEDICAL_LEAVE_MONTHLY_LIMIT,
            "medical_leave_used": usage["medical_leave_used"],
            "medical_leave_available": medical[0],
            "medical_leave_note": f"Medical Leave is unavailable: {medical[1]}" if medical[1] else None,
        },
        "days": days,
    }


def _next_request_number() -> str:
    sequence = notification_sequences_collection.find_one_and_update(
        {"_id": "attendance_request"}, {"$inc": {"value": 1}},
        upsert=True, return_document=ReturnDocument.AFTER,
    )
    return f"AR{int(sequence['value']):04d}"


def _notify(recipient_id: str, title: str, message: str, request: dict[str, Any], key: str) -> None:
    """Create one in-app notification in the existing Leave notification feed."""
    request_id = str(request["_id"])
    notification_key = f"attendance_request:{request_id}:{key}:{recipient_id}"
    if leave_notifications_collection.find_one({"notification_key": notification_key}):
        return  # Retried operation: never notify twice.
    recipient = users_collection.find_one({"_id": ObjectId(recipient_id)}) if ObjectId.is_valid(recipient_id) else None
    if not recipient:
        return
    sequence = notification_sequences_collection.find_one_and_update(
        {"_id": "leave_notification"}, {"$inc": {"value": 1}},
        upsert=True, return_document=ReturnDocument.AFTER,
    )
    leave_notifications_collection.insert_one({
        "leave_notification_id": f"L{int(sequence['value']):03d}",
        "notification_key": notification_key,
        "recipient_user_id": recipient_id,
        "employee_name": request.get("employee_name"),
        "employee_id": request.get("employee_id"),
        "leave_request_id": None,
        "attendance_request_id": request_id,
        "related_user_id": request.get("user_id"),
        "related_user_name": request.get("employee_name"),
        "notification_type": "attendance_request",
        "title": title,
        "message": message,
        "is_read": False, "is_starred": False, "is_deleted": False,
        "created_at": datetime.now(timezone.utc), "expires_at": None,
        "read_at": None, "deleted_at": None,
    })


def _audit(actor: dict[str, Any], action: str, request: dict[str, Any], details: dict[str, Any] | None = None) -> None:
    # audit_logs has a $jsonSchema validator (user_id, action, timestamp). An
    # audit failure must not turn an already-saved operation into an error.
    try:
        audit_logs_collection.insert_one({
            "user_id": str(actor["_id"]),
            "action": action,
            "entity": "attendance_request",
            "entity_id": str(request["_id"]),
            "new_value": {"request_number": request.get("request_number"), **(details or {})},
            "timestamp": datetime.now(timezone.utc),
        })
    except Exception:
        logger.exception("Unable to write audit log %s for attendance request %s", action, request.get("request_number"))


def _serialize(request: dict[str, Any]) -> dict[str, Any]:
    result = {key: value for key, value in request.items() if key not in {"_id", "processing_token"}}
    result["id"] = str(request["_id"])
    return result


def _load_request(request_id: str) -> dict[str, Any]:
    if not ObjectId.is_valid(request_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid attendance request ID.")
    request = attendance_requests_collection.find_one({"_id": ObjectId(request_id)})
    if not request:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attendance request not found.")
    return request


def _is_hr_admin(user: dict[str, Any]) -> bool:
    return str(user.get("role", "")).strip().lower() in {"hr admin", "administrator"}


def _release_claims(request_id: str) -> None:
    attendance_request_date_claims_collection.delete_many({"request_id": request_id})


def _local_text(value: datetime) -> str:
    if value.tzinfo is None:  # PyMongo returns naive UTC datetimes.
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(COMPANY_TZ).strftime("%d %b %Y %I:%M %p")


def _date_label(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%a, %d %b %Y")


# ---------------------------------------------------------------------------
# Employee endpoints
# ---------------------------------------------------------------------------

@router.get("/calendar")
def my_attendance_calendar(
    year: int = Query(...),
    month: int = Query(...),
    authorization: str | None = Header(default=None),
):
    """Monthly calendar for the authenticated employee, including request eligibility."""
    return _build_calendar(_current_user(authorization), year, month)


@router.get("/calendar/user/{user_id}")
def employee_attendance_calendar(
    user_id: str,
    year: int = Query(...),
    month: int = Query(...),
    authorization: str | None = Header(default=None),
):
    """The same monthly calendar for one employee; read-only view for HR Admin."""
    _require_hr_admin(authorization, "Only HR Admin or Administrator can view employee attendance")
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid user ID.")
    employee = users_collection.find_one({"_id": ObjectId(user_id)})
    if not employee:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Employee not found.")
    return _build_calendar(employee, year, month)


@router.get("/eligible-dates")
def my_eligible_dates(
    year: int = Query(...),
    month: int = Query(...),
    authorization: str | None = Header(default=None),
):
    calendar = _build_calendar(_current_user(authorization), year, month)
    return {
        "year": year, "month": month, "period": calendar["period"],
        "payroll_status": calendar["payroll_status"],
        "dates": [day["attendance_date"] for day in calendar["days"] if day["eligible_for_request"]],
    }


def _times_text(check_in: str | None, check_out: str | None) -> str:
    if not check_in and not check_out:
        return ""
    return f" ({check_in or 'no check-in'} – {check_out or 'no check-out'})"


def _time_problem(
    status_value: str | None,
    check_in: str | None,
    check_out: str | None,
    original: dict[str, Any] | None = None,
) -> str | None:
    if status_value != "Present":
        return "Check-in/check-out times apply only to Present." if check_in or check_out else None
    if check_in and check_out and check_out <= check_in:
        return "Check-out time must be later than check-in time."
    # A correction may change recorded times but never erase them.
    if original and original.get("status") == "Present":
        if original.get("check_in_time") and not check_in:
            return "Enter a check-in time; an existing check-in cannot be removed."
        if original.get("check_out_time") and not check_out:
            return "Enter a check-out time; an existing check-out cannot be removed."
    return None


@router.post("", status_code=status.HTTP_201_CREATED)
def create_attendance_request(
    payload: AttendanceRequestCreate,
    authorization: str | None = Header(default=None),
):
    # Identity always comes from the session; the payload has no employee field.
    user = _current_user(authorization)
    user_id = str(user["_id"])
    requested = sorted(payload.dates, key=lambda item: item.attendance_date)
    isos = [item.attendance_date.isoformat() for item in requested]
    if len(set(isos)) != len(isos):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Each date can be included only once in a request.")

    calendars: dict[tuple[int, int], dict[str, Any]] = {}
    added: dict[tuple[int, int], dict[str, int]] = {}
    problems: list[str] = []
    items: list[dict[str, Any]] = []
    for item in requested:
        iso = item.attendance_date.isoformat()
        key = (item.attendance_date.year, item.attendance_date.month)
        if key not in calendars:
            try:
                calendar = _build_calendar(user, *key)
                calendars[key] = {"limits": calendar["limits"], "days": {day["attendance_date"]: day for day in calendar["days"]}}
            except HTTPException as exc:
                calendars[key] = {"limits": None, "days": {}, "error": exc.detail}
            added[key] = {"Weekly Off": 0, "Medical Leave": 0}
        calendar = calendars[key]
        day = calendar["days"].get(iso)
        if not day:
            problems.append(f"{iso}: {calendar.get('error', 'Invalid date.')}")
            continue
        if not day["eligible_for_request"]:
            problems.append(f"{iso}: {day['ineligible_reason']}")
            continue
        if item.requested_status not in day["request_options"]:
            notes = " ".join(day["option_notes"])
            problems.append(f"{iso}: {item.requested_status} cannot be requested for a {day['status']} date. {notes}".strip())
            continue
        time_problem = _time_problem(
            item.requested_status, item.requested_check_in_time, item.requested_check_out_time,
            {"status": day["status"], "check_in_time": day["check_in_time"], "check_out_time": day["check_out_time"]},
        )
        if time_problem:
            problems.append(f"{iso}: {time_problem}")
            continue
        if (
            item.requested_status == "Present" and day["status"] == "Present"
            and (item.requested_check_in_time, item.requested_check_out_time) == (day["check_in_time"], day["check_out_time"])
        ):
            problems.append(f"{iso}: Change the check-in or check-out time to request a Present correction.")
            continue
        if any(leave_item.get("day_part", "full_day") == "full_day" for leave_item in _approved_leaves_for_date(iso, user_id)):
            problems.append(f"{iso}: An approved full-day leave covers this date.")
            continue
        # Several Weekly Off / Medical Leave dates in one request count together.
        limits = calendar["limits"]
        if item.requested_status == "Weekly Off":
            if limits["weekly_off_used"] + added[key]["Weekly Off"] >= limits["weekly_off_limit"]:
                problems.append(f"{iso}: Weekly Off would exceed the monthly limit of {limits['weekly_off_limit']}.")
                continue
            added[key]["Weekly Off"] += 1
        if item.requested_status == "Medical Leave":
            if limits["medical_leave_used"] + added[key]["Medical Leave"] + 1 > limits["medical_leave_limit"]:
                problems.append(f"{iso}: Medical Leave would exceed the monthly limit of {limits['medical_leave_limit']}.")
                continue
            added[key]["Medical Leave"] += 1
        items.append({
            "attendance_date": iso,
            # Original attendance is kept separately; the record is unchanged until approval.
            "original_status": day["status"],
            "original_check_in_time": day["check_in_time"],
            "original_check_out_time": day["check_out_time"],
            "original_attendance_category": day["attendance_category"],
            "original_has_record": day["has_record"],
            "requested_status": item.requested_status,
            "requested_check_in_time": item.requested_check_in_time if item.requested_status == "Present" else None,
            "requested_check_out_time": item.requested_check_out_time if item.requested_status == "Present" else None,
            "decision": None,
            "final_status": None,
            "final_check_in_time": None,
            "final_check_out_time": None,
            "outcome": None,
            "applied": False,
            "message": None,
        })
    if problems:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "These dates cannot be requested: " + "; ".join(problems))

    request_id = ObjectId()
    try:
        for iso in isos:
            attendance_request_date_claims_collection.insert_one(
                {"user_id": user_id, "attendance_date": iso, "request_id": str(request_id), "created_at": datetime.now(timezone.utc)}
            )
    except DuplicateKeyError as exc:
        attendance_request_date_claims_collection.delete_many({"request_id": str(request_id)})
        raise HTTPException(status.HTTP_409_CONFLICT, "Another attendance request already covers one of these dates. Refresh and try again.") from exc

    now = datetime.now(timezone.utc)
    record = {
        "_id": request_id,
        "user_id": user_id,
        "employee_id": user.get("employee_id", ""),
        "employee_name": user.get("name", ""),
        "department": _department(user),
        "dates": items,
        "attendance_dates": isos,
        "periods": sorted({iso[:7] for iso in isos}),
        "employee_remarks": payload.remarks,
        "status": "pending",
        "submitted_at": now,
        "reviewed_by": None,
        "reviewed_by_name": None,
        "reviewed_at": None,
        "review_comments": None,
        "created_at": now,
        "updated_at": now,
    }
    try:
        record["request_number"] = _next_request_number()
        attendance_requests_collection.insert_one(record)
    except Exception as exc:
        _release_claims(str(request_id))
        logger.exception("Unable to save attendance request for user %s", user_id)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Your attendance request could not be saved. Please try again.") from exc

    # Concurrent requests for different dates could both pass the limit check;
    # recheck with this request's reservations included and roll back if over.
    weekly_limit = _weekly_off_limit()
    for year, month in {(item.attendance_date.year, item.attendance_date.month) for item in requested
                        if item.requested_status in {"Weekly Off", "Medical Leave"}}:
        usage = _month_usage(user, _month_context(user, year, month))
        if usage["weekly_off_used"] > weekly_limit or usage["medical_leave_used"] > MEDICAL_LEAVE_MONTHLY_LIMIT:
            attendance_requests_collection.delete_one({"_id": request_id, "status": "pending"})
            _release_claims(str(request_id))
            raise HTTPException(status.HTTP_409_CONFLICT, "Another request used the remaining Weekly Off / Medical Leave allowance for this month. Refresh and try again.")

    _audit(user, "ATTENDANCE_REQUEST_SUBMITTED", record, {"dates": isos})
    summary = ", ".join(
        f"{_date_label(item['attendance_date'])} ({item['original_status']} → {item['requested_status']}"
        f"{_times_text(item['requested_check_in_time'], item['requested_check_out_time'])})"
        for item in items
    )
    try:
        for admin in users_collection.find({"is_active": True, "role": {"$regex": r"^\s*(hr admin|administrator)\s*$", "$options": "i"}}, {"_id": 1}):
            _notify(
                str(admin["_id"]),
                "Attendance Request Awaiting Review",
                f"{record['employee_name']} ({record['employee_id']}) submitted attendance request {record['request_number']} for {summary}. Review it in Attendance > Attendance Requests.",
                record,
                "submitted",
            )
    except Exception:
        # The HR inbox is the source of truth; a notification failure must not
        # undo a saved request.
        logger.exception("Unable to notify HR Admin about attendance request %s", record["request_number"])
    return _serialize(record)


@router.get("/my")
def my_attendance_requests(authorization: str | None = Header(default=None)):
    user = _current_user(authorization)
    return [_serialize(item) for item in attendance_requests_collection.find({"user_id": str(user["_id"])}).sort("submitted_at", -1)]


@router.post("/{request_id}/cancel")
def cancel_attendance_request(request_id: str, authorization: str | None = Header(default=None)):
    user = _current_user(authorization)
    request = _load_request(request_id)
    if request.get("user_id") != str(user["_id"]):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can cancel only your own attendance requests.")
    now = datetime.now(timezone.utc)
    updated = attendance_requests_collection.find_one_and_update(
        {"_id": request["_id"], "user_id": str(user["_id"]), "status": "pending"},
        {"$set": {"status": "cancelled", "cancelled_at": now, "updated_at": now}},
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only pending requests can be cancelled. This request has already been processed.")
    _release_claims(str(request["_id"]))
    _audit(user, "ATTENDANCE_REQUEST_CANCELLED", updated)
    return _serialize(updated)


# ---------------------------------------------------------------------------
# HR Admin endpoints
# ---------------------------------------------------------------------------

@router.get("")
def list_attendance_requests(
    request_status: str | None = Query(default=None, alias="status"),
    employee: str | None = Query(default=None, max_length=100),
    department: str | None = Query(default=None, max_length=100),
    period: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
    attendance_date: date | None = Query(default=None),
    authorization: str | None = Header(default=None),
):
    _require_hr_admin(authorization, "Only HR Admin or Administrator can review attendance requests.")
    query: dict[str, Any] = {}
    if request_status and request_status != "all":
        if request_status not in REQUEST_STATUSES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid request status filter.")
        query["status"] = request_status
    if employee and employee.strip():
        pattern = re.escape(employee.strip())
        query["$or"] = [
            {"employee_name": {"$regex": pattern, "$options": "i"}},
            {"employee_id": {"$regex": pattern, "$options": "i"}},
        ]
    if department and department.strip():
        query["department"] = {"$regex": f"^{re.escape(department.strip())}$", "$options": "i"}
    if period:
        query["periods"] = period
    if attendance_date:
        query["attendance_dates"] = attendance_date.isoformat()
    records = attendance_requests_collection.find(query).sort("submitted_at", -1).limit(500)
    return [_serialize(item) for item in records]


def _employee_for_request(request: dict[str, Any]) -> dict[str, Any]:
    user_id = request.get("user_id", "")
    employee = users_collection.find_one({"_id": ObjectId(user_id)}) if ObjectId.is_valid(user_id) else None
    if not employee:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The employee for this request no longer exists.")
    return employee


def _record_matches_original(item: dict[str, Any], record: dict[str, Any] | None, current_status: str) -> bool:
    """True when the date still has the attendance the employee saw at submission."""
    if current_status != item.get("original_status", "Not Marked"):
        return False
    if not record:
        return True
    return (record.get("check_in_time"), record.get("check_out_time")) == (
        item.get("original_check_in_time"), item.get("original_check_out_time"),
    )


@router.get("/{request_id}")
def get_attendance_request(request_id: str, authorization: str | None = Header(default=None)):
    user = _current_user(authorization)
    request = _load_request(request_id)
    is_admin = _is_hr_admin(user)
    if not is_admin and request.get("user_id") != str(user["_id"]):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You are not authorized to view this attendance request.")
    result = _serialize(request)
    can_respond = is_admin and request.get("status") in ACTIVE_REQUEST_STATUSES
    employee = _employee_for_request(request) if can_respond else None
    payroll_cache: dict[str, str | None] = {}
    month_cache: dict[tuple[int, int], tuple[dict[str, Any], dict[str, int]]] = {}
    weekly_limit = _weekly_off_limit()
    for item in result["dates"]:
        item.setdefault("original_status", "Not Marked")
        record = attendance_collection.find_one({"user_id": request["user_id"], "attendance_date": item["attendance_date"]})
        payroll_status = _payroll_status(request.get("employee_id", ""), item["attendance_date"][:7], payroll_cache)
        lock_reason = _payroll_lock_reason(item["attendance_date"][:7], payroll_status)
        item["current_status"] = str(record.get("status")) if record else None
        item["current_check_in_time"] = record.get("check_in_time") if record else None
        item["current_check_out_time"] = record.get("check_out_time") if record else None
        item["current_attendance_category"] = _attendance_category(record)
        item["payroll_status"] = payroll_status
        item["payroll_locked"] = lock_reason is not None
        item["payroll_note"] = _payroll_draft_note(item["attendance_date"][:7], payroll_status)
        item["eligibility_message"] = lock_reason
        if not can_respond:
            continue
        day = date.fromisoformat(item["attendance_date"])
        key = (day.year, day.month)
        if key not in month_cache:
            ctx = _month_context(employee, *key)
            month_cache[key] = (ctx, _month_usage(employee, ctx, exclude_request_id=result["id"]))
        ctx, usage = month_cache[key]
        current_status = _day_status(day, record, ctx)
        item["current_status"] = current_status
        if not lock_reason and not _record_matches_original(item, record, current_status):
            item["eligibility_message"] = (
                f"Attendance for this date changed after the request was submitted (now {current_status}); "
                "approving will not overwrite it."
            )
        options, notes = _status_options(item["original_status"], usage, weekly_limit)
        if item["original_status"] != NOT_APPLICABLE:
            options.append(NOT_APPLICABLE)
        item["allowed_final_statuses"] = options
        item["option_notes"] = notes
    result["can_respond"] = can_respond
    result["limits"] = {"weekly_off_limit": weekly_limit, "medical_leave_limit": MEDICAL_LEAVE_MONTHLY_LIMIT}
    return result


ESSL_FIELDS = ("first_punch", "last_punch", "last_essl_sync_at", "last_essl_sync_id")
LEAVE_FIELDS = ("leave_request_id", "leave_day_part", "leave_type_code", "medical_leave")


def _apply_date_decision(
    request: dict[str, Any],
    item: dict[str, Any],
    decision: AttendanceDecisionIn,
    admin: dict[str, Any],
    employee: dict[str, Any],
    payroll_cache: dict[str, str | None],
) -> tuple[bool, str, str]:
    """Apply one approved decision. Returns (applied, final_status, message)."""
    request_id = str(request["_id"])
    user_id = request["user_id"]
    attendance_date = item["attendance_date"]
    final_status = decision.final_status or ""
    check_in = decision.final_check_in_time if final_status == "Present" else None
    check_out = decision.final_check_out_time if final_status == "Present" else None
    period = attendance_date[:7]
    lock_reason = _payroll_lock_reason(period, _payroll_status(request.get("employee_id", ""), period, payroll_cache))
    if lock_reason:
        return False, final_status, lock_reason
    key = {"user_id": user_id, "attendance_date": attendance_date}
    existing = attendance_collection.find_one(key)
    if existing and existing.get("attendance_request_id") == request_id:
        # Applied by an earlier, interrupted attempt of this same response.
        return True, final_status, "Recorded."
    day = date.fromisoformat(attendance_date)
    ctx = _month_context(employee, day.year, day.month)
    if ctx["joining"] and day < ctx["joining"]:
        return False, final_status, f"This date is before the employee's joining date ({ctx['joining'].isoformat()}); attendance was not changed."
    current_status = _day_status(day, existing, ctx)
    if not _record_matches_original(item, existing, current_status):
        return False, final_status, f"Attendance for this date changed after the request was submitted (now {current_status}); the existing record was preserved."
    if any(leave_item.get("day_part", "full_day") == "full_day" for leave_item in _approved_leaves_for_date(attendance_date, user_id)):
        return False, final_status, "An approved full-day leave covers this date; attendance was not changed."

    # Revalidate monthly limits immediately before writing.
    if final_status in {"Weekly Off", "Medical Leave"}:
        usage = _month_usage(employee, ctx, exclude_request_id=request_id)
        if final_status == "Weekly Off" and usage["weekly_off_used"] >= _weekly_off_limit():
            return False, final_status, f"Weekly Off would exceed the monthly limit of {_weekly_off_limit()}."
        if final_status == "Medical Leave":
            available, reason = _medical_leave_availability(usage, _weekly_off_limit())
            if not available:
                return False, final_status, f"Medical Leave is unavailable: {reason}"

    now = datetime.now(timezone.utc)
    values: dict[str, Any] = {
        "status": "Leave" if final_status == "Medical Leave" else final_status,
        # Times come only from the employee request / HR decision; never invented.
        "check_in_time": check_in,
        "check_out_time": check_out,
        "punch_details_missing": final_status == "Present" and not check_in,
        "attendance_source": "ATTENDANCE_REQUEST",
        "attendance_type": (existing or {}).get("attendance_type") if final_status == "Present" else None,
        "attendance_request_id": request_id,
        "attendance_request_number": request.get("request_number"),
        "marked_by": str(admin["_id"]),
        "updated_at": now,
    }
    unset = {field: "" for field in ESSL_FIELDS}
    if final_status == "Medical Leave":
        values.update({
            "leave_type_code": MEDICAL_LEAVE_CODE,
            "leave_day_part": "full_day",
            "leave_request_id": None,
            "medical_leave": True,
        })
    else:
        unset.update({field: "" for field in LEAVE_FIELDS})
    if existing:
        values["correction_previous"] = {
            "status": existing.get("status"),
            "check_in_time": existing.get("check_in_time"),
            "check_out_time": existing.get("check_out_time"),
            "attendance_source": existing.get("attendance_source"),
        }
        written = attendance_collection.update_one(
            {
                "_id": existing["_id"],
                "status": existing.get("status"),
                "check_in_time": existing.get("check_in_time"),
                "check_out_time": existing.get("check_out_time"),
            },
            {"$set": values, "$unset": unset},
        )
        if written.matched_count != 1:
            return False, final_status, "Attendance was changed while this response was being processed; the existing record was preserved."
    else:
        try:
            attendance_collection.insert_one({
                **values, "user_id": user_id, "employee_id": request.get("employee_id", ""),
                "attendance_date": attendance_date, "created_at": now,
            })
        except DuplicateKeyError:
            current = attendance_collection.find_one(key) or {}
            return False, final_status, f"Attendance was recorded as {current.get('status', 'another status')} while this response was being processed; the existing record was preserved."

    # A calculated (draft) payroll no longer matches attendance: flag it so it
    # cannot be approved until HR disapproves and recalculates. If payroll was
    # finalized in the meantime, undo this write instead.
    flagged = payroll_collection.update_one(
        {"employee_id": request.get("employee_id", ""), "period": period, "status": {"$in": list(PAYROLL_DRAFT_STATUSES)}},
        {
            "$set": {
                "attendance_outdated": True,
                "attendance_outdated_at": now,
                "attendance_outdated_reason": f"Attendance request {request.get('request_number')} changed attendance after payroll was calculated.",
            },
            "$addToSet": {"attendance_outdated_dates": attendance_date},
        },
    )
    payroll_note = ""
    if flagged.matched_count:
        payroll_note = f" Payroll for {period} was already calculated and is now marked outdated; disapprove and recalculate it."
    else:
        current_payroll = _payroll_status(request.get("employee_id", ""), period)
        if current_payroll in PAYROLL_FINALIZED_STATUSES:
            if existing:
                attendance_collection.replace_one({"_id": existing["_id"], "attendance_request_id": request_id}, existing)
            else:
                attendance_collection.delete_one({**key, "attendance_request_id": request_id})
            payroll_cache[period] = current_payroll
            return False, final_status, _payroll_lock_reason(period, current_payroll) or "Payroll is locked."

    try:
        audit_logs_collection.insert_one({
            "user_id": str(admin["_id"]),
            "action": "attendance.request_decision",
            "old_value": {
                "user_id": user_id, "attendance_date": attendance_date, "status": current_status,
                "check_in_time": (existing or {}).get("check_in_time"), "check_out_time": (existing or {}).get("check_out_time"),
            },
            "new_value": {
                "attendance_date": attendance_date, "status": values["status"], "final_status": final_status,
                "check_in_time": check_in, "check_out_time": check_out,
                "attendance_source": "ATTENDANCE_REQUEST", "attendance_request_id": request_id,
            },
            "timestamp": now,
        })
    except Exception:
        logger.exception("Unable to write audit log for attendance request %s on %s", request.get("request_number"), attendance_date)
    return True, final_status, f"Recorded as {final_status}{_times_text(check_in, check_out)}.{payroll_note}"


def _validate_decisions(request: dict[str, Any], payload: AttendanceRequestResponsePayload, employee: dict[str, Any]) -> dict[str, AttendanceDecisionIn]:
    requested_dates = [item["attendance_date"] for item in request.get("dates", [])]
    decisions: dict[str, AttendanceDecisionIn] = {}
    for decision in payload.decisions:
        iso = decision.attendance_date.isoformat()
        if iso in decisions:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{iso} has more than one decision.")
        decisions[iso] = decision
    unknown = sorted(set(decisions) - set(requested_dates))
    missing = [iso for iso in requested_dates if iso not in decisions]
    if unknown:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"These dates are not part of the request: {', '.join(unknown)}.")
    if missing:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Approve or reject every requested date. Missing: {', '.join(missing)}.")
    if any(decision.action == "reject" for decision in decisions.values()) and not payload.review_comments:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Enter response remarks when rejecting a date.")

    problems: list[str] = []
    added: dict[str, dict[str, int]] = {}
    weekly_limit = _weekly_off_limit()
    # Weekly Off days approved in this response also unlock Medical Leave.
    approved_weekly_offs: dict[str, int] = {}
    for iso, decision in decisions.items():
        if decision.action == "approve" and decision.final_status == "Weekly Off":
            approved_weekly_offs[iso[:7]] = approved_weekly_offs.get(iso[:7], 0) + 1
    usage_cache: dict[str, dict[str, int]] = {}
    for item in request.get("dates", []):
        iso = item["attendance_date"]
        decision = decisions[iso]
        if decision.action == "reject":
            continue
        original = item.get("original_status", "Not Marked")
        if not decision.final_status:
            problems.append(f"{iso}: select the final attendance status to approve.")
            continue
        hr_only = decision.final_status == NOT_APPLICABLE and original != NOT_APPLICABLE
        if not hr_only and decision.final_status not in STATUS_TRANSITIONS.get(original, ()):
            problems.append(f"{iso}: {decision.final_status} is not a permitted correction for a {original} date.")
            continue
        time_problem = _time_problem(
            decision.final_status, decision.final_check_in_time, decision.final_check_out_time,
            {"status": original, "check_in_time": item.get("original_check_in_time"), "check_out_time": item.get("original_check_out_time")},
        )
        if time_problem:
            problems.append(f"{iso}: {time_problem}")
            continue
        if decision.final_status in {"Weekly Off", "Medical Leave"}:
            period = iso[:7]
            if period not in usage_cache:
                day = date.fromisoformat(iso)
                usage_cache[period] = _month_usage(employee, _month_context(employee, day.year, day.month), exclude_request_id=str(request["_id"]))
                added[period] = {"Weekly Off": 0, "Medical Leave": 0}
            used = usage_cache[period]
            if decision.final_status == "Weekly Off" and used["weekly_off_used"] + added[period]["Weekly Off"] >= weekly_limit:
                problems.append(f"{iso}: Weekly Off would exceed the monthly limit of {weekly_limit}.")
                continue
            if decision.final_status == "Medical Leave":
                if used["medical_leave_used"] + added[period]["Medical Leave"] + 1 > MEDICAL_LEAVE_MONTHLY_LIMIT:
                    problems.append(f"{iso}: Medical Leave would exceed the monthly limit of {MEDICAL_LEAVE_MONTHLY_LIMIT}.")
                    continue
                available, reason = _medical_leave_availability(used, weekly_limit, approved_weekly_offs.get(period, 0))
                if not available:
                    problems.append(f"{iso}: Medical Leave is unavailable: {reason}")
                    continue
            added[period][decision.final_status] += 1
    if problems:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Correct these decisions: " + "; ".join(problems))
    return decisions


@router.post("/{request_id}/respond")
def respond_to_attendance_request(
    request_id: str,
    payload: AttendanceRequestResponsePayload,
    authorization: str | None = Header(default=None),
):
    admin = _require_hr_admin(authorization, "Only HR Admin or Administrator can respond to attendance requests.")
    request = _load_request(request_id)
    if request.get("user_id") == str(admin["_id"]):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You cannot respond to your own attendance request. Another HR Admin must review it.")
    if request.get("status") not in ACTIVE_REQUEST_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, f"This attendance request has already been {request.get('status', 'processed').replace('_', ' ')}.")
    employee = _employee_for_request(request)
    decisions = _validate_decisions(request, payload, employee)

    # Claim the request so only one reviewer can apply it; a stale claim from
    # an interrupted attempt can be retried after the timeout.
    now = datetime.now(timezone.utc)
    token = str(ObjectId())
    claimed = attendance_requests_collection.find_one_and_update(
        {
            "_id": request["_id"],
            "$or": [
                {"status": "pending"},
                {"status": "processing", "processing_started_at": {"$lt": now - PROCESSING_TIMEOUT}},
            ],
        },
        {"$set": {"status": "processing", "processing_token": token, "processing_by": str(admin["_id"]), "processing_started_at": now, "updated_at": now}},
        return_document=ReturnDocument.AFTER,
    )
    if not claimed:
        raise HTTPException(status.HTTP_409_CONFLICT, "This attendance request is already being processed or has been processed. Refresh the list.")

    def release_to_pending() -> None:
        attendance_requests_collection.update_one(
            {"_id": claimed["_id"], "processing_token": token},
            {"$set": {"status": "pending", "updated_at": datetime.now(timezone.utc)}, "$unset": {"processing_token": "", "processing_by": "", "processing_started_at": ""}},
        )

    # Serialize responses per employee and month so concurrent approvals cannot
    # exceed the monthly Weekly Off / Medical Leave limits.
    if attendance_requests_collection.find_one({
        "_id": {"$ne": claimed["_id"]}, "user_id": claimed["user_id"],
        "status": "processing", "periods": {"$in": claimed.get("periods", [])},
    }):
        release_to_pending()
        raise HTTPException(status.HTTP_409_CONFLICT, "Another response for this employee and month is being saved. Retry in a moment.")

    payroll_cache: dict[str, str | None] = {}
    results = []
    try:
        # Apply Medical Leave last so Weekly Off days approved here unlock it.
        ordered = sorted(claimed["dates"], key=lambda entry: decisions[entry["attendance_date"]].final_status == "Medical Leave")
        for item in ordered:
            item = {"original_status": "Not Marked", **item}
            decision = decisions[item["attendance_date"]]
            if decision.action == "reject":
                results.append({**item, "decision": "reject", "final_status": None, "final_check_in_time": None, "final_check_out_time": None,
                                "outcome": "rejected", "applied": False, "message": "Rejected by HR Admin; attendance unchanged."})
                continue
            applied, final_status, message = _apply_date_decision(claimed, item, decision, admin, employee, payroll_cache)
            final_in = decision.final_check_in_time if final_status == "Present" else None
            final_out = decision.final_check_out_time if final_status == "Present" else None
            if not applied:
                outcome = "not_applied"
            elif final_status == item["requested_status"] and (final_in, final_out) == (item.get("requested_check_in_time"), item.get("requested_check_out_time")):
                outcome = "approved"
            else:
                outcome = "changed"
            results.append({**item, "decision": "approve", "final_status": final_status, "final_check_in_time": final_in,
                            "final_check_out_time": final_out, "outcome": outcome, "applied": applied, "message": message})
    except Exception as exc:
        # Leave the claim to expire so a retry resumes idempotently; dates
        # already written are recognised by attendance_request_id.
        logger.exception("Attendance request %s failed while applying decisions", claimed.get("request_number"))
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "The response could not be fully saved. No notification was sent; please retry in a few minutes.") from exc

    results.sort(key=lambda entry: entry["attendance_date"])
    applied_count = sum(1 for item in results if item["applied"])
    rejected_count = sum(1 for item in results if item["outcome"] == "rejected")
    if applied_count == 0 and rejected_count == 0:
        # Nothing could be saved (all dates locked/changed): keep the request
        # pending, report every date, and do not notify the employee.
        release_to_pending()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"message": "No date could be updated, so the request is still pending.", "dates": [{"attendance_date": item["attendance_date"], "message": item["message"]} for item in results]},
        )

    overall = "approved" if applied_count == len(results) else ("rejected" if applied_count == 0 else "partially_approved")
    reviewed_at = datetime.now(timezone.utc)
    finished = attendance_requests_collection.find_one_and_update(
        {"_id": claimed["_id"], "status": "processing", "processing_token": token},
        {
            "$set": {
                "dates": results, "status": overall,
                "reviewed_by": str(admin["_id"]), "reviewed_by_name": admin.get("name", ""),
                "reviewed_at": reviewed_at, "review_comments": payload.review_comments, "updated_at": reviewed_at,
            },
            "$unset": {"processing_token": "", "processing_by": "", "processing_started_at": ""},
        },
        return_document=ReturnDocument.AFTER,
    )
    if not finished:
        raise HTTPException(status.HTTP_409_CONFLICT, "This request was taken over by another reviewer after a timeout. Refresh to see its current state.")
    _release_claims(str(claimed["_id"]))
    _audit(admin, "ATTENDANCE_REQUEST_RESPONDED", finished, {
        "status": overall,
        "dates": [{k: item.get(k) for k in ("attendance_date", "original_status", "requested_status", "requested_check_in_time",
                                             "requested_check_out_time", "decision", "final_status", "final_check_in_time",
                                             "final_check_out_time", "outcome", "applied")} for item in results],
    })

    def outcome_line(item: dict[str, Any]) -> str:
        requested = f"Requested {item['requested_status']}{_times_text(item.get('requested_check_in_time'), item.get('requested_check_out_time'))}"
        final = f"{item['final_status']}{_times_text(item.get('final_check_in_time'), item.get('final_check_out_time'))}"
        text = {
            "approved": f"Approved as {final}",
            "changed": f"Approved with changes as {final}",
            "rejected": "Rejected",
            "not_applied": f"Not applied ({item['message']})",
        }[item["outcome"]]
        return f"{_date_label(item['attendance_date'])} — {requested} — {text}"

    message = (
        f"Attendance request {finished['request_number']} submitted on {_local_text(finished['submitted_at'])} "
        f"was {overall.replace('_', ' ')} on {_local_text(reviewed_at)}. " + " | ".join(outcome_line(item) for item in results)
        + (f" | Remarks: {payload.review_comments}" if payload.review_comments else "")
    )
    try:
        _notify(finished["user_id"], f"Attendance Request {overall.replace('_', ' ').title()}", message, finished, "response")
        notification_sent = True
    except Exception:
        logger.exception("Unable to notify employee about attendance request %s", finished["request_number"])
        notification_sent = False

    return {
        **_serialize(finished),
        "summary": {
            "applied": applied_count,
            "rejected": rejected_count,
            "not_applied": len(results) - applied_count - rejected_count,
            "notification_sent": notification_sent,
            "outdated_payroll_periods": sorted(
                row["period"]
                for row in payroll_collection.find(
                    {"employee_id": finished.get("employee_id", ""), "period": {"$in": finished.get("periods", [])}, "attendance_outdated": True, "status": {"$in": list(PAYROLL_DRAFT_STATUSES)}},
                    {"period": 1},
                )
            ),
        },
    }
