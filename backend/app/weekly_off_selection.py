"""Employee weekly-off selection and HR Admin review."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from bson import ObjectId
from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app import attendance_requests as attendance_request_logic
from app.attendance import (
    attendance_collection,
    company_today,
    employee_joining_date,
    users_collection,
)
from app.db.mongodb import db
from app.weekly_off_planner import monthly_weekly_off_entitlement

router = APIRouter(prefix="/api/attendance-requests/weekly-offs", tags=["Weekly Off Selection"])
logger = logging.getLogger(__name__)

attendance_requests_collection = db["attendance_requests"]
payroll_collection = db["monthly_payroll_records"]
FINALIZED_PAYROLL_STATUSES = {"Approved", "Processed", "Paid"}
FORWARD_DAYS = 14
PUNCH_FIELDS = (
    "check_in_time", "check_out_time", "first_half_check_in_time",
    "second_half_check_in_time", "first_punch", "last_punch",
    "check_in_at", "check_out_at",
)


class WeeklyOffSelectionPayload(BaseModel):
    attendance_dates: list[date] = Field(min_length=1, max_length=15)


class WeeklyOffDecisionPayload(BaseModel):
    action: Literal["approve", "reject"]
    remarks: str | None = Field(default=None, max_length=1000)


def _request_id(request: dict[str, Any]) -> str:
    return str(request["_id"])


def _public_request(request: dict[str, Any]) -> dict[str, Any]:
    item = (request.get("dates") or [{}])[0]
    return {
        "id": _request_id(request),
        "request_number": request.get("request_number"),
        "employee_id": request.get("employee_id", ""),
        "employee_name": request.get("employee_name", ""),
        "department": request.get("department", ""),
        "attendance_date": item.get("attendance_date"),
        "original_status": item.get("original_status", "Not Marked"),
        "status": request.get("status"),
        "approval_required": bool(request.get("approval_required")),
        "approval_reason": request.get("approval_reason"),
        "approval_stage": request.get("approval_stage"),
        "approver_id": request.get("approver_id"),
        "approver_name": request.get("approver_name"),
        "original_approver_id": request.get("reviewed_by"),
        "original_approver_name": request.get("reviewed_by_name"),
        "previous_weekly_off_date": request.get("previous_weekly_off_date"),
        "submitted_at": request.get("submitted_at"),
        "reviewed_by_name": request.get("reviewed_by_name"),
        "reviewed_at": request.get("reviewed_at"),
        "review_comments": request.get("review_comments"),
        "cancellation": request.get("cancellation"),
        "cancellation_history": request.get("cancellation_history") or [],
        "payroll_status": request.get("payroll_status"),
        "auto_applied": bool(request.get("auto_applied")),
    }


def _month_context(user: dict[str, Any], year: int, month: int) -> dict[str, Any]:
    return attendance_request_logic._month_context(user, year, month)


def _month_entitlement(user: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    limit = monthly_weekly_off_entitlement(ctx.get("joining"), ctx["start"].year, ctx["start"].month)
    usage = attendance_request_logic._month_usage(user, ctx)
    period = ctx["start"].strftime("%Y-%m")
    payroll_status = attendance_request_logic._payroll_status(str(user.get("employee_id") or ""), period)
    return {
        "period": period,
        "limit": limit,
        "used": int(usage["weekly_off_used"]),
        "remaining": max(0, limit - int(usage["weekly_off_used"])),
        "payroll_status": payroll_status,
        "payroll_locked": payroll_status in FINALIZED_PAYROLL_STATUSES,
    }


def _has_activity(record: dict[str, Any] | None) -> bool:
    return bool(record and any(record.get(field) for field in PUNCH_FIELDS))


def _needs_approval(day_status: str, record: dict[str, Any] | None) -> tuple[bool, str | None]:
    if day_status in {"Holiday", "Leave", "Absent"}:
        return True, f"Current attendance is {day_status}; HR Admin approval is required."
    if day_status in {"Not Marked", "Upcoming"} and not _has_activity(record):
        return False, None
    return True, f"Current attendance is {day_status}; HR Admin approval is required before it can be changed."


def _holiday_proximity_reason(user: dict[str, Any], selected: date) -> str | None:
    """Only the calendar day immediately before or after a holiday needs HR Admin."""
    holidays = attendance_request_logic.leave._holiday_names(
        user, selected - timedelta(days=1), selected + timedelta(days=1),
    )
    nearby = [
        (abs((holiday_date - selected).days), holiday_date, name)
        for holiday_date, name in holidays.items()
        if abs((holiday_date - selected).days) == 1
    ]
    if not nearby:
        return None
    gap, holiday_date, name = min(nearby)
    relation = "before" if holiday_date > selected else "after"
    return (
        f"This Weekly Off is immediately {relation} the approved holiday "
        f"({name}) on {holiday_date.isoformat()}; HR Admin approval is required."
    )


def _previous_weekly_off_within_three_days(
    user: dict[str, Any], selected: date, additional_dates: list[date] | None = None,
) -> date | None:
    """Return the nearest earlier selected/approved Weekly Off within 1–3 days."""
    candidates: set[date] = {
        value for value in (additional_dates or [])
        if value < selected and 1 <= (selected - value).days <= 3
    }
    user_id = str(user.get("_id") or "")
    for request in attendance_requests_collection.find({
        "user_id": user_id,
        "request_type": "weekly_off_selection",
        "status": {"$nin": ["rejected", "cancelled"]},
    }, {"attendance_dates": 1, "dates.attendance_date": 1}):
        values = request.get("attendance_dates") or [
            item.get("attendance_date") for item in request.get("dates", [])
        ]
        for value in values:
            try:
                prior = date.fromisoformat(str(value)[:10])
            except (TypeError, ValueError):
                continue
            if prior < selected and 1 <= (selected - prior).days <= 3:
                candidates.add(prior)
    # Include legacy/manual Weekly Off attendance records that predate this
    # selection workflow, without treating the selected date itself as prior.
    for record in attendance_collection.find({
        "user_id": user_id, "status": "Weekly Off",
        "attendance_date": {"$lt": selected.isoformat(), "$gte": (selected - timedelta(days=3)).isoformat()},
    }, {"attendance_date": 1}):
        try:
            prior = date.fromisoformat(str(record.get("attendance_date"))[:10])
        except (TypeError, ValueError):
            continue
        if 1 <= (selected - prior).days <= 3:
            candidates.add(prior)
    return max(candidates) if candidates else None


def _approval_requirement(
    user: dict[str, Any],
    selected: date,
    day_status: str,
    record: dict[str, Any] | None,
    additional_weekly_off_dates: list[date] | None = None,
) -> tuple[bool, str | None]:
    required, reason = _needs_approval(day_status, record)
    holiday_reason = _holiday_proximity_reason(user, selected)
    previous_date = _previous_weekly_off_within_three_days(user, selected, additional_weekly_off_dates)
    previous_reason = (
        f"Another Weekly Off was selected on {previous_date.isoformat()}, only "
        f"{(selected - previous_date).days} calendar day(s) before {selected.isoformat()}; "
        "HR Admin approval is required because the gap is less than 4 days."
        if previous_date else None
    )
    reasons = [value for value in (reason, holiday_reason, previous_reason) if value]
    return bool(required or holiday_reason or previous_reason), " ".join(reasons) or None


def _resolve_weekly_off_approver(user: dict[str, Any], approval_stage: str) -> tuple[str, dict[str, Any]]:
    """Resolve the one authority assigned to this request and validate it."""
    stage = "hr_admin" if approval_stage == "hr_admin" else "reporting_manager"
    if stage == "hr_admin":
        admins = list(users_collection.find(
            {"is_active": True, "role": {"$regex": r"^\s*(hr admin|administrator)\s*$", "$options": "i"}},
            {"_id": 1, "name": 1, "role": 1},
        ).sort("_id", 1).limit(1))
        if not admins:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No active HR Admin is available to approve this Weekly Off request.")
        return stage, admins[0]
    manager_id = attendance_request_logic.leave._get_reporting_manager_id(user)
    if not manager_id or not ObjectId.is_valid(str(manager_id)):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No valid Reporting Manager is assigned. Please ask HR Admin to assign a Reporting Manager before submitting Weekly Off dates.")
    manager = users_collection.find_one({"_id": ObjectId(str(manager_id)), "is_active": True})
    if not manager or str(manager["_id"]) == str(user["_id"]):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The assigned Reporting Manager is unavailable. Please ask HR Admin to update the Reporting Manager assignment.")
    return stage, manager


def _notify_approver(request: dict[str, Any], cancellation: bool = False) -> bool:
    """Create one idempotent leave-style notification for the assigned approver."""
    notification_collection = attendance_request_logic.leave_notifications_collection
    sequences = attendance_request_logic.notification_sequences_collection
    request_id = _request_id(request)
    item = (request.get("dates") or [{}])[0]
    cancellation_data = request.get("cancellation") or {}
    recipient_value = cancellation_data.get("approver_id") if cancellation else request.get("approver_id")
    recipient_id = str(recipient_value or "")
    if not ObjectId.is_valid(recipient_id):
        logger.error("Weekly Off request %s has no valid approver", request.get("request_number"))
        return False
    recipient = users_collection.find_one({"_id": ObjectId(recipient_id), "is_active": True})
    if not recipient:
        return False
    stage = cancellation_data.get("approval_stage") or request.get("approval_stage") or "reporting_manager"
    authority = "HR Admin" if stage == "hr_admin" else "Reporting Manager"
    event = "cancellation" if cancellation else "approval"
    attempt_key = str(cancellation_data.get("requested_at") or "legacy") if cancellation else "initial"
    key = f"weekly_off:{request_id}:{event}:{recipient_id}:{attempt_key}"
    if notification_collection.find_one({"weekly_off_event_key": key}):
        return True
    if cancellation:
        title = "Weekly Off Cancellation Approval Required"
        message = (
            f"{request.get('employee_name', 'Employee')} ({request.get('employee_id', '')}) "
            f"requested cancellation of the Weekly Off on {item.get('attendance_date')}. "
            f"Please review this cancellation as the original approving authority."
        )
    elif stage == "hr_admin":
        title = "HR Admin Weekly Off Approval Required"
        message = (
            f"{request.get('employee_name', 'Employee')} ({request.get('employee_id', '')}) "
            f"requested Weekly Off on {item.get('attendance_date')}. "
            f"{request.get('approval_reason') or 'HR Admin approval is required.'}"
        )
    else:
        title = "Weekly Off Approval Required"
        message = (
            f"{request.get('employee_name', 'Employee')} ({request.get('employee_id', '')}) "
            f"submitted a Weekly Off request for {item.get('attendance_date')}. "
            "Please review and approve or reject the request."
        )
    try:
        sequence = sequences.find_one_and_update(
            {"_id": "leave_notification"}, {"$inc": {"value": 1}},
            upsert=True, return_document=ReturnDocument.AFTER,
        )
        notification_collection.insert_one({
            "leave_notification_id": f"L{int(sequence['value']):03d}",
            "weekly_off_event_key": key,
            "recipient_user_id": recipient_id,
            "employee_name": request.get("employee_name"),
            "employee_id": request.get("employee_id"),
            "leave_request_id": None,
            "attendance_request_id": None,
            "weekly_off_request_id": request_id,
            "related_user_id": request.get("user_id"),
            "related_user_name": request.get("employee_name"),
            "notification_type": "weekly_off_cancellation" if cancellation else "weekly_off_request",
            "title": title,
            "message": message,
            "is_read": False, "is_starred": False, "is_deleted": False,
            "created_at": datetime.now(timezone.utc), "expires_at": None,
            "read_at": None, "deleted_at": None,
        })
        return True
    except DuplicateKeyError:
        return True
    except Exception:
        logger.exception("Unable to notify %s %s about Weekly Off request %s", authority, recipient_id, request.get("request_number"))
        return False


def _notify_employee_decision(request: dict[str, Any], approved: bool, admin: dict[str, Any]) -> None:
    """Notify the employee after a persisted HR Admin decision; safe to retry."""
    recipient_id = str(request.get("user_id") or "")
    request_id = _request_id(request)
    item = (request.get("dates") or [{}])[0]
    notification_collection = attendance_request_logic.leave_notifications_collection
    sequences = attendance_request_logic.notification_sequences_collection
    key = f"weekly_off:{request_id}:decision:{recipient_id}"
    try:
        if notification_collection.find_one({"weekly_off_event_key": key}):
            return
        recipient = users_collection.find_one({"_id": ObjectId(recipient_id)}) if ObjectId.is_valid(recipient_id) else None
        if not recipient:
            return
        action = "approved" if approved else "rejected"
        authority = "HR Admin" if request.get("approval_stage") == "hr_admin" else "your Reporting Manager"
        sequence = sequences.find_one_and_update(
            {"_id": "leave_notification"}, {"$inc": {"value": 1}},
            upsert=True, return_document=ReturnDocument.AFTER,
        )
        notification_collection.insert_one({
            "leave_notification_id": f"L{int(sequence['value']):03d}",
            "weekly_off_event_key": key,
            "recipient_user_id": recipient_id,
            "employee_name": request.get("employee_name"),
            "employee_id": request.get("employee_id"),
            "leave_request_id": None,
            "attendance_request_id": None,
            "weekly_off_request_id": request_id,
            "related_user_id": recipient_id,
            "related_user_name": request.get("employee_name"),
            "approver_user_id": str(admin.get("_id") or ""),
            "approver_name": admin.get("name"),
            "notification_type": "weekly_off_decision",
            "title": f"Weekly Off Request {action.title()}",
            "message": f"Your Weekly Off request {request.get('request_number')} for {item.get('attendance_date')} was {action} by {admin.get('name') or 'HR Admin'}." + (f" Remarks: {request.get('review_comments')}" if request.get("review_comments") else ""),
            "is_read": False, "is_starred": False, "is_deleted": False,
            "created_at": datetime.now(timezone.utc), "expires_at": None,
            "read_at": None, "deleted_at": None,
        })
    except DuplicateKeyError:
        return
    except Exception:
        logger.exception("Weekly Off decision was saved but employee notification failed for %s", request.get("request_number"))


def _notify_employee_cancellation_decision(request: dict[str, Any], approved: bool, approver: dict[str, Any]) -> None:
    recipient_id = str(request.get("user_id") or "")
    request_id = _request_id(request)
    cancellation = request.get("cancellation") or {}
    attempt_key = str(cancellation.get("requested_at") or "legacy")
    key = f"weekly_off:{request_id}:cancellation_decision:{recipient_id}:{attempt_key}"
    collection = attendance_request_logic.leave_notifications_collection
    sequences = attendance_request_logic.notification_sequences_collection
    if collection.find_one({"weekly_off_event_key": key}):
        return
    if not ObjectId.is_valid(recipient_id) or not users_collection.find_one({"_id": ObjectId(recipient_id), "is_active": True}):
        return
    stage = (request.get("cancellation") or {}).get("approval_stage") or request.get("approval_stage") or "reporting_manager"
    authority = "HR Admin" if stage == "hr_admin" else "your Reporting Manager"
    action = "approved" if approved else "rejected"
    try:
        sequence = sequences.find_one_and_update(
            {"_id": "leave_notification"}, {"$inc": {"value": 1}},
            upsert=True, return_document=ReturnDocument.AFTER,
        )
        item = (request.get("dates") or [{}])[0]
        collection.insert_one({
            "leave_notification_id": f"L{int(sequence['value']):03d}",
            "weekly_off_event_key": key,
            "recipient_user_id": recipient_id,
            "employee_name": request.get("employee_name"),
            "employee_id": request.get("employee_id"),
            "weekly_off_request_id": request_id,
            "related_user_id": recipient_id,
            "related_user_name": request.get("employee_name"),
            "approver_user_id": str(approver.get("_id") or ""),
            "approver_name": approver.get("name"),
            "notification_type": "weekly_off_cancellation_decision",
            "title": f"Weekly Off Cancellation {action.title()}",
            "message": f"Your cancellation request for Weekly Off on {item.get('attendance_date')} was {action} by {authority} ({approver.get('name') or 'approver'}).",
            "is_read": False, "is_starred": False, "is_deleted": False,
            "created_at": datetime.now(timezone.utc), "expires_at": None,
            "read_at": None, "deleted_at": None,
        })
    except DuplicateKeyError:
        return
    except Exception:
        logger.exception("Unable to notify employee about Weekly Off cancellation decision %s", request.get("request_number"))


def _load_weekly_off_request(request_id: str) -> dict[str, Any]:
    if not ObjectId.is_valid(request_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid Weekly Off request ID.")
    request = attendance_requests_collection.find_one({
        "_id": ObjectId(request_id), "request_type": "weekly_off_selection",
    })
    if not request:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Weekly Off request not found.")
    return request


def _month_data_for_dates(user: dict[str, Any], dates: list[date]) -> dict[str, dict[str, Any]]:
    contexts: dict[str, dict[str, Any]] = {}
    for selected in dates:
        period = selected.strftime("%Y-%m")
        if period not in contexts:
            contexts[period] = _month_context(user, selected.year, selected.month)
    return contexts


def _existing_claim(user_id: str, iso: str) -> dict[str, Any] | None:
    return attendance_request_logic.attendance_request_date_claims_collection.find_one({"user_id": user_id, "attendance_date": iso})


def _selection_records(user_id: str) -> list[dict[str, Any]]:
    records = attendance_requests_collection.find(
        {"user_id": user_id, "request_type": "weekly_off_selection"},
    ).sort("submitted_at", -1).limit(200)
    return [_public_request(item) for item in records]


@router.get("/my")
def my_weekly_off_selections(authorization: str | None = Header(default=None)):
    user = attendance_request_logic._current_user(authorization)
    today = company_today()
    end = today + timedelta(days=FORWARD_DAYS)
    dates: list[date] = []
    current = today
    while current <= end:
        dates.append(current)
        current += timedelta(days=1)
    contexts = _month_data_for_dates(user, dates)
    joining = employee_joining_date(user)
    user_id = str(user["_id"])
    month_summaries = {
        period: _month_entitlement(user, ctx)
        for period, ctx in contexts.items()
    }
    eligible_dates = []
    for selected in dates:
        iso = selected.isoformat()
        period = selected.strftime("%Y-%m")
        ctx = contexts[period]
        summary = month_summaries[period]
        record = ctx["records"].get(iso)
        day_status = attendance_request_logic._day_status(selected, record, ctx)
        reason = None
        claim = _existing_claim(user_id, iso)
        if joining and selected < joining:
            reason = "This date is before your joining date."
        elif claim:
            reason = "A Weekly Off selection or attendance request already covers this date."
        elif summary["payroll_locked"]:
            reason = f"Payroll for {period} is {summary['payroll_status']}; this month is locked."
        elif summary["remaining"] <= 0:
            reason = f"The monthly entitlement of {summary['limit']} Weekly Off days has been used."
        elif day_status == "Weekly Off":
            reason = "This date is already recorded as a Weekly Off."
        approval_required, approval_reason = _approval_requirement(user, selected, day_status, record)
        previous_weekly_off_date = _previous_weekly_off_within_three_days(user, selected)
        eligible_dates.append({
            "attendance_date": iso,
            "day": selected.strftime("%A"),
            "current_status": day_status,
            "can_select": reason is None,
            "reason": reason,
            "approval_required": approval_required,
            "approval_reason": approval_reason,
            "previous_weekly_off_date": previous_weekly_off_date.isoformat() if previous_weekly_off_date else None,
            "period": period,
        })
    return {
        "today": today.isoformat(),
        "last_date": end.isoformat(),
        "eligible_dates": eligible_dates,
        "monthly_entitlements": list(month_summaries.values()),
        "requests": _selection_records(user_id),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def create_weekly_off_selections(
    payload: WeeklyOffSelectionPayload,
    authorization: str | None = Header(default=None),
):
    user = attendance_request_logic._current_user(authorization)
    user_id = str(user["_id"])
    today = company_today()
    last_date = today + timedelta(days=FORWARD_DAYS)
    selected_dates = sorted(payload.attendance_dates)
    if len(set(selected_dates)) != len(selected_dates):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Each Weekly Off date can be selected only once.")
    if any(selected < today or selected > last_date for selected in selected_dates):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Weekly Off dates can be selected only from {today.isoformat()} through {last_date.isoformat()}.",
        )
    joining = employee_joining_date(user)
    if joining and any(selected < joining for selected in selected_dates):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A Weekly Off date cannot be before your joining date.")

    contexts = _month_data_for_dates(user, selected_dates)
    summaries = {period: _month_entitlement(user, ctx) for period, ctx in contexts.items()}
    grouped: dict[str, list[date]] = {}
    for selected in selected_dates:
        grouped.setdefault(selected.strftime("%Y-%m"), []).append(selected)
    for period, values in grouped.items():
        summary = summaries[period]
        if summary["payroll_locked"]:
            raise HTTPException(status.HTTP_409_CONFLICT, f"Payroll for {period} is {summary['payroll_status']}; this month is locked.")
        if summary["used"] + len(values) > summary["limit"]:
            available = max(0, summary["limit"] - summary["used"])
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Only {available} more Weekly Off date(s) can be selected for {period} (limit {summary['limit']}, already used or pending {summary['used']}).",
            )

    submissions: list[dict[str, Any]] = []
    for selected_index, selected in enumerate(selected_dates):
        iso = selected.isoformat()
        ctx = contexts[selected.strftime("%Y-%m")]
        record = ctx["records"].get(iso)
        current_status = attendance_request_logic._day_status(selected, record, ctx)
        if current_status == "Weekly Off":
            raise HTTPException(status.HTTP_409_CONFLICT, f"{iso} is already recorded as a Weekly Off.")
        earlier_submitted_dates = selected_dates[:selected_index]
        approval_required, approval_reason = _approval_requirement(
            user, selected, current_status, record, earlier_submitted_dates,
        )
        previous_weekly_off_date = _previous_weekly_off_within_three_days(
            user, selected, earlier_submitted_dates,
        )
        holiday_adjacent = bool(_holiday_proximity_reason(user, selected))
        attendance_conflict, _ = _needs_approval(current_status, record)
        approval_stage = "hr_admin" if holiday_adjacent or attendance_conflict or previous_weekly_off_date else "reporting_manager"
        approval_stage, approver = _resolve_weekly_off_approver(user, approval_stage)
        payroll_status = summaries[selected.strftime("%Y-%m")]["payroll_status"]
        if _existing_claim(user_id, iso):
            raise HTTPException(status.HTTP_409_CONFLICT, f"{iso} is already covered by another attendance or Weekly Off request.")
        original_attendance = {key: value for key, value in (record or {}).items() if key != "_id"}
        submissions.append({
            "selected": selected,
            "iso": iso,
            "period": selected.strftime("%Y-%m"),
            "original_status": current_status,
            "original_attendance": original_attendance if record else None,
            "approval_required": True,
            "approval_reason": approval_reason or "Reporting Manager approval is required.",
            "approval_stage": approval_stage,
            "approver_id": str(approver["_id"]),
            "approver_name": approver.get("name", ""),
            "payroll_status": payroll_status,
            "previous_weekly_off_date": previous_weekly_off_date.isoformat() if previous_weekly_off_date else None,
        })

    created: list[dict[str, Any]] = []
    claimed_request_ids: list[str] = []
    try:
        for item in submissions:
            request_id = ObjectId()
            attendance_request_logic.attendance_request_date_claims_collection.insert_one({
                "user_id": user_id,
                "attendance_date": item["iso"],
                "request_id": str(request_id),
                "created_at": datetime.now(timezone.utc),
            })
            claimed_request_ids.append(str(request_id))
            now = datetime.now(timezone.utc)
            request = {
                "_id": request_id,
                "request_type": "weekly_off_selection",
                "user_id": user_id,
                "employee_id": user.get("employee_id", ""),
                "employee_name": user.get("name", ""),
                "department": attendance_request_logic._department(user),
                "dates": [{
                    "attendance_date": item["iso"],
                    "requested_status": "Weekly Off",
                    "original_status": item["original_status"],
                    "original_attendance": item["original_attendance"],
                }],
                "attendance_dates": [item["iso"]],
                "periods": [item["period"]],
                "status": "pending",
                "approval_required": True,
                "approval_reason": item["approval_reason"],
                "approval_stage": item["approval_stage"],
                "approver_id": item["approver_id"],
                "approver_name": item["approver_name"],
                "previous_weekly_off_date": item["previous_weekly_off_date"],
                "payroll_status": item["payroll_status"],
                "submitted_at": now,
                "created_at": now,
                "updated_at": now,
            }
            request["request_number"] = attendance_request_logic._next_request_number()
            attendance_requests_collection.insert_one(request)
            created.append(request)
    except DuplicateKeyError as exc:
        for request_id in claimed_request_ids:
            attendance_request_logic._release_claims(request_id)
        for request in created:
            attendance_requests_collection.delete_one({"_id": request["_id"], "status": "pending"})
        raise HTTPException(status.HTTP_409_CONFLICT, "Another request already covers one of these dates. Refresh and try again.") from exc
    except Exception as exc:
        for request_id in claimed_request_ids:
            attendance_request_logic._release_claims(request_id)
        for request in created:
            attendance_requests_collection.delete_one({"_id": request["_id"], "status": "pending"})
        logger.exception("Unable to save Weekly Off selection for employee %s", user_id)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Weekly Off selections could not be saved.") from exc

    # Recount after reserving every date to avoid exceeding a month when two
    # employees' requests race for the same remaining entitlement.
    for period, values in grouped.items():
        ctx = contexts[period]
        used = int(attendance_request_logic._month_usage(user, ctx)["weekly_off_used"])
        if used > summaries[period]["limit"]:
            for request_id in claimed_request_ids:
                attendance_request_logic._release_claims(request_id)
            for request in created:
                attendance_requests_collection.delete_one({"_id": request["_id"], "status": "pending"})
            raise HTTPException(status.HTTP_409_CONFLICT, "The remaining monthly entitlement was used by another request. Refresh and try again.")

    pending_requests = list(attendance_requests_collection.find({
        "_id": {"$in": [request["_id"] for request in created]},
        "status": "pending",
    }))
    notification_results = [_notify_approver(request) for request in pending_requests]
    notification_sent = all(notification_results) if pending_requests else True
    return {
        "message": "Weekly Off dates submitted successfully. Requests are pending approval from the assigned Reporting Manager or HR Admin.",
        "requests": [_public_request(item) for item in attendance_requests_collection.find({"_id": {"$in": [r["_id"] for r in created]}})],
        "notification_sent": notification_sent,
    }


def _attendance_matches_selection(request: dict[str, Any], existing: dict[str, Any] | None) -> bool:
    item = (request.get("dates") or [{}])[0]
    original = item.get("original_attendance")
    if original is None:
        return existing is None
    if existing is None:
        return False
    current = {key: value for key, value in existing.items() if key != "_id"}
    return current == original


def _mark_payroll_outdated(request: dict[str, Any], reason: str) -> None:
    period = (request.get("periods") or [""])[0]
    payroll_status = attendance_request_logic._payroll_status(request.get("employee_id", ""), period)
    if payroll_status != "Calculated":
        return
    now = datetime.now(timezone.utc)
    payroll_collection.update_one(
        {"employee_id": request.get("employee_id", ""), "period": period, "status": "Calculated"},
        {"$set": {"attendance_outdated": True, "attendance_outdated_at": now, "attendance_outdated_reason": reason},
         "$addToSet": {"attendance_outdated_dates": (request.get("attendance_dates") or [""])[0]}},
    )


def _apply_weekly_off(request: dict[str, Any], actor: dict[str, Any], source: str) -> None:
    item = (request.get("dates") or [{}])[0]
    iso = item["attendance_date"]
    selected = date.fromisoformat(iso)
    employee = users_collection.find_one({"_id": ObjectId(request["user_id"])})
    if not employee:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The employee for this Weekly Off request no longer exists.")
    period = iso[:7]
    payroll_status = attendance_request_logic._payroll_status(request.get("employee_id", ""), period)
    lock_reason = attendance_request_logic._payroll_lock_reason(period, payroll_status)
    if lock_reason:
        raise HTTPException(status.HTTP_409_CONFLICT, lock_reason)
    key = {"user_id": request["user_id"], "attendance_date": iso}
    existing = attendance_collection.find_one(key)
    request_id = _request_id(request)
    if existing and existing.get("weekly_off_selection_id") == request_id:
        return
    if not _attendance_matches_selection(request, existing):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Attendance for {iso} changed after selection; refresh before applying it.")
    ctx = _month_context(employee, selected.year, selected.month)
    current_status = attendance_request_logic._day_status(selected, existing, ctx)
    original_status = item.get("original_status", "Not Marked")
    if current_status != original_status and not (original_status == "Upcoming" and current_status == "Not Marked" and not existing):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Attendance for {iso} is now {current_status}; the original record was preserved.")
    if source == "EMPLOYEE_WEEKLY_OFF":
        holiday_reason = _holiday_proximity_reason(employee, selected)
        if holiday_reason:
            raise HTTPException(status.HTTP_409_CONFLICT, holiday_reason)
    usage = attendance_request_logic._month_usage(employee, ctx)
    limit = monthly_weekly_off_entitlement(ctx.get("joining"), selected.year, selected.month)
    if usage["weekly_off_used"] > limit:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Weekly Off would exceed the monthly entitlement of {limit} for {period}.")

    now = datetime.now(timezone.utc)
    changes = {
        "status": "Weekly Off",
        "attendance_type": None,
        "attendance_source": source,
        "weekly_off_selection_id": request_id,
        "weekly_off_request_number": request.get("request_number"),
        "weekly_off_applied_at": now,
        "check_in_time": None,
        "check_out_time": None,
        "punch_details_missing": False,
        "marked_by": str(actor["_id"]),
        "updated_at": now,
    }
    clear_fields = {
        "first_half_status": "", "second_half_status": "",
        "first_half_check_in_time": "", "first_half_check_out_time": "",
        "second_half_check_in_time": "", "second_half_check_out_time": "",
        "first_punch": "", "last_punch": "", "check_in_at": "", "check_out_at": "",
        "last_essl_sync_at": "", "last_essl_sync_id": "",
        "wfh_auto_absent": "", "wfh_scheduled_end_time": "",
        "attendance_issue": "", "attendance_issue_original_status": "",
        "attendance_issue_reason": "", "attendance_issue_marked_at": "",
        "leave_request_id": "", "leave_day_part": "", "leave_type_code": "", "medical_leave": "",
    }
    if existing:
        result = attendance_collection.update_one(
            {"_id": existing["_id"], "status": existing.get("status"), "updated_at": existing.get("updated_at")},
            {"$set": changes, "$unset": clear_fields},
        )
        if not result.modified_count:
            raise HTTPException(status.HTTP_409_CONFLICT, f"Attendance for {iso} changed while Weekly Off was being applied.")
    else:
        try:
            attendance_collection.insert_one({
                "user_id": request["user_id"], "attendance_date": iso, "created_at": now, **changes,
            })
        except DuplicateKeyError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, f"Attendance for {iso} was created concurrently.") from exc
    _mark_payroll_outdated(request, f"Weekly Off selection {request.get('request_number')} changed attendance.")
    attendance_request_logic._audit(actor, "WEEKLY_OFF_APPLIED", request, {"attendance_date": iso, "source": source})


@router.get("/pending")
def pending_weekly_off_selections(authorization: str | None = Header(default=None)):
    approver = attendance_request_logic._current_user(authorization)
    approver_id = str(approver["_id"])
    is_hr_admin = str(approver.get("role") or "").strip().lower() in {"hr admin", "administrator"}
    assigned_conditions = [
        {"status": "pending", "approver_id": approver_id},
        {"status": "approved", "cancellation.status": "pending", "cancellation.approver_id": approver_id},
    ]
    if is_hr_admin:
        # Keep requests created before assigned-approver routing reviewable.
        assigned_conditions.extend([
            {"status": "pending", "approver_id": {"$exists": False}},
            {"status": "approved", "cancellation.status": "pending", "cancellation.approver_id": {"$exists": False}},
        ])
    records = attendance_requests_collection.find({
        "request_type": "weekly_off_selection",
        "user_id": {"$ne": approver_id},
        "$or": assigned_conditions,
    }).sort("submitted_at", 1).limit(500)
    return [_public_request(item) for item in records]


@router.put("/{request_id}/decision")
def decide_weekly_off_selection(
    request_id: str,
    payload: WeeklyOffDecisionPayload,
    authorization: str | None = Header(default=None),
):
    approver = attendance_request_logic._current_user(authorization)
    request = _load_weekly_off_request(request_id)
    if request.get("user_id") == str(approver["_id"]):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You cannot review your own Weekly Off request.")
    if request.get("status") != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only pending Weekly Off requests can be reviewed.")
    stage = request.get("approval_stage") or "hr_admin"
    assigned_approver_id = str(request.get("approver_id") or "")
    if assigned_approver_id and assigned_approver_id != str(approver["_id"]):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the assigned approver can approve or reject this Weekly Off request.")
    if not assigned_approver_id and str(approver.get("role") or "").strip().lower() not in {"hr admin", "administrator"}:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only HR Admin can review this legacy Weekly Off request.")
    if stage == "hr_admin":
        role = str(approver.get("role") or "").strip().lower()
        if role not in {"hr admin", "administrator"}:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only HR Admin can review this Weekly Off request.")
    claimed = attendance_requests_collection.find_one_and_update(
        {"_id": request["_id"], "status": "pending", "$or": [
            {"approver_id": str(approver["_id"])},
            {"approver_id": {"$exists": False}} if not assigned_approver_id else {"approver_id": str(approver["_id"])},
        ]},
        {"$set": {"status": "processing", "reviewed_by": str(approver["_id"]), "updated_at": datetime.now(timezone.utc)}},
    )
    if not claimed:
        raise HTTPException(status.HTTP_409_CONFLICT, "This Weekly Off request is already being reviewed.")
    request = claimed
    if payload.action == "reject":
        now = datetime.now(timezone.utc)
        updated = attendance_requests_collection.find_one_and_update(
            {"_id": request["_id"], "status": "processing"},
            {"$set": {"status": "rejected", "reviewed_by": str(approver["_id"]), "reviewed_by_name": approver.get("name", ""),
                      "reviewed_at": now, "review_comments": (payload.remarks or "").strip() or None, "updated_at": now}},
        )
        if not updated:
            raise HTTPException(status.HTTP_409_CONFLICT, "This Weekly Off request was already reviewed.")
        attendance_request_logic._release_claims(request_id)
        attendance_request_logic._audit(approver, "WEEKLY_OFF_REJECTED", request, {"attendance_date": request["attendance_dates"][0]})
        final_request = attendance_requests_collection.find_one({"_id": request["_id"]})
        _notify_employee_decision(final_request or updated, False, approver)
        return _public_request(final_request)
    try:
        _apply_weekly_off(request, approver, "APPROVED_WEEKLY_OFF_REQUEST")
    except Exception:
        attendance_requests_collection.update_one(
            {"_id": request["_id"], "status": "processing"},
            {"$set": {"status": "pending", "updated_at": datetime.now(timezone.utc)},
             "$unset": {"reviewed_by": ""}},
        )
        raise
    now = datetime.now(timezone.utc)
    updated = attendance_requests_collection.find_one_and_update(
        {"_id": request["_id"], "status": "processing"},
        {"$set": {"status": "approved", "reviewed_by": str(approver["_id"]), "reviewed_by_name": approver.get("name", ""),
                  "reviewed_at": now, "review_comments": (payload.remarks or "").strip() or None, "updated_at": now}},
    )
    if not updated:
        raise HTTPException(status.HTTP_409_CONFLICT, "This Weekly Off request was already reviewed.")
    attendance_request_logic._release_claims(request_id)
    attendance_request_logic._audit(approver, "WEEKLY_OFF_APPROVED", request, {"attendance_date": request["attendance_dates"][0]})
    final_request = attendance_requests_collection.find_one({"_id": request["_id"]})
    _notify_employee_decision(final_request or updated, True, approver)
    return _public_request(final_request)


def _restore_original_attendance(request: dict[str, Any], admin: dict[str, Any]) -> None:
    iso = (request.get("attendance_dates") or [""])[0]
    key = {"user_id": request["user_id"], "attendance_date": iso}
    existing = attendance_collection.find_one(key)
    if not existing or existing.get("status") != "Weekly Off" or existing.get("weekly_off_selection_id") != _request_id(request):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Attendance for {iso} changed after approval; it was not cancelled.")
    original = (request.get("dates") or [{}])[0].get("original_attendance")
    if original is None:
        attendance_collection.delete_one({"_id": existing["_id"], "weekly_off_selection_id": _request_id(request)})
    else:
        restored = {"_id": existing["_id"], **original}
        attendance_collection.replace_one(
            {"_id": existing["_id"], "weekly_off_selection_id": _request_id(request)},
            restored,
        )
    _mark_payroll_outdated(request, f"Weekly Off cancellation for {request.get('request_number')} changed attendance.")
    attendance_request_logic._audit(admin, "WEEKLY_OFF_CANCELLATION_APPLIED", request, {"attendance_date": iso})


@router.put("/{request_id}/cancellation-decision")
def decide_weekly_off_cancellation(
    request_id: str,
    payload: WeeklyOffDecisionPayload,
    authorization: str | None = Header(default=None),
):
    approver = attendance_request_logic._current_user(authorization)
    request = _load_weekly_off_request(request_id)
    cancellation = request.get("cancellation") or {}
    if request.get("user_id") == str(approver["_id"]):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You cannot review your own Weekly Off cancellation.")
    if request.get("status") != "approved" or cancellation.get("status") != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, "This Weekly Off cancellation is no longer pending.")
    cancellation_approver_id = str(cancellation.get("approver_id") or "")
    is_hr_admin = str(approver.get("role") or "").strip().lower() in {"hr admin", "administrator"}
    if cancellation_approver_id and cancellation_approver_id != str(approver["_id"]):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the person who originally approved this Weekly Off can decide its cancellation.")
    if not cancellation_approver_id and not is_hr_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only HR Admin can review this legacy Weekly Off cancellation.")
    if cancellation.get("approval_stage") == "hr_admin" and not is_hr_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only HR Admin can review this Weekly Off cancellation.")
    period = (request.get("periods") or [""])[0]
    payroll_status = attendance_request_logic._payroll_status(request.get("employee_id", ""), period)
    lock_reason = attendance_request_logic._payroll_lock_reason(period, payroll_status)
    if lock_reason:
        raise HTTPException(status.HTTP_409_CONFLICT, lock_reason)
    claimed = attendance_requests_collection.find_one_and_update(
        {"_id": request["_id"], "status": "approved", "cancellation.status": "pending",
         "$or": [{"cancellation.approver_id": str(approver["_id"])},
                 {"cancellation.approver_id": {"$exists": False}} if not cancellation_approver_id else {"cancellation.approver_id": str(approver["_id"])}]},
        {"$set": {"cancellation.status": "processing", "cancellation.reviewed_by": str(approver["_id"]),
                  "updated_at": datetime.now(timezone.utc)}},
    )
    if not claimed:
        raise HTTPException(status.HTTP_409_CONFLICT, "This Weekly Off cancellation is already being reviewed.")
    request = claimed
    now = datetime.now(timezone.utc)
    try:
        if payload.action == "approve":
            _restore_original_attendance(request, approver)
            update = {"status": "cancelled", "cancellation.status": "approved"}
            action = "WEEKLY_OFF_CANCELLATION_APPROVED"
        else:
            update = {"cancellation.status": "rejected"}
            action = "WEEKLY_OFF_CANCELLATION_REJECTED"
        result = attendance_requests_collection.update_one(
            {"_id": request["_id"], "status": "approved", "cancellation.status": "processing"},
            {"$set": {**update, "cancellation.reviewed_by": str(approver["_id"]),
                      "cancellation.reviewed_by_name": approver.get("name", ""), "cancellation.reviewed_at": now,
                      "cancellation.remarks": (payload.remarks or "").strip() or None, "updated_at": now}},
        )
        if not result.modified_count:
            raise HTTPException(status.HTTP_409_CONFLICT, "This Weekly Off cancellation changed while being processed.")
        if payload.action == "approve":
            attendance_request_logic._release_claims(request_id)
        attendance_request_logic._audit(approver, action, request, {"attendance_date": request["attendance_dates"][0]})
    except Exception:
        attendance_requests_collection.update_one(
            {"_id": request["_id"], "status": "approved", "cancellation.status": "processing"},
            {"$set": {"cancellation.status": "pending", "updated_at": datetime.now(timezone.utc)},
             "$unset": {"cancellation.reviewed_by": ""}},
        )
        raise
    final_request = attendance_requests_collection.find_one({"_id": request["_id"]})
    _notify_employee_cancellation_decision(final_request or request, payload.action == "approve", approver)
    return _public_request(final_request)


@router.delete("/my/{request_id}")
def cancel_my_weekly_off_selection(request_id: str, authorization: str | None = Header(default=None)):
    user = attendance_request_logic._current_user(authorization)
    request = _load_weekly_off_request(request_id)
    if request.get("user_id") != str(user["_id"]):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can cancel only your own Weekly Off selection.")
    if request.get("status") == "pending":
        now = datetime.now(timezone.utc)
        updated = attendance_requests_collection.find_one_and_update(
            {"_id": request["_id"], "user_id": str(user["_id"]), "status": "pending"},
            {"$set": {"status": "cancelled", "cancelled_at": now, "updated_at": now}},
        )
        if not updated:
            raise HTTPException(status.HTTP_409_CONFLICT, "This Weekly Off request was already processed.")
        attendance_request_logic._release_claims(request_id)
        attendance_request_logic._audit(user, "WEEKLY_OFF_REQUEST_CANCELLED", request, {"attendance_date": request["attendance_dates"][0]})
        return _public_request(attendance_requests_collection.find_one({"_id": request["_id"]}))
    if request.get("status") != "approved":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only pending or approved Weekly Off selections can be cancelled.")
    if (request.get("cancellation") or {}).get("status") in {"pending", "processing"}:
        raise HTTPException(status.HTTP_409_CONFLICT, "A cancellation request is already waiting for the original approver.")
    period = (request.get("periods") or [""])[0]
    payroll_status = attendance_request_logic._payroll_status(request.get("employee_id", ""), period)
    lock_reason = attendance_request_logic._payroll_lock_reason(period, payroll_status)
    if lock_reason:
        raise HTTPException(status.HTTP_409_CONFLICT, lock_reason)

    # The cancellation follows the authority that approved the original request.
    original_approver_id = "" if request.get("auto_applied") else str(request.get("reviewed_by") or request.get("approver_id") or "")
    original_stage = request.get("approval_stage") or "hr_admin"
    if not ObjectId.is_valid(original_approver_id):
        # Legacy automatically-applied records have no external approver. Route
        # them to HR Admin rather than allowing an employee to reverse approval.
        admin = users_collection.find_one({
            "is_active": True,
            "role": {"$regex": r"^\s*(hr admin|administrator)\s*$", "$options": "i"},
        }, {"_id": 1})
        if not admin:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No active original approver or HR Admin is available to review this cancellation.")
        original_approver_id = str(admin["_id"])
        original_stage = "hr_admin"
    approver = users_collection.find_one({"_id": ObjectId(original_approver_id), "is_active": True})
    if not approver:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The original approving authority is no longer active. Please ask HR Admin to resolve the approver assignment.")
    now = datetime.now(timezone.utc)
    cancellation_update = {
        "$set": {
            "cancellation": {
                "status": "pending", "requested_at": now,
                "requested_by": str(user["_id"]), "requested_by_name": user.get("name", ""),
                "approver_id": original_approver_id, "approval_stage": original_stage,
            },
            "updated_at": now,
        },
    }
    if request.get("cancellation"):
        cancellation_update["$push"] = {"cancellation_history": request["cancellation"]}
    updated = attendance_requests_collection.find_one_and_update(
        {"_id": request["_id"], "status": "approved", "cancellation.status": {"$nin": ["pending", "processing"]}},
        cancellation_update,
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        raise HTTPException(status.HTTP_409_CONFLICT, "A cancellation request is already waiting for the original approver.")
    attendance_request_logic._audit(user, "WEEKLY_OFF_CANCELLATION_REQUESTED", request, {
        "attendance_date": request["attendance_dates"][0], "original_approver_id": original_approver_id,
    })
    notification_sent = _notify_approver(updated, cancellation=True)
    return {**_public_request(updated), "notification_sent": notification_sent}
