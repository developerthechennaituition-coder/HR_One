"""Employee attendance correction requests with per-date HR Admin decisions.

Employees request Present/Holiday for their own Not Marked past dates. HR Admin
decides every date individually (Present, Holiday or Absent); decisions are
written to the canonical ``attendance`` collection (one record per employee and
date) and are blocked while payroll for that month is calculated or finalized.
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
    COMPANY_TZ,
    WEEKDAY_NAMES,
    company_weekly_off_weekdays,
    _approved_leaves_for_date,
    _current_user,
    _require_hr_admin,
    _sync_leave_for_user_range,
    attendance_collection,
    audit_logs_collection,
    company_today,
    users_collection,
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

REQUESTABLE_STATUSES = ("Present", "Holiday")
DECISION_STATUSES = ("Present", "Holiday", "Absent")
REQUEST_STATUSES = ("pending", "processing", "approved", "partially_approved", "rejected", "cancelled")
# Payroll statuses from app.payroll: Calculated is a draft that HR can
# disapprove; Approved/Processed/Paid are finalized.
PAYROLL_FINALIZED_STATUSES = {"Approved", "Processed", "Paid"}
PAYROLL_DRAFT_STATUSES = {"Calculated"}
PROCESSING_TIMEOUT = timedelta(minutes=5)
MAX_DATES_PER_REQUEST = 31


def ensure_attendance_request_indexes() -> None:
    attendance_request_date_claims_collection.create_index(
        [("user_id", 1), ("attendance_date", 1)], unique=True,
    )
    attendance_request_date_claims_collection.create_index("request_id")
    attendance_requests_collection.create_index("request_number", unique=True)
    attendance_requests_collection.create_index([("user_id", 1), ("submitted_at", -1)])
    attendance_requests_collection.create_index([("status", 1), ("submitted_at", -1)])
    attendance_requests_collection.create_index([("employee_id", 1), ("status", 1), ("periods", 1)])


@router.on_event("startup")
def startup_attendance_requests() -> None:
    ensure_attendance_request_indexes()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class AttendanceRequestDateIn(BaseModel):
    attendance_date: date
    requested_status: Literal["Present", "Holiday"]


class AttendanceRequestCreate(BaseModel):
    dates: list[AttendanceRequestDateIn] = Field(min_length=1, max_length=MAX_DATES_PER_REQUEST)
    remarks: str | None = Field(default=None, max_length=1000)

    @field_validator("remarks")
    @classmethod
    def strip_remarks(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        return value or None


class AttendanceDecisionIn(BaseModel):
    attendance_date: date
    final_status: Literal["Present", "Holiday", "Absent"]


class AttendanceRequestResponsePayload(BaseModel):
    decisions: list[AttendanceDecisionIn] = Field(min_length=1, max_length=MAX_DATES_PER_REQUEST)
    review_comments: str | None = Field(default=None, max_length=1000)

    @field_validator("review_comments")
    @classmethod
    def strip_comments(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        return value or None


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


def _validate_month(year: int, month: int) -> tuple[date, date]:
    today = company_today()
    if month < 1 or month > 12:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid month.")
    if year < 2000 or (year, month) > (today.year, today.month):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Future months cannot be viewed.")
    return date(year, month, 1), date(year, month, monthrange(year, month)[1])


def _build_calendar(user: dict[str, Any], year: int, month: int) -> dict[str, Any]:
    """Authoritative per-date status and request eligibility for one month."""
    start, end = _validate_month(year, month)
    user_id = str(user["_id"])
    employee_id = str(user.get("employee_id") or "")
    today = company_today()
    period = f"{year:04d}-{month:02d}"

    # Same lazy approved-leave sync as GET /api/attendance/my (idempotent).
    _sync_leave_for_user_range(user_id, start.isoformat(), end.isoformat())
    records = {
        str(record.get("attendance_date"))[:10]: record
        for record in attendance_collection.find(
            {"user_id": user_id, "attendance_date": {"$gte": start.isoformat(), "$lte": end.isoformat()}}
        )
    }
    holidays = leave._holiday_dates(user, start, end)
    weekly_offs = company_weekly_off_weekdays()
    joining = leave._joining_date(user)
    claims = {
        claim["attendance_date"]: claim["request_id"]
        for claim in attendance_request_date_claims_collection.find(
            {"user_id": user_id, "attendance_date": {"$gte": start.isoformat(), "$lte": end.isoformat()}}
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

    days: list[dict[str, Any]] = []
    current = start
    while current <= end:
        iso = current.isoformat()
        record = records.get(iso)
        is_holiday = current in holidays
        is_weekly_off = current.weekday() in weekly_offs
        if record:
            day_status = str(record.get("status") or "Not Marked")
        elif current > today:
            day_status = "Upcoming"
        elif is_holiday:
            day_status = "Holiday"
        elif is_weekly_off:
            day_status = "Weekly Off"
        elif joining and current < joining:
            day_status = "Not Applicable"
        else:
            day_status = "Not Marked"

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

        reason = None
        if day_status != "Not Marked":
            reason = "Only Not Marked dates can be requested."
        elif current >= today:
            reason = "Today's attendance can still be marked; requests are for past dates."
        elif request_info:
            reason = f"Request {request_info['request_number']} is already {request_info['status']} for this date."
        elif payroll_lock:
            reason = _payroll_lock_reason(period, payroll_status)

        days.append({
            "attendance_date": iso,
            "status": day_status,
            "attendance_category": _attendance_category(record),
            "attendance_source": record.get("attendance_source") if record else None,
            "check_in_time": record.get("check_in_time") if record else None,
            "check_out_time": record.get("check_out_time") if record else None,
            "leave_type_code": record.get("leave_type_code") if record else None,
            "is_holiday": is_holiday,
            "is_weekly_off": is_weekly_off,
            "is_future": current > today,
            "has_record": record is not None,
            "request": request_info,
            "eligible_for_request": reason is None,
            "ineligible_reason": reason,
        })
        current += timedelta(days=1)

    return {
        "year": year,
        "month": month,
        "period": period,
        "payroll_status": payroll_status,
        "payroll_locked": payroll_lock,
        "weekly_off_weekdays": [WEEKDAY_NAMES[index] for index in sorted(weekly_offs)],
        "weekly_off_configured": bool(weekly_offs),
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
    return date.fromisoformat(iso).strftime("%d %b %Y")


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

    calendars: dict[tuple[int, int], dict[str, dict[str, Any]]] = {}
    problems = []
    for item in requested:
        key = (item.attendance_date.year, item.attendance_date.month)
        if key not in calendars:
            try:
                calendars[key] = {day["attendance_date"]: day for day in _build_calendar(user, *key)["days"]}
            except HTTPException as exc:
                problems.append(f"{item.attendance_date.isoformat()}: {exc.detail}")
                calendars[key] = {}
                continue
        day = calendars[key].get(item.attendance_date.isoformat())
        if not day:
            continue
        if not day["eligible_for_request"]:
            problems.append(f"{item.attendance_date.isoformat()}: {day['ineligible_reason']}")
        elif any(leave_item.get("day_part", "full_day") == "full_day" for leave_item in _approved_leaves_for_date(item.attendance_date.isoformat(), user_id)):
            problems.append(f"{item.attendance_date.isoformat()}: An approved full-day leave covers this date.")
    if problems:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "These dates are not eligible for an attendance request: " + "; ".join(problems))

    request_id = ObjectId()
    claimed: list[str] = []
    try:
        for iso in isos:
            attendance_request_date_claims_collection.insert_one(
                {"user_id": user_id, "attendance_date": iso, "request_id": str(request_id), "created_at": datetime.now(timezone.utc)}
            )
            claimed.append(iso)
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
        "dates": [
            {
                "attendance_date": item.attendance_date.isoformat(),
                "requested_status": item.requested_status,
                "final_status": None,
                "outcome": None,
                "applied": False,
                "message": None,
            }
            for item in requested
        ],
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

    _audit(user, "ATTENDANCE_REQUEST_SUBMITTED", record, {"dates": isos})
    summary = ", ".join(f"{_date_label(item['attendance_date'])} ({item['requested_status']})" for item in record["dates"])
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


@router.get("/{request_id}")
def get_attendance_request(request_id: str, authorization: str | None = Header(default=None)):
    user = _current_user(authorization)
    request = _load_request(request_id)
    is_admin = _is_hr_admin(user)
    if not is_admin and request.get("user_id") != str(user["_id"]):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You are not authorized to view this attendance request.")
    result = _serialize(request)
    payroll_cache: dict[str, str | None] = {}
    for item in result["dates"]:
        record = attendance_collection.find_one({"user_id": request["user_id"], "attendance_date": item["attendance_date"]})
        payroll_status = _payroll_status(request.get("employee_id", ""), item["attendance_date"][:7], payroll_cache)
        lock_reason = _payroll_lock_reason(item["attendance_date"][:7], payroll_status)
        item["current_status"] = str(record.get("status")) if record else "Not Marked"
        item["current_check_in_time"] = record.get("check_in_time") if record else None
        item["current_check_out_time"] = record.get("check_out_time") if record else None
        item["current_attendance_category"] = _attendance_category(record)
        item["payroll_status"] = payroll_status
        item["payroll_locked"] = lock_reason is not None
        item["payroll_note"] = _payroll_draft_note(item["attendance_date"][:7], payroll_status)
        item["eligibility_message"] = lock_reason or (
            None if not record or record.get("attendance_request_id") == result["id"]
            else f"Attendance is already recorded as {record.get('status')}; the existing record will be preserved."
        )
    result["can_respond"] = is_admin and request.get("status") in {"pending", "processing"}
    return result


def _apply_date_decision(
    request: dict[str, Any],
    attendance_date: str,
    final_status: str,
    admin: dict[str, Any],
    payroll_cache: dict[str, str | None],
) -> tuple[bool, str, str]:
    """Apply one decision. Returns (applied, final_status, message)."""
    request_id = str(request["_id"])
    user_id = request["user_id"]
    period = attendance_date[:7]
    lock_reason = _payroll_lock_reason(period, _payroll_status(request.get("employee_id", ""), period, payroll_cache))
    if lock_reason:
        return False, final_status, lock_reason
    key = {"user_id": user_id, "attendance_date": attendance_date}
    existing = attendance_collection.find_one(key)
    if existing:
        if existing.get("attendance_request_id") == request_id:
            # Applied by an earlier, interrupted attempt of this same response.
            return True, str(existing.get("status")), f"Recorded as {existing.get('status')}."
        return False, final_status, f"Attendance is already recorded as {existing.get('status')}; the existing record was preserved."
    if any(item.get("day_part", "full_day") == "full_day" for item in _approved_leaves_for_date(attendance_date, user_id)):
        return False, final_status, "An approved full-day leave covers this date; attendance was not changed."
    now = datetime.now(timezone.utc)
    record = {
        "user_id": user_id,
        "employee_id": request.get("employee_id", ""),
        "attendance_date": attendance_date,
        "status": final_status,
        # Never fabricate punches: request-based attendance has no times.
        "check_in_time": None,
        "check_out_time": None,
        "punch_details_missing": final_status == "Present",
        "attendance_source": "ATTENDANCE_REQUEST",
        "attendance_type": None,
        "attendance_request_id": request_id,
        "attendance_request_number": request.get("request_number"),
        "marked_by": str(admin["_id"]),
        "created_at": now,
        "updated_at": now,
    }
    try:
        attendance_collection.insert_one(record)
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
            attendance_collection.delete_one({**key, "attendance_request_id": request_id})
            payroll_cache[period] = current_payroll
            return False, final_status, _payroll_lock_reason(period, current_payroll) or "Payroll is locked."
    try:
        audit_logs_collection.insert_one({
            "user_id": str(admin["_id"]),
            "action": "attendance.request_decision",
            "old_value": {"user_id": user_id, "attendance_date": attendance_date, "status": "Not Marked"},
            "new_value": {"attendance_date": attendance_date, "status": final_status, "attendance_source": "ATTENDANCE_REQUEST", "attendance_request_id": request_id},
            "timestamp": now,
        })
    except Exception:
        logger.exception("Unable to write audit log for attendance request %s on %s", request.get("request_number"), attendance_date)
    return True, final_status, f"Recorded as {final_status}.{payroll_note}"


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
    if request.get("status") not in {"pending", "processing"}:
        raise HTTPException(status.HTTP_409_CONFLICT, f"This attendance request has already been {request.get('status', 'processed').replace('_', ' ')}.")

    requested_dates = [item["attendance_date"] for item in request.get("dates", [])]
    decisions: dict[str, str] = {}
    for item in payload.decisions:
        iso = item.attendance_date.isoformat()
        if iso in decisions:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{iso} has more than one decision.")
        decisions[iso] = item.final_status
    unknown = sorted(set(decisions) - set(requested_dates))
    missing = [iso for iso in requested_dates if iso not in decisions]
    if unknown:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"These dates are not part of the request: {', '.join(unknown)}.")
    if missing:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Select a final status for every requested date. Missing: {', '.join(missing)}.")

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

    payroll_cache: dict[str, str | None] = {}
    results = []
    try:
        for item in claimed["dates"]:
            iso = item["attendance_date"]
            applied, final_status, message = _apply_date_decision(claimed, iso, decisions[iso], admin, payroll_cache)
            if not applied:
                outcome = "not_applied"
            elif final_status == item["requested_status"]:
                outcome = "approved"
            elif final_status == "Absent":
                outcome = "rejected"
            else:
                outcome = "changed"
            results.append({**item, "final_status": final_status, "outcome": outcome, "applied": applied, "message": message})
    except Exception as exc:
        # Leave the claim to expire so a retry resumes idempotently; dates
        # already written are recognised by attendance_request_id.
        logger.exception("Attendance request %s failed while applying decisions", claimed.get("request_number"))
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "The response could not be fully saved. No notification was sent; please retry in a few minutes.") from exc

    applied_count = sum(1 for item in results if item["applied"])
    if applied_count == 0:
        # Nothing could be saved (all dates locked/ineligible): keep the
        # request pending, report every date, and do not notify the employee.
        attendance_requests_collection.update_one(
            {"_id": claimed["_id"], "processing_token": token},
            {"$set": {"status": "pending", "updated_at": datetime.now(timezone.utc)}, "$unset": {"processing_token": "", "processing_by": "", "processing_started_at": ""}},
        )
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"message": "No date could be updated, so the request is still pending.", "dates": [{"attendance_date": item["attendance_date"], "message": item["message"]} for item in results]},
        )

    approved_count = sum(1 for item in results if item["outcome"] == "approved")
    overall = "approved" if approved_count == len(results) else ("rejected" if approved_count == 0 else "partially_approved")
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
    _audit(admin, "ATTENDANCE_REQUEST_RESPONDED", finished, {"status": overall, "dates": [{k: item[k] for k in ("attendance_date", "requested_status", "final_status", "outcome", "applied")} for item in results]})

    outcome_text = {
        "approved": "Approved as {final}",
        "changed": "Changed to {final}",
        "rejected": "Changed to {final}",
        "not_applied": "Not applied ({message})",
    }
    lines = [
        f"{_date_label(item['attendance_date'])} — Requested {item['requested_status']} — "
        + outcome_text[item["outcome"]].format(final=item["final_status"], message=item["message"])
        for item in results
    ]
    message = (
        f"Attendance request {finished['request_number']} submitted on {_local_text(finished['submitted_at'])} "
        f"was {overall.replace('_', ' ')} on {_local_text(reviewed_at)}. " + " | ".join(lines)
        + (f" | Comments: {payload.review_comments}" if payload.review_comments else "")
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
            "not_applied": len(results) - applied_count,
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
