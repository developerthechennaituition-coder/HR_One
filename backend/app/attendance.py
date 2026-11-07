"""
app/attendance.py

Attendance module + Leave integration.

Leave integration rules
-----------------------
1. When HR approves a leave, attendance is marked "Leave" ONLY for today
   (company time zone). Future leave dates are NOT written and NOT shown.

2. When a leave day arrives (or a past leave day is opened), the record is
   created automatically the first time that date is read (lazy sync).

3. Manual Present / Holiday records are never overwritten by lazy sync.

4. When a leave is cancelled, attendance records created by that leave
   are removed.

5. Approved FULL-DAY leave locks attendance until the leave is cancelled.

6. HR can explicitly save "Leave" from the Attendance page.
   The backend verifies the employee has an approved leave covering that
   date and stores the canonical leave information.

7. Leave information is always determined by MongoDB leave_requests.
   The frontend cannot create fake Leave attendance by sending arbitrary
   leave_request_id / leave_type_code values.

8. Future leave dates are never automatically created or returned.

9. leave.py imports helpers from this file.
   This file never imports leave.py to avoid circular imports.
"""

from __future__ import annotations

import base64
import logging
import math
import os
import re
import secrets
import hashlib
import smtplib
import calendar
from email.message import EmailMessage
from urllib.parse import quote, urlsplit
from datetime import date, datetime, time, timedelta, timezone
from io import BytesIO
from time import time as unix_time
from typing import Any, Literal
from zoneinfo import ZoneInfo

from bson import ObjectId
from fastapi import APIRouter, Header, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.utils import get_column_letter
from dotenv import load_dotenv
from pymongo import ReturnDocument
from pydantic import BaseModel, Field, field_validator
from pymongo.errors import DuplicateKeyError

from app.db.mongodb import db
from app.session import active_sessions
from app.working_details import applicable_schedule, format_hours_minutes
from app.weekly_off_planner import (
    MAX_WEEKLY_OFFS_PER_MONTH,
    rank_weekdays,
    suggest_weekly_off_dates,
    validate_weekly_off_selection,
)
from app.attendance_calculations import (
    calculate_half_day_portions,
    classify_half_day,
    combine_half_day_statuses,
    effective_working_minutes,
    attendance_issue,
    half_day_status_override,
    permission_minutes_outside_total_duration,
)
from app.attendance_export import build_multi_employee_attendance_workbook

load_dotenv()
from app.wfh_biometrics import (
    create_face_template,
    decode_frames,
    decrypt_face_photo,
    encrypt_face_photo,
    encrypt_face_template,
    verify_face_template,
)


router = APIRouter(
    prefix="/api/attendance",
    tags=["Attendance"],
)
logger = logging.getLogger(__name__)
WFH_MAX_LOCATION_ACCURACY_METERS = 100
WFH_MAX_ALLOWED_RADIUS_METERS = 5000
WFH_MAX_LOCATION_AGE_SECONDS = 120
WFH_MAX_LOCATION_FUTURE_SKEW_SECONDS = 30
WFH_LATE_CHECK_IN_REQUEST_TYPE = "wfh_late_check_in"
WFH_LATE_CHECK_IN_EXTENSION = timedelta(hours=2)
WFH_ATTENDANCE_ENABLED = os.getenv(
    "WFH_ATTENDANCE_ENABLED",
    "true",
).strip().lower() in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# MongoDB collections
# ---------------------------------------------------------------------------

users_collection = db["users"]
attendance_collection = db["attendance"]
attendance_remarks_collection = db["attendance_remarks"]
essl_raw_punches_collection = db["essl_raw_punches"]
audit_logs_collection = db["audit_logs"]
leave_requests_collection = db["leave_requests"]
leave_types_collection = db["leave_types"]
wfh_face_templates_collection = db["wfh_face_templates"]
wfh_liveness_challenges_collection = db["wfh_liveness_challenges"]
wfh_email_tokens_collection = db["wfh_email_verification_tokens"]
wfh_email_sessions_collection = db["wfh_email_verification_sessions"]
# Shared with the Leave module's permission workflow (approval queue/decisions).
permission_requests_collection = db["permission_requests"]


# ---------------------------------------------------------------------------
# Company settings
# ---------------------------------------------------------------------------

COMPANY_TZ = ZoneInfo(
    os.getenv("COMPANY_TIMEZONE", "Asia/Kolkata")
)

COMPANY_START_TIME = os.getenv(
    "COMPANY_START_TIME",
    "09:00",
)


def _schedule_fields_for_date(user_id: str, attendance_date: date) -> dict[str, Any]:
    """Return the employee schedule applicable on an attendance date.

    Keep this small adapter in the attendance module so all attendance API
    responses use the shared, effective-date-aware schedule implementation.
    """
    return applicable_schedule(str(user_id), attendance_date)


WEEKDAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def company_weekly_off_weekdays() -> set[int]:
    """Weekly-off weekdays (0=Monday) configured by HR Admin; none by default."""
    policy = db["leave_policies"].find_one(
        {"leave_type_id": "__company_policy__"},
        {"weekly_off_weekdays": 1},
    ) or {}
    return {
        WEEKDAY_NAMES.index(day)
        for day in policy.get("weekly_off_weekdays") or []
        if day in WEEKDAY_NAMES
    }


def company_now() -> datetime:
    """
    Current datetime in company timezone.
    """
    return datetime.now(COMPANY_TZ)


def company_today() -> date:
    """
    Current company-local date.
    """
    return company_now().date()


def employee_joining_date(user: dict[str, Any]) -> date | None:
    """Authoritative joining date: the user record, else employee details."""
    value = user.get("date_of_joining")
    if value is None and user.get("_id") is not None:
        details = db["employee_details"].find_one(
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


# Attendance calendars cover today and the following 89 days (a 90-day window).
CALENDAR_FORWARD_DAYS = 89


def validate_calendar_month(year: int, month: int, joining: date | None, today: date) -> tuple[date, date]:
    """Months from the joining month through the end of the 90-day window."""
    if month < 1 or month > 12:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid month.")
    window_end = today + timedelta(days=CALENDAR_FORWARD_DAYS)
    latest = max((window_end.year, window_end.month), (joining.year, joining.month) if joining else (0, 0))
    if year < 2000 or (year, month) > latest:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Months beyond the 90-day attendance calendar cannot be viewed.")
    # The current month always loads (pages open on it before they know the
    # joining date); its pre-joining dates are still marked Before Joining.
    earliest = min((joining.year, joining.month), (today.year, today.month)) if joining else None
    if earliest and (year, month) < earliest:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"The attendance calendar starts from the joining month ({joining.strftime('%B %Y')}).",
        )
    last_day = (date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1))
    return date(year, month, 1), last_day


def assert_on_or_after_joining(user: dict[str, Any], attendance_date: date) -> None:
    """Reject attendance writes dated before the employee's joining date."""
    joining = employee_joining_date(user)
    if joining and attendance_date < joining:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Attendance cannot be recorded for {attendance_date.isoformat()} because it is before "
            f"the employee's joining date ({joining.isoformat()}).",
        )


def _start_time() -> time:
    """
    Convert COMPANY_START_TIME into a time object.
    """
    try:
        hour, minute = COMPANY_START_TIME.split(":")
        return time(int(hour), int(minute))
    except (ValueError, TypeError):
        return time(9, 0)


def leave_cancel_deadline(from_date_iso: str) -> datetime:
    """
    Leave cancellation is allowed until company start time
    on the leave start date.
    """
    return datetime.combine(
        date.fromisoformat(from_date_iso),
        _start_time(),
        tzinfo=COMPANY_TZ,
    )


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class AttendanceCreate(BaseModel):
    user_id: str
    attendance_date: str
    status: str
    check_in_time: str | None = None
    check_out_time: str | None = None
    permission_start_time: str | None = None
    permission_end_time: str | None = None
    permission_reason: str | None = None
    modification_reason: str | None = None
    remove_permission: bool = False


class AttendanceResponse(BaseModel):
    id: str
    user_id: str
    employee_id: str
    name: str
    attendance_date: str
    status: str
    check_in_time: str | None = None
    check_out_time: str | None = None
    marked_by: str | None = None

    # Leave integration fields
    leave_request_id: str | None = None
    leave_day_part: str | None = None
    leave_type_code: str | None = None
    approved_permission: dict[str, Any] | None = None

    attendance_source: str | None = None
    attendance_type: str | None = None
    first_punch: datetime | None = None
    last_punch: datetime | None = None
    last_essl_sync_at: datetime | None = None
    first_check_in: str | None = None
    last_check_out: str | None = None
    attendance_state: str | None = None
    working_minutes: int | None = None
    working_duration: str | None = None
    total_duration_minutes: int | None = None
    actual_elapsed_minutes: int | None = None
    total_duration: str | None = None
    permission_total_minutes: int | None = None
    outside_minutes: int | None = None
    outside_duration: str | None = None
    visible_break_minutes: int | None = None
    break_duration: str | None = None
    working_intervals: list[dict[str, Any]] | None = None
    break_intervals: list[dict[str, Any]] | None = None
    half_day_present: str | None = None
    calculated_attendance_status: str | None = None
    half_day_present_side: str | None = None
    first_half_status: str | None = None
    second_half_status: str | None = None
    first_half_check_in_time: str | None = None
    first_half_check_out_time: str | None = None
    second_half_check_in_time: str | None = None
    second_half_check_out_time: str | None = None
    first_half_working_intervals: list[dict[str, Any]] | None = None
    second_half_working_intervals: list[dict[str, Any]] | None = None
    half_day_eligibility: str | None = None
    actual_working_minutes: int | None = None
    half_day_review_required: bool | None = None
    half_day_working_duration: int | None = None
    half_day_working_duration_text: str | None = None
    effective_working_minutes: int | None = None
    has_attendance_issue: bool | None = None
    attendance_issue_details: list[str] | None = None
    early_going_by: str | None = None
    late_going_by: str | None = None
    early_going_by_minutes: int | None = None
    late_going_by_minutes: int | None = None
    check_in_latitude: float | None = None
    check_in_longitude: float | None = None
    check_in_distance: float | None = None
    check_in_accuracy_meters: float | None = None
    check_in_location_timestamp: float | None = None
    check_out_latitude: float | None = None
    check_out_longitude: float | None = None
    check_out_distance: float | None = None
    check_out_accuracy_meters: float | None = None
    check_out_location_timestamp: float | None = None
    face_verification_status: str | None = None
    check_in_location_verified: bool | None = None
    check_in_face_verified: bool | None = None
    check_in_liveness_verified: bool | None = None
    check_in_approved_home_latitude: float | None = None
    check_in_approved_home_longitude: float | None = None
    check_in_approved_radius_meters: float | None = None
    check_in_approved_location_id: str | None = None
    check_in_approved_location_name: str | None = None
    check_in_verified_at: datetime | None = None
    check_out_location_verified: bool | None = None
    check_out_face_verified: bool | None = None
    check_out_liveness_verified: bool | None = None
    check_out_approved_home_latitude: float | None = None
    check_out_approved_home_longitude: float | None = None
    check_out_approved_radius_meters: float | None = None
    check_out_approved_location_id: str | None = None
    check_out_approved_location_name: str | None = None
    check_out_verified_at: datetime | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None


class WfhSettingsPayload(BaseModel):
    is_wfh_enabled: bool
    home_latitude: float | None = Field(default=None, ge=-90, le=90)
    home_longitude: float | None = Field(default=None, ge=-180, le=180)
    allowed_radius: float = Field(
        default=100,
        gt=0,
        le=WFH_MAX_ALLOWED_RADIUS_METERS,
    )
    home_location_accuracy_meters: float | None = Field(default=None, gt=0)
    home_location_timestamp: float | None = Field(default=None, gt=0)
    home_location_verification_method: Literal["browser", "manual"]
    home_location_manually_verified: bool = False
    wfh_permitted_from: str | None = None
    wfh_permitted_to: str | None = None
    wfh_permitted_start_time: str | None = None
    wfh_permitted_end_time: str | None = None

    @field_validator(
        "home_latitude",
        "home_longitude",
        "allowed_radius",
        "home_location_accuracy_meters",
        "home_location_timestamp",
    )
    @classmethod
    def validate_finite_setting(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("Location and radius values must be finite numbers.")
        return value


class WfhApprovedLocationPayload(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    allowed_radius: float = Field(default=100, gt=0, le=WFH_MAX_ALLOWED_RADIUS_METERS)
    verification_method: Literal["browser", "manual"] = "manual"
    accuracy_meters: float | None = Field(default=None, gt=0)
    location_timestamp: float | None = Field(default=None, gt=0)
    manually_verified: bool = False

    @field_validator("name")
    @classmethod
    def validate_approved_location_name(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Approved location name must contain at least two non-space characters.")
        return value

    @field_validator("latitude", "longitude", "allowed_radius", "accuracy_meters", "location_timestamp")
    @classmethod
    def validate_finite_approved_location(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("Approved location values must be finite numbers.")
        return value


class WfhLocationPayload(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracy_m: float = Field(gt=0)
    location_timestamp: float = Field(gt=0)

    @field_validator(
        "latitude",
        "longitude",
        "accuracy_m",
        "location_timestamp",
    )
    @classmethod
    def validate_finite_location(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("GPS coordinates and accuracy must be finite numbers.")
        return value


class WfhChallengePayload(BaseModel):
    purpose: Literal["register", "check_in", "check_out"]
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, gt=0)
    location_timestamp: float | None = Field(default=None, gt=0)


class WfhFacePayload(BaseModel):
    challenge_id: str = Field(min_length=20, max_length=100)
    frames: list[str] = Field(min_length=8, max_length=16)


class WfhLocationVerifyPayload(WfhLocationPayload):
    purpose: Literal["check_in", "check_out"]


class WfhAttendancePayload(WfhLocationPayload):
    challenge_id: str = Field(min_length=20, max_length=100)
    frames: list[str] = Field(min_length=8, max_length=16)


# ---------------------------------------------------------------------------
# Indexes
# ---------------------------------------------------------------------------

def ensure_attendance_indexes() -> None:
    """
    Call once from main.py startup.
    """

    attendance_collection.create_index(
        [
            ("user_id", 1),
            ("attendance_date", 1),
        ],
        unique=True,
    )

    attendance_collection.create_index(
        "leave_request_id"
    )
    db["attendance_modification_history"].create_index(
        [("user_id", 1), ("attendance_date", 1), ("modified_at", -1)],
        name="attendance_modification_employee_date_history",
    )
    attendance_remarks_collection.create_index(
        [("user_id", 1), ("attendance_date", 1)],
        unique=True,
    )
    wfh_face_templates_collection.create_index(
        "user_id",
        unique=True,
    )
    wfh_liveness_challenges_collection.create_index(
        "expires_at",
        expireAfterSeconds=0,
    )
    wfh_email_tokens_collection.create_index("token_hash", unique=True)
    wfh_email_tokens_collection.create_index("expires_at", expireAfterSeconds=0)
    wfh_email_tokens_collection.create_index([("user_id", 1), ("created_at", -1)])
    wfh_email_sessions_collection.create_index("token_hash", unique=True)
    wfh_email_sessions_collection.create_index("expires_at", expireAfterSeconds=0)
    # One late check-in request per employee per day, so repeated attempts
    # cannot create duplicate requests/notifications or repeated extensions.
    permission_requests_collection.create_index(
        [("user_id", 1), ("permission_date", 1)],
        unique=True,
        partialFilterExpression={"request_type": WFH_LATE_CHECK_IN_REQUEST_TYPE},
        name="uniq_wfh_late_check_in_per_day",
    )


def _is_automatic_attendance_record(record: dict[str, Any] | None) -> bool:
    """True when no explicit HR/manual decision is stored for the day.

    Automatic (ESSL / WFH) results that HR Admin has not edited, and days with no
    stored record at all, are always derived from the current half-day rules, so
    a stale earlier result (Absent / Half-Day Present) may be replaced. Manual,
    admin-edited and attendance-request records remain authoritative.
    """
    if not record:
        return True
    source = str(record.get("attendance_source") or "").upper()
    return source in {"ESSL", "WFH_FACE_GPS"} and not record.get("hr_admin_modified")


def _preserve_saved_half_day_status(record: dict[str, Any] | None) -> bool:
    """Preserve explicit HR/manual half-day decisions, not stale automatic results."""
    if not record:
        return False
    source = str(record.get("attendance_source") or "").upper()
    return bool(record.get("hr_admin_modified") or source in {"MANUAL", "ADMIN", "ATTENDANCE_REQUEST"})



# ---------------------------------------------------------------------------
# Authentication helpers
# ---------------------------------------------------------------------------

def _current_user(
    authorization: str | None,
) -> dict[str, Any]:
    """
    Get the currently authenticated active user.
    """

    if not isinstance(authorization, str) or not authorization:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Authentication required",
        )

    token = authorization.removeprefix("Bearer ").strip()

    user_id = active_sessions.get(token)

    if not user_id or not ObjectId.is_valid(user_id):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Authentication required",
        )

    user = users_collection.find_one(
        {
            "_id": ObjectId(user_id),
            "is_active": True,
        }
    )

    if not user:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Authentication required",
        )

    return user


def _require_hr_admin(
    authorization: str | None,
    message: str,
) -> dict[str, Any]:
    """
    Attendance management is restricted to HR Admin.
    """

    user = _current_user(authorization)

    role = str(
        user.get("role", "")
    ).strip().lower()

    if role not in {"hr admin", "administrator"}:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            message,
        )

    return user


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _validate_date(value: str) -> date:
    """
    Validate YYYY-MM-DD date.
    """

    try:
        return datetime.strptime(
            value,
            "%Y-%m-%d",
        ).date()

    except ValueError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Invalid attendance date. Use YYYY-MM-DD",
        )


def _validate_time(
    value: str,
    label: str,
) -> None:
    """
    Validate HH:MM time.
    """

    try:
        datetime.strptime(
            value,
            "%H:%M",
        )

    except ValueError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Invalid {label} time. Use HH:MM",
        )


# ---------------------------------------------------------------------------
# Leave integration
# ---------------------------------------------------------------------------

def _leave_covers(
    leave: dict[str, Any],
    date_iso: str,
) -> bool:
    """
    Determine whether a leave actually covers a particular date.

    New leave records contain leave_dates.
    Older leave records may not, so HR-configured weekly offs are excluded.
    """

    dates = leave.get("leave_dates")

    if dates is not None:
        return date_iso in dates

    return (
        date.fromisoformat(date_iso).weekday()
        not in company_weekly_off_weekdays()
    )


def _approved_leaves_for_date(
    date_iso: str,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    """
    Return approved leave requests covering the specified date.
    """

    query: dict[str, Any] = {
        "status": "approved",
        "from_date": {
            "$lte": date_iso,
        },
        "to_date": {
            "$gte": date_iso,
        },
    }

    if user_id:
        query["user_id"] = user_id

    leaves = list(
        leave_requests_collection.find(query)
    )

    return [
        leave
        for leave in leaves
        if _leave_covers(
            leave,
            date_iso,
        )
    ]


def _get_leave_type_code(
    leave: dict[str, Any],
) -> str | None:
    """
    Resolve the leave type code from leave_types collection.
    """

    leave_type_id = leave.get(
        "leave_type_id"
    )

    if not leave_type_id:
        return None

    if not ObjectId.is_valid(
        str(leave_type_id)
    ):
        return None

    leave_type = leave_types_collection.find_one(
        {
            "_id": ObjectId(
                str(leave_type_id)
            )
        }
    )

    if not leave_type:
        return None

    code = leave_type.get("code")

    if code is None:
        return None

    return str(code)


def _build_leave_attendance_fields(
    leave: dict[str, Any],
    updated_at: datetime,
) -> dict[str, Any]:
    """
    Build the canonical MongoDB attendance fields for Leave.

    The frontend does not control these values.
    """

    return {
        "status": "Leave",
        "check_in_time": None,
        "check_out_time": None,
        "leave_request_id": str(
            leave["_id"]
        ),
        "leave_day_part": leave.get(
            "day_part",
            "full_day",
        ),
        "leave_type_code": _get_leave_type_code(
            leave
        ),
        "updated_at": updated_at,
    }


def _apply_leave_to_date(
    leave: dict[str, Any],
    date_iso: str,
    override_absent: bool,
) -> None:
    """
    Write a Leave attendance record.

    Rules:
    - Present is never overwritten by lazy sync.
    - Holiday is never overwritten by lazy sync.
    - Absent can be replaced during leave approval.
    - Existing Leave is safely refreshed.
    """

    user_id = str(
        leave["user_id"]
    )

    now = datetime.now(timezone.utc)

    user = users_collection.find_one(
        {
            "_id": ObjectId(user_id),
        },
        {
            "employee_id": 1,
            "date_of_joining": 1,
        },
    ) or {}

    # Automated leave sync never creates attendance before the joining date.
    joining = employee_joining_date({"_id": ObjectId(user_id), **user})
    if joining and date_iso < joining.isoformat():
        return

    fields = _build_leave_attendance_fields(
        leave,
        now,
    )

    key = {
        "user_id": user_id,
        "attendance_date": date_iso,
    }

    # During approval, replace Absent or refresh existing Leave.
    if override_absent:
        attendance_collection.update_one(
            {
                **key,
                "status": {
                    "$in": [
                        "Absent",
                        "Leave",
                    ]
                },
            },
            {
                "$set": fields,
            },
        )

    try:
        attendance_collection.update_one(
            key,
            {
                "$setOnInsert": {
                    **fields,
                    "employee_id": user.get(
                        "employee_id",
                        "",
                    ),
                    "marked_by": "leave-system",
                    "created_at": now,
                }
            },
            upsert=True,
        )

    except DuplicateKeyError:
        # Another request created the same attendance
        # record at the same time.
        pass


def sync_leave_for_today(
    leave: dict[str, Any],
) -> bool:
    """
    Called by leave.py after approval.

    Only today's attendance is created.
    Future dates are never created.
    """

    today = company_today().isoformat()

    if (
        leave["from_date"]
        <= today
        <= leave["to_date"]
        and _leave_covers(
            leave,
            today,
        )
    ):
        _apply_leave_to_date(
            leave,
            today,
            override_absent=True,
        )

        return True

    return False


def remove_leave_attendance(
    request_id: str,
) -> None:
    """
    Called by leave.py after leave cancellation.

    Remove Leave attendance records created by that request.
    """

    attendance_collection.delete_many(
        {
            "leave_request_id": request_id,
            "status": "Leave",
        }
    )

    # For half-day leave where HR has already
    # changed attendance to Present, remove only
    # the stale leave metadata.
    attendance_collection.update_many(
        {
            "leave_request_id": request_id,
        },
        {
            "$unset": {
                "leave_request_id": "",
                "leave_day_part": "",
                "leave_type_code": "",
            }
        },
    )


def _sync_leave_for_date(
    date_iso: str,
) -> None:
    """
    Lazy-sync approved leave for one date.

    Future dates are ignored.
    """

    today_iso = company_today().isoformat()

    if date_iso > today_iso:
        return

    leaves = _approved_leaves_for_date(
        date_iso
    )

    for leave in leaves:
        _apply_leave_to_date(
            leave,
            date_iso,
            override_absent=False,
        )


def _sync_leave_for_user_range(
    user_id: str,
    start_iso: str,
    end_iso: str,
) -> None:
    """
    Lazy-sync approved leave for one user.

    Sync stops at today, never at a future date.
    """

    today_iso = company_today().isoformat()

    last = min(
        end_iso,
        today_iso,
    )

    if start_iso > last:
        return

    leaves = leave_requests_collection.find(
        {
            "user_id": user_id,
            "status": "approved",
            "from_date": {
                "$lte": last,
            },
            "to_date": {
                "$gte": start_iso,
            },
        }
    )

    for leave in leaves:

        current = max(
            date.fromisoformat(
                start_iso
            ),
            date.fromisoformat(
                leave["from_date"]
            ),
        )

        stop = min(
            date.fromisoformat(
                last
            ),
            date.fromisoformat(
                leave["to_date"]
            ),
        )

        while current <= stop:

            current_iso = current.isoformat()

            if _leave_covers(
                leave,
                current_iso,
            ):
                _apply_leave_to_date(
                    leave,
                    current_iso,
                    override_absent=False,
                )

            current += timedelta(days=1)


# ---------------------------------------------------------------------------
# Response builder
# ---------------------------------------------------------------------------

def _to_response(
    record: dict[str, Any],
    user: dict[str, Any],
) -> dict[str, Any]:
    """
    Convert MongoDB attendance record to API response.
    """

    return {
        "id": str(
            record["_id"]
        ),
        "user_id": str(
            user["_id"]
        ),
        "employee_id": user.get(
            "employee_id",
            "",
        ),
        "name": user.get(
            "name",
            "",
        ),
        "attendance_date": record.get(
            "attendance_date",
            "",
        ),
        "status": record.get(
            "status",
            "Absent",
        ),
        "check_in_time": _attendance_clock_hhmm(record.get("check_in_time")),
        "check_out_time": _attendance_clock_hhmm(record.get("check_out_time")),
        "marked_by": record.get(
            "marked_by"
        ),
        "leave_request_id": record.get(
            "leave_request_id"
        ),
        "leave_day_part": record.get(
            "leave_day_part"
        ),
        "leave_type_code": record.get(
            "leave_type_code"
        ),
        "approved_permission": ({
            **record["approved_permission"],
            "start_time": _attendance_clock_hhmm(record["approved_permission"].get("start_time")),
            "end_time": _attendance_clock_hhmm(record["approved_permission"].get("end_time")),
        } if isinstance(record.get("approved_permission"), dict) else record.get("approved_permission")),
        "attendance_source": record.get("attendance_source"),
        "attendance_type": record.get("attendance_type"),
        "first_punch": record.get("first_punch"),
        "last_punch": record.get("last_punch"),
        "last_essl_sync_at": record.get("last_essl_sync_at"),
        "first_half_status": record.get("first_half_status"),
        "second_half_status": record.get("second_half_status"),
        "first_half_check_in_time": _attendance_clock_hhmm(record.get("first_half_check_in_time")),
        "first_half_check_out_time": _attendance_clock_hhmm(record.get("first_half_check_out_time")),
        "second_half_check_in_time": _attendance_clock_hhmm(record.get("second_half_check_in_time")),
        "second_half_check_out_time": _attendance_clock_hhmm(record.get("second_half_check_out_time")),
        "first_half_working_intervals": record.get("first_half_working_intervals"),
        "second_half_working_intervals": record.get("second_half_working_intervals"),
        "half_day_present": record.get("half_day_present"),
        "half_day_present_side": record.get("half_day_present_side"),
        "half_day_eligibility": record.get("half_day_eligibility"),
        "half_day_working_duration": record.get("half_day_working_duration"),
        "actual_working_minutes": record.get("actual_working_minutes"),
        "check_in_latitude": record.get("check_in_latitude"),
        "check_in_longitude": record.get("check_in_longitude"),
        "check_in_distance": record.get("check_in_distance"),
        "check_in_accuracy_meters": record.get("check_in_accuracy_meters"),
        "check_in_location_timestamp": record.get("check_in_location_timestamp"),
        "check_out_latitude": record.get("check_out_latitude"),
        "check_out_longitude": record.get("check_out_longitude"),
        "check_out_distance": record.get("check_out_distance"),
        "check_out_accuracy_meters": record.get("check_out_accuracy_meters"),
        "check_out_location_timestamp": record.get("check_out_location_timestamp"),
        "face_verification_status": record.get("face_verification_status"),
        "check_in_location_verified": record.get("check_in_location_verified"),
        "check_in_face_verified": record.get("check_in_face_verified"),
        "check_in_liveness_verified": record.get("check_in_liveness_verified"),
        "check_in_approved_home_latitude": record.get(
            "check_in_approved_home_latitude"
        ),
        "check_in_approved_location_id": record.get("check_in_approved_location_id"),
        "check_in_approved_home_longitude": record.get(
            "check_in_approved_home_longitude"
        ),
        "check_in_approved_radius_meters": record.get(
            "check_in_approved_radius_meters"
        ),
        "check_in_approved_location_name": record.get("check_in_approved_location_name"),
        "check_in_verified_at": record.get("check_in_verified_at"),
        "check_out_location_verified": record.get("check_out_location_verified"),
        "check_out_face_verified": record.get("check_out_face_verified"),
        "check_out_liveness_verified": record.get("check_out_liveness_verified"),
        "check_out_approved_home_latitude": record.get(
            "check_out_approved_home_latitude"
        ),
        "check_out_approved_location_id": record.get("check_out_approved_location_id"),
        "check_out_approved_home_longitude": record.get(
            "check_out_approved_home_longitude"
        ),
        "check_out_approved_radius_meters": record.get(
            "check_out_approved_radius_meters"
        ),
        "check_out_approved_location_name": record.get("check_out_approved_location_name"),
        "check_out_verified_at": record.get("check_out_verified_at"),
        "check_in_at": record.get("check_in_at"),
        "check_out_at": record.get("check_out_at"),
        "wfh_breaks": record.get("wfh_breaks", []),
        "created_at": record.get(
            "created_at"
        ),
        "updated_at": record.get(
            "updated_at"
        ),
    }


# ---------------------------------------------------------------------------
# WFH attendance
# ---------------------------------------------------------------------------

def _require_wfh_admin(authorization: str | None) -> dict[str, Any]:
    return _require_hr_admin(
        authorization,
        "Only HR Admin or Administrator can configure WFH attendance.",
    )


def _bearer_token(authorization: str | None) -> str:
    if not isinstance(authorization, str) or not authorization:
        return ""
    return authorization.removeprefix("Bearer ").strip()


def _require_wfh_employee(
    authorization: str | None,
    require_wfh_enabled: bool = True,
) -> dict[str, Any]:
    token = _bearer_token(authorization)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest() if token else ""
    email_session = wfh_email_sessions_collection.find_one({"token_hash": token_hash}) if token else None
    if email_session:
        session_expiry = email_session.get("expires_at")
        if session_expiry and session_expiry.tzinfo is None:
            session_expiry = session_expiry.replace(tzinfo=timezone.utc)
        if not session_expiry or session_expiry <= datetime.now(timezone.utc):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This WFH verification session has expired. Request a new email link.")
        user_id = email_session.get("user_id")
        if not ObjectId.is_valid(user_id):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid WFH verification session.")
        user = users_collection.find_one({"_id": ObjectId(user_id), "is_active": True})
        if not user:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Employee account is not active.")
    else:
        user = _current_user(authorization)
    role = str(user.get("role", "")).strip().lower()
    if role not in {"employee", "hr admin", "hr_admin", "administrator", "admin", "hr manager", "manager"}:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "WFH attendance is not available for this account role.",
        )
    if require_wfh_enabled and user.get("is_wfh_enabled") is not True:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "WFH attendance is not enabled for your account.",
        )
    return user


def _assert_wfh_email_scope(authorization: str | None, purpose: str | None = None) -> None:
    token = _bearer_token(authorization)
    if not token:
        return
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    session = wfh_email_sessions_collection.find_one({"token_hash": token_hash})
    if not session:
        return  # Normal authenticated application session.
    session_expiry = session.get("expires_at")
    if session_expiry and session_expiry.tzinfo is None:
        session_expiry = session_expiry.replace(tzinfo=timezone.utc)
    if not session_expiry or session_expiry <= datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This WFH verification session has expired. Request a new email link.")
    if session.get("consumed"):
        raise HTTPException(status.HTTP_410_GONE, "This email verification link has already been used. Request a new link.")
    allowed_purpose = session.get("purpose")
    if purpose and allowed_purpose != purpose:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This email link is not authorized for that WFH action.")


def _consume_wfh_email_scope(authorization: str | None, purpose: str) -> None:
    token = _bearer_token(authorization)
    if not token:
        return
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    session = wfh_email_sessions_collection.find_one({"token_hash": token_hash})
    if session:
        if session.get("purpose") != purpose or session.get("consumed"):
            raise HTTPException(status.HTTP_410_GONE, "This WFH verification session is no longer valid.")
        result = wfh_email_sessions_collection.update_one(
            {"_id": session["_id"], "consumed": False},
            {"$set": {"consumed": True, "consumed_at": datetime.now(timezone.utc)}},
        )
        if result.modified_count != 1:
            raise HTTPException(status.HTTP_410_GONE, "This WFH verification session has already been used.")


def _send_wfh_verification_email(recipient: str, employee_name: str, link: str, purpose: str) -> None:
    host = os.getenv("SMTP_HOST", "").strip()
    port = int(os.getenv("SMTP_PORT", "465"))
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    sender = os.getenv("SMTP_FROM", "").strip() or username
    if not host or not sender or not username or not password:
        raise RuntimeError("WFH email verification is not configured. Check SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD and SMTP_FROM in backend .env.")

    action_label = {"register": "face registration", "check_in": "WFH check-in", "check_out": "WFH check-out"}[purpose]
    message = EmailMessage()
    message["Subject"] = f"HR One secure WFH {action_label} verification"
    message["From"] = sender
    message["To"] = recipient
    message.set_content(
        f"Hello {employee_name or 'Employee'},\n\n"
        f"You requested a secure fallback for {action_label}. Open this link on the device you want to use for face and live-location verification:\n\n"
        f"{link}\n\nThis link expires in 15 minutes and can be used only once for this action. It does not bypass face, liveness, location-accuracy or geofence checks.\n\n"
        "If you did not request this, ignore this email and contact HR.\n\nHR One"
    )
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=20) as smtp:
            smtp.login(username, password)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(username, password)
            smtp.send_message(message)


def _ensure_wfh_attendance_enabled() -> None:
    if WFH_ATTENDANCE_ENABLED:
        return
    raise HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "Work From Home attendance is temporarily unavailable.",
    )


def _calculate_distance_meters(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    earth_radius_m = 6_371_000
    lat_a = math.radians(latitude_a)
    lat_b = math.radians(latitude_b)
    lat_delta = math.radians(latitude_b - latitude_a)
    lon_delta = math.radians(longitude_b - longitude_a)
    haversine = (
        math.sin(lat_delta / 2) ** 2
        + math.cos(lat_a) * math.cos(lat_b) * math.sin(lon_delta / 2) ** 2
    )
    return earth_radius_m * 2 * math.asin(math.sqrt(min(1, haversine)))


def _configured_wfh_accuracy_limit() -> float:
    configured_limit = os.getenv(
        "WFH_MAX_LOCATION_ACCURACY_METERS",
        str(WFH_MAX_LOCATION_ACCURACY_METERS),
    )
    try:
        limit = float(configured_limit)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "WFH_MAX_LOCATION_ACCURACY_METERS must be a number greater than 0 and no more than 100."
        ) from exc
    if not math.isfinite(limit) or limit <= 0:
        raise RuntimeError(
            "WFH_MAX_LOCATION_ACCURACY_METERS must be a number greater than 0 and no more than 100."
        )
    return min(limit, WFH_MAX_LOCATION_ACCURACY_METERS)


def _configured_wfh_location_max_age_seconds() -> float:
    configured_age = os.getenv(
        "WFH_MAX_LOCATION_AGE_SECONDS",
        str(WFH_MAX_LOCATION_AGE_SECONDS),
    )
    try:
        max_age = float(configured_age)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "WFH_MAX_LOCATION_AGE_SECONDS must be a number greater than 0."
        ) from exc
    if not math.isfinite(max_age) or max_age <= 0:
        raise RuntimeError(
            "WFH_MAX_LOCATION_AGE_SECONDS must be a number greater than 0."
        )
    return max_age


def _validate_wfh_location_timestamp(location_timestamp: float) -> None:
    location_time_seconds = location_timestamp / 1000
    age_seconds = unix_time() - location_time_seconds
    if age_seconds < -WFH_MAX_LOCATION_FUTURE_SKEW_SECONDS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Your location information is invalid. Please try again.",
        )
    if age_seconds > _configured_wfh_location_max_age_seconds():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Your location information is outdated. Please try again.",
        )


def _validate_wfh_location(
    user: dict[str, Any],
    payload: WfhLocationPayload,
) -> tuple[float, float, str, float, float, str]:
    # Keep precise coordinates out of logs. They are personal location data.
    max_accuracy = _configured_wfh_accuracy_limit()
    if payload.accuracy_m > max_accuracy:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Your device reported location accuracy of ±{round(payload.accuracy_m)} meters, "
            f"which exceeds the allowed {round(max_accuracy)}-meter limit. "
            "Enable location services and browser location permission, then retry. "
            "If this computer cannot obtain a precise location, use an approved GPS-capable mobile device.",
        )
    _validate_wfh_location_timestamp(payload.location_timestamp)

    candidates: list[dict[str, Any]] = []
    home_latitude = user.get("home_latitude")
    home_longitude = user.get("home_longitude")
    home_radius = user.get("allowed_radius", 100)
    if home_latitude is not None and home_longitude is not None:
        candidates.append({
            "id": "home",
            "name": "Home",
            "latitude": home_latitude,
            "longitude": home_longitude,
            "radius": home_radius,
        })
    valid_candidates: list[tuple[float, float, str, float, float, str]] = []
    for candidate in candidates:
        lat, lon, radius = candidate["latitude"], candidate["longitude"], candidate["radius"]
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in (lat, lon, radius)):
            continue
        if not all(math.isfinite(float(value)) for value in (lat, lon, radius)):
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180 and 0 < radius <= WFH_MAX_ALLOWED_RADIUS_METERS):
            continue
        distance = _calculate_distance_meters(payload.latitude, payload.longitude, float(lat), float(lon))
        valid_candidates.append((distance, float(radius), str(candidate["name"]), float(lat), float(lon), str(candidate["id"])))

    if not valid_candidates:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "No valid approved WFH location is configured. Please contact HR Admin.",
        )

    valid_candidates.sort(key=lambda item: item[0])
    nearest_distance, nearest_radius, nearest_name, _, _, _ = valid_candidates[0]
    accepted = next((item for item in valid_candidates if item[0] <= item[1]), None)
    if accepted is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"You are {round(nearest_distance)} meters from the nearest approved location ({nearest_name}). "
            f"The permitted radius is {round(nearest_radius)} meters. Attendance is allowed only at an approved location.",
        )
    distance, radius, location_name, approved_latitude, approved_longitude, location_id = accepted
    return distance, radius, location_name, approved_latitude, approved_longitude, location_id


def _today_wfh_attendance(user_id: str) -> dict[str, Any] | None:
    return attendance_collection.find_one(
        {
            "user_id": user_id,
            "attendance_date": company_today().isoformat(),
        }
    )


def _wfh_check_in_deadlines(
    user: dict[str, Any],
    now_local: datetime,
) -> tuple[datetime, datetime, datetime]:
    """Return today's earliest check-in, normal deadline and late-permission deadline."""
    scheduled_start = time.fromisoformat(str(user["wfh_permitted_start_time"]))
    scheduled_start_dt = datetime.combine(now_local.date(), scheduled_start, tzinfo=now_local.tzinfo)
    earliest = scheduled_start_dt - timedelta(minutes=15)
    latest_normal = scheduled_start_dt + timedelta(hours=1)
    # An approved late permission extends the normal deadline (not the current
    # time) by two hours, but never past the employee's scheduled WFH end time.
    extended = latest_normal + WFH_LATE_CHECK_IN_EXTENSION
    end_time = user.get("wfh_permitted_end_time")
    if end_time:
        scheduled_end_dt = datetime.combine(now_local.date(), time.fromisoformat(str(end_time)), tzinfo=now_local.tzinfo)
        if latest_normal < scheduled_end_dt < extended:
            extended = scheduled_end_dt
    return earliest, latest_normal, extended


def _wfh_late_check_in_request(user_id: str, attendance_date: str) -> dict[str, Any] | None:
    return permission_requests_collection.find_one(
        {
            "user_id": user_id,
            "permission_date": attendance_date,
            "request_type": WFH_LATE_CHECK_IN_REQUEST_TYPE,
        }
    )


def _wfh_late_check_in_status(user: dict[str, Any]) -> dict[str, Any] | None:
    """Today's late check-in permission state for the employee WFH page."""
    if not user.get("wfh_permitted_start_time"):
        return None
    now_local = company_now()
    _, latest_normal, extended = _wfh_check_in_deadlines(user, now_local)
    request = _wfh_late_check_in_request(str(user["_id"]), now_local.date().isoformat())
    if request and request.get("end_time"):
        extended = datetime.combine(now_local.date(), time.fromisoformat(str(request["end_time"])), tzinfo=now_local.tzinfo)
    return {
        "attendance_date": now_local.date().isoformat(),
        "normal_deadline": latest_normal.strftime("%H:%M"),
        "extended_deadline": extended.strftime("%H:%M"),
        "is_late": now_local > latest_normal,
        "is_expired": now_local > extended,
        "request_id": str(request["_id"]) if request else None,
        "status": request.get("status") if request else None,
        "requested_at": request.get("created_at") if request else None,
        "decided_at": request.get("decided_at") if request else None,
        "decision_remarks": request.get("decision_remarks") if request else None,
    }


def _submit_wfh_late_check_in_request(
    user: dict[str, Any],
    now_local: datetime,
    earliest: datetime,
    latest_normal: datetime,
    extended: datetime,
) -> None:
    # Reuse the Leave module's permission workflow so the request appears in the
    # existing approval queue and decision endpoints. Imported lazily because
    # app.leave imports this module.
    from app.leave import _audit, _get_reporting_manager_id, _permission_notify

    user_id = str(user["_id"])
    manager_id = _get_reporting_manager_id(user)
    manager = (
        users_collection.find_one({"_id": ObjectId(manager_id), "is_active": True})
        if manager_id and ObjectId.is_valid(manager_id)
        else None
    )
    if not manager or str(manager["_id"]) == user_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "You are outside the normal WFH check-in window. Late check-in needs reporting-manager approval, "
            "but no active reporting manager is assigned to you. Please contact HR Admin.",
        )
    manager_id = str(manager["_id"])
    attendance_date = now_local.date().isoformat()
    attempted_at = now_local.strftime("%H:%M")
    window = f"{earliest.strftime('%H:%M')}–{latest_normal.strftime('%H:%M')}"
    extension_minutes = int((extended - latest_normal).total_seconds() // 60)
    extension = f"{extension_minutes // 60}h {extension_minutes % 60:02d}m"
    now = now_local.astimezone(timezone.utc)
    record = {
        "user_id": user_id,
        "reporting_manager_id": manager_id,
        "request_type": WFH_LATE_CHECK_IN_REQUEST_TYPE,
        "permission_date": attendance_date,
        "start_time": latest_normal.strftime("%H:%M"),
        "end_time": extended.strftime("%H:%M"),
        "reason": (
            f"WFH late check-in attempted at {attempted_at}. Normal check-in window: {window}. "
            f"Requested {extension} extension until {extended.strftime('%H:%M')}."
        ),
        "status": "pending",
        "check_in_attempted_at": now,
        "created_at": now,
        "updated_at": now,
    }
    try:
        result = permission_requests_collection.insert_one(record)
    except DuplicateKeyError:
        # A concurrent attempt already created (and notified) today's request.
        return
    request_id = str(result.inserted_id)

    employee_master = db["employees"].find_one({"employee_id": str(user.get("employee_id") or "")}) or {}
    employee_details = db["employee_details"].find_one({"user_id": user_id}) or {}
    department = user.get("department") or employee_master.get("department") or employee_details.get("department")
    employee_label = f"{user.get('name') or 'Employee'} ({user.get('employee_id') or 'No employee ID'})"
    message = (
        f"{employee_label}{f', {department}' if department else ''} attempted to check in after the permitted "
        f"WFH attendance window on {attendance_date}. Scheduled start: {user.get('wfh_permitted_start_time')}; "
        f"normal check-in window: {window}; attempted at: {attempted_at}. Requested permission: {extension} "
        f"extension, check-in allowed until {extended.strftime('%H:%M')} if approved. Please review the request "
        "in Leave > Permission > Permission Approval Queue and approve or reject the additional check-in permission."
    )
    try:
        _permission_notify(manager_id, "WFH Late Check-In Approval Required", message, request_id)
    except Exception as exc:
        # Do not leave a pending request the manager was never told about.
        logger.exception("Unable to notify reporting manager about WFH late check-in request %s", request_id)
        permission_requests_collection.delete_one({"_id": result.inserted_id, "status": "pending"})
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Your late check-in request could not be sent to your reporting manager. Please try again.",
        ) from exc
    _audit(user, "WFH_LATE_CHECK_IN_REQUESTED", request_id, {"permission_date": attendance_date, "reporting_manager_id": manager_id})


def _assert_wfh_late_check_in_permitted(
    user: dict[str, Any],
    now_local: datetime,
    earliest: datetime,
    latest_normal: datetime,
    extended: datetime,
) -> None:
    request = _wfh_late_check_in_request(str(user["_id"]), now_local.date().isoformat())
    request_status = request.get("status") if request else None
    if request_status == "approved":
        # Honour the deadline the manager approved for this employee and date.
        approved_until = datetime.combine(now_local.date(), time.fromisoformat(str(request["end_time"])), tzinfo=now_local.tzinfo)
        if now_local <= approved_until:
            return
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Your approved late WFH check-in permission for today expired at {request['end_time']}.")
    if request_status == "pending":
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Your late WFH check-in request for today is awaiting reporting-manager approval. Check-in will be available after approval, until {request.get('end_time')}.")
    if request:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your reporting manager rejected late WFH check-in permission for today. Check-in is not available outside the normal WFH check-in window.")
    if now_local > extended:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"The WFH check-in window closed at {latest_normal.strftime('%H:%M')}, and the late check-in permission period ended at {extended.strftime('%H:%M')}. Please contact HR Admin.")
    _submit_wfh_late_check_in_request(user, now_local, earliest, latest_normal, extended)
    raise HTTPException(status.HTTP_403_FORBIDDEN, f"You are outside the normal WFH check-in window. A reporting manager must approve late-time permission before check-in can proceed. Your request has been sent to your reporting manager; once approved, you can check in until {extended.strftime('%H:%M')}.")


def _assert_wfh_action_allowed(
    user: dict[str, Any],
    purpose: str,
) -> None:
    user_id = str(user["_id"])
    today = company_today()
    permitted_from = user.get("wfh_permitted_from")
    permitted_to = user.get("wfh_permitted_to")
    if not permitted_from or not permitted_to:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "WFH permission dates have not been configured. Please contact HR Admin.")
    if permitted_from and today < date.fromisoformat(str(permitted_from)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"WFH permission starts on {permitted_from}.")
    if permitted_to and today > date.fromisoformat(str(permitted_to)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"WFH permission ended on {permitted_to}.")
    start_time = user.get("wfh_permitted_start_time")
    end_time = user.get("wfh_permitted_end_time")
    if not start_time or not end_time:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "WFH start and end times have not been configured. Please contact HR Admin.")
    # Start/end times describe the daily schedule. Check-in gets a separate
    # grace window; check-out remains possible after the scheduled end time.
    late_check_in = False
    if purpose == "check_in":
        now_local = company_now()
        earliest, latest_normal, extended = _wfh_check_in_deadlines(user, now_local)
        if now_local < earliest:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"WFH check-in opens 15 minutes before your scheduled start time ({start_time}).")
        # Decided after the duplicate/leave checks below, so an approval request
        # is raised only for a genuine check-in attempt.
        late_check_in = now_local > latest_normal
    attendance = _today_wfh_attendance(user_id)
    if purpose == "check_in":
        if attendance:
            if attendance.get("attendance_source") == "WFH_FACE_GPS":
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "You have already checked in today.",
                )
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Attendance has already been marked for today. Please contact HR Admin.",
            )
        approved_leaves = _approved_leaves_for_date(
            company_today().isoformat(),
            user_id,
        )
        if any(leave.get("day_part", "full_day") == "full_day" for leave in approved_leaves):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "You are on approved full-day leave and cannot mark WFH attendance.",
            )
        if late_check_in:
            _assert_wfh_late_check_in_permitted(user, now_local, earliest, latest_normal, extended)
    elif purpose == "check_out":
        if (
            not attendance
            or attendance.get("attendance_source") != "WFH_FACE_GPS"
            or not attendance.get("check_in_time")
        ):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "A valid WFH Check-In is required before Check-Out.",
            )
        if attendance.get("check_out_time"):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "You have already checked out today.",
            )
        if any(
            isinstance(item, dict) and not item.get("ended_at")
            for item in (attendance.get("wfh_breaks") or [])
        ):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Please end your active break before checking out.",
            )


def _face_is_registered(user_id: str) -> bool:
    return (
        wfh_face_templates_collection.find_one(
            {"user_id": user_id},
            {"_id": 1},
        )
        is not None
    )


class WfhEmailFallbackRequest(BaseModel):
    purpose: Literal["register", "check_in", "check_out"]


class WfhEmailFallbackRedeem(BaseModel):
    token: str = Field(min_length=32, max_length=200)


@router.post("/wfh/email-fallback/request")
def request_wfh_email_fallback(
    payload: WfhEmailFallbackRequest,
    authorization: str | None = Header(default=None),
    origin: str | None = Header(default=None),
):
    token_value = _bearer_token(authorization)
    if token_value and wfh_email_sessions_collection.find_one({"token_hash": hashlib.sha256(token_value.encode("utf-8")).hexdigest()}):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Request a new fallback link from your signed-in HR One session.")
    user = _require_wfh_employee(authorization)
    _assert_wfh_email_scope(authorization, payload.purpose)
    user_id = str(user["_id"])
    if payload.purpose == "register" and _face_is_registered(user_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Your face is already registered. Contact HR Admin if it must be reset.")
    if payload.purpose != "register":
        _ensure_wfh_attendance_enabled()
        _assert_wfh_action_allowed(user, payload.purpose)
        if not _face_is_registered(user_id):
            raise HTTPException(status.HTTP_409_CONFLICT, "Register your face before requesting attendance verification.")

    email = str(user.get("work_email", "")).strip()
    if not email or "@" not in email:
        raise HTTPException(status.HTTP_409_CONFLICT, "Your registered work email is missing. Contact HR Admin.")
    now = datetime.now(timezone.utc)
    existing_link = wfh_email_tokens_collection.find_one({
        "user_id": user_id,
        "purpose": payload.purpose,
        "source": {"$ne": "qr"},
        "expires_at": {"$gt": now},
        "used_at": None,
    })
    if existing_link:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "A WFH verification link is already active. Check your work email or wait for it to expire before requesting another.")
    raw_token = secrets.token_urlsafe(40)
    expires_at = now + timedelta(minutes=15)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    wfh_email_tokens_collection.insert_one({
        "token_hash": token_hash,
        "user_id": user_id,
        "purpose": payload.purpose,
        "source": "email",
        "created_at": now,
        "expires_at": expires_at,
        "used_at": None,
    })
    frontend_url = os.getenv("FRONTEND_URL", "").strip().rstrip("/")
    request_origin = (origin or "").strip().rstrip("/")
    # If production has an old localhost FRONTEND_URL configured, prefer the
    # browser's origin so emailed links open the same reachable HRMS frontend.
    configured_host = urlsplit(frontend_url).hostname if frontend_url else None
    origin_parts = urlsplit(request_origin) if request_origin else None
    origin_is_valid = bool(
        origin_parts
        and origin_parts.scheme in {"http", "https"}
        and origin_parts.netloc
        and not origin_parts.username
        and not origin_parts.password
    )
    origin_is_local = origin_parts.hostname in {"localhost", "127.0.0.1"} if origin_is_valid else True
    if (not frontend_url or configured_host in {"localhost", "127.0.0.1"}) and origin_is_valid:
        if configured_host not in {"localhost", "127.0.0.1"} or not origin_is_local:
            frontend_url = request_origin
    if not frontend_url:
        frontend_url = "http://localhost:5173"
    link = f"{frontend_url}/wfh-email-verification?token={quote(raw_token)}"
    try:
        _send_wfh_verification_email(email, str(user.get("name", "Employee")), link, payload.purpose)
    except Exception as exc:
        wfh_email_tokens_collection.delete_one({"token_hash": token_hash})
        logger.exception("Unable to send WFH verification email")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Unable to send the WFH verification email. Check the SMTP configuration and try again.") from exc
    local_part, domain = email.split("@", 1)
    masked_local = (local_part[:1] + "***") if len(local_part) > 1 else "***"
    return {"sent": True, "masked_email": f"{masked_local}@{domain}", "expires_in_minutes": 15, "purpose": payload.purpose}


@router.post("/wfh/qr-fallback/request")
def request_wfh_qr_fallback(
    payload: WfhEmailFallbackRequest,
    authorization: str | None = Header(default=None),
):
    """Create a short-lived, one-time QR session without sending an email."""
    token_value = _bearer_token(authorization)
    if token_value and wfh_email_sessions_collection.find_one({
        "token_hash": hashlib.sha256(token_value.encode("utf-8")).hexdigest()
    }):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "QR verification can only be created from your signed-in HR One session.",
        )

    user = _require_wfh_employee(authorization)
    _assert_wfh_email_scope(authorization, payload.purpose)
    user_id = str(user["_id"])
    if payload.purpose == "register" and _face_is_registered(user_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Your face is already registered. Contact HR Admin if it must be reset.")
    if payload.purpose != "register":
        _ensure_wfh_attendance_enabled()
        _assert_wfh_action_allowed(user, payload.purpose)
        if not _face_is_registered(user_id):
            raise HTTPException(status.HTTP_409_CONFLICT, "Register your face before requesting attendance verification.")

    now = datetime.now(timezone.utc)
    # Invalidate any previous unredeemed QR for this same action so the
    # employee can regenerate a code after closing or losing the first one.
    wfh_email_tokens_collection.update_many(
        {
            "user_id": user_id,
            "purpose": payload.purpose,
            "source": "qr",
            "expires_at": {"$gt": now},
            "used_at": None,
        },
        {"$set": {"used_at": now, "invalidated_at": now}},
    )

    raw_token = secrets.token_urlsafe(40)
    expires_at = now + timedelta(minutes=5)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    wfh_email_tokens_collection.insert_one({
        "token_hash": token_hash,
        "user_id": user_id,
        "purpose": payload.purpose,
        "source": "qr",
        "created_at": now,
        "expires_at": expires_at,
        "used_at": None,
    })
    return {
        "token": raw_token,
        "purpose": payload.purpose,
        "expires_in_minutes": 5,
    }


@router.post("/wfh/email-fallback/redeem")
def redeem_wfh_email_fallback(payload: WfhEmailFallbackRedeem):
    token_hash = hashlib.sha256(payload.token.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)
    record = wfh_email_tokens_collection.find_one_and_update(
        {"token_hash": token_hash, "expires_at": {"$gt": now}, "used_at": None},
        {"$set": {"used_at": now}},
        return_document=ReturnDocument.BEFORE,
    )
    if not record:
        raise HTTPException(status.HTTP_410_GONE, "This WFH email link is invalid, expired or already used. Request a new link from HR One.")
    user_id = record.get("user_id")
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid employee verification link.")
    user = users_collection.find_one({"_id": ObjectId(user_id), "is_active": True})
    if not user or user.get("is_wfh_enabled") is not True:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "WFH access is not enabled for this employee.")
    scoped_token = secrets.token_urlsafe(40)
    scoped_expires_at = now + timedelta(minutes=15)
    wfh_email_sessions_collection.insert_one({
        "token_hash": hashlib.sha256(scoped_token.encode("utf-8")).hexdigest(),
        "user_id": user_id,
        "purpose": record.get("purpose"),
        "source": record.get("source", "email"),
        "created_at": now,
        "expires_at": scoped_expires_at,
        "consumed": False,
    })
    return {"access_token": scoped_token, "purpose": record.get("purpose"), "source": record.get("source", "email"), "expires_in_minutes": 15}


@router.get("/wfh/me")
def get_my_wfh_settings(
    authorization: str | None = Header(default=None),
    purpose: Literal["check_in", "check_out"] | None = Query(default=None),
):
    # Authentication and role are still enforced. Only the employee-level
    # enablement flag is relaxed so the page can show an explicit "not enabled"
    # state instead of a generic error. No WFH details are exposed in that case.
    user = _require_wfh_employee(authorization, require_wfh_enabled=False)
    if user.get("is_wfh_enabled") is not True:
        return {
            "is_wfh_enabled": False,
            "wfh_company_enabled": bool(WFH_ATTENDANCE_ENABLED),
        }
    if purpose is not None:
        _assert_wfh_email_scope(authorization, purpose)
        _assert_wfh_action_allowed(user, purpose)
    else:
        _assert_wfh_email_scope(authorization)
    user_id = str(user["_id"])
    record = _today_wfh_attendance(user_id)
    return {
        "is_wfh_enabled": user.get("is_wfh_enabled") is True,
        "wfh_company_enabled": bool(WFH_ATTENDANCE_ENABLED),
        "wfh_permitted_from": user.get("wfh_permitted_from"),
        "wfh_permitted_to": user.get("wfh_permitted_to"),
        "wfh_permitted_start_time": user.get("wfh_permitted_start_time"),
        "wfh_permitted_end_time": user.get("wfh_permitted_end_time"),
        "home_location_configured": (
            user.get("home_latitude") is not None
            and user.get("home_longitude") is not None
        ),
        "location_registration_required": bool(user.get("wfh_location_registration_required", False)) or user.get("home_latitude") is None or user.get("home_longitude") is None,
        "home_location_accuracy_meters": user.get("home_location_accuracy_meters"),
        "home_location_updated_at": user.get("home_location_updated_at"),
        "allowed_radius": user.get("allowed_radius", 100),
        "approved_locations": [],
        "max_location_accuracy_meters": _configured_wfh_accuracy_limit(),
        "max_location_age_seconds": _configured_wfh_location_max_age_seconds(),
        "face_registered": _face_is_registered(user_id),
        "attendance": (
            _to_response(record, user)
            if record and record.get("attendance_source") == "WFH_FACE_GPS"
            else None
        ),
        "attendance_already_marked": record is not None,
        "wfh_breaks": (record or {}).get("wfh_breaks", []),
        "late_check_in_permission": _wfh_late_check_in_status(user),
    }


@router.post("/wfh/break/start")
def start_wfh_break(
    authorization: str | None = Header(default=None),
):
    """Start a break for the authenticated employee's active WFH attendance."""
    _ensure_wfh_attendance_enabled()
    user = _require_wfh_employee(authorization)
    _assert_wfh_email_scope(authorization)
    user_id = str(user["_id"])
    date_iso = company_today().isoformat()
    now_local = company_now()
    now_utc = datetime.now(timezone.utc)
    result = attendance_collection.update_one(
        {
            "user_id": user_id,
            "attendance_date": date_iso,
            "attendance_source": "WFH_FACE_GPS",
            "check_in_time": {"$ne": None},
            "check_out_time": None,
            "wfh_breaks": {"$not": {"$elemMatch": {"ended_at": None}}},
        },
        {
            "$push": {
                "wfh_breaks": {
                    "started_at": now_utc,
                    "ended_at": None,
                    "start_time": now_local.strftime("%H:%M:%S"),
                    "end_time": None,
                    "duration_minutes": None,
                }
            },
            "$set": {"updated_at": now_utc},
        },
    )
    if result.modified_count != 1:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "A checked-in WFH attendance is required, and no other break may be active.",
        )
    record = attendance_collection.find_one({"user_id": user_id, "attendance_date": date_iso})
    return {"attendance": _to_response(record, user), "wfh_breaks": record.get("wfh_breaks", [])}


@router.post("/wfh/break/end")
def end_wfh_break(
    authorization: str | None = Header(default=None),
):
    """End the employee's currently active WFH break."""
    _ensure_wfh_attendance_enabled()
    user = _require_wfh_employee(authorization)
    _assert_wfh_email_scope(authorization)
    user_id = str(user["_id"])
    date_iso = company_today().isoformat()
    now_local = company_now()
    now_utc = datetime.now(timezone.utc)
    record = attendance_collection.find_one({
        "user_id": user_id,
        "attendance_date": date_iso,
        "attendance_source": "WFH_FACE_GPS",
        "check_out_time": None,
        "wfh_breaks": {"$elemMatch": {"ended_at": None}},
    })
    if not record:
        raise HTTPException(status.HTTP_409_CONFLICT, "There is no active WFH break to end.")
    active_break = next(
        (item for item in reversed(record.get("wfh_breaks", []))
         if isinstance(item, dict) and not item.get("ended_at")),
        None,
    )
    if not active_break or not isinstance(active_break.get("started_at"), datetime):
        raise HTTPException(status.HTTP_409_CONFLICT, "The active WFH break could not be validated.")
    started_at = active_break["started_at"]
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    duration_minutes = max(0, int((now_utc - started_at).total_seconds() // 60))
    result = attendance_collection.update_one(
        {
            "_id": record["_id"],
            "wfh_breaks": {"$elemMatch": {"ended_at": None, "started_at": active_break["started_at"]}},
        },
        {
            "$set": {
                "wfh_breaks.$[active].ended_at": now_utc,
                "wfh_breaks.$[active].end_time": now_local.strftime("%H:%M:%S"),
                "wfh_breaks.$[active].duration_minutes": duration_minutes,
                "updated_at": now_utc,
            }
        },
        array_filters=[{"active.ended_at": None, "active.started_at": active_break["started_at"]}],
    )
    if result.modified_count != 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "The WFH break was already ended. Refresh and try again.")
    updated = attendance_collection.find_one({"_id": record["_id"]})
    return {"attendance": _to_response(updated, user), "wfh_breaks": updated.get("wfh_breaks", [])}


@router.get("/wfh/admin/employees")
def list_wfh_employee_settings(
    authorization: str | None = Header(default=None),
):
    _require_wfh_admin(authorization)
    employees = users_collection.find(
        {"role": {"$regex": "^(employee|hr admin|hr_admin|administrator|admin|hr manager|manager)$", "$options": "i"}}
    ).sort("name", 1)
    return [
        {
            "user_id": str(employee["_id"]),
            "employee_id": employee.get("employee_id", ""),
            "name": employee.get("name", ""),
            "is_active": bool(employee.get("is_active", False)),
            "role": employee.get("role", "Employee"),
            "is_wfh_enabled": employee.get("is_wfh_enabled") is True,
            "wfh_permitted_from": employee.get("wfh_permitted_from"),
            "wfh_permitted_to": employee.get("wfh_permitted_to"),
            "wfh_permitted_start_time": employee.get("wfh_permitted_start_time"),
            "wfh_permitted_end_time": employee.get("wfh_permitted_end_time"),
            "home_latitude": employee.get("home_latitude"),
            "home_longitude": employee.get("home_longitude"),
            "allowed_radius": employee.get("allowed_radius", 100),
            "approved_locations": [
                {
                    "id": item.get("id"),
                    "name": item.get("name", "Approved location"),
                    "latitude": item.get("latitude"),
                    "longitude": item.get("longitude"),
                    "allowed_radius": item.get("allowed_radius", 100),
                    "active": item.get("active", True),
                    "verification_method": item.get("verification_method", "manual"),
                }
                for item in (employee.get("wfh_approved_locations") or [])
                if isinstance(item, dict)
            ],
            "location_registration_required": bool(employee.get("wfh_location_registration_required", False)) or employee.get("home_latitude") is None or employee.get("home_longitude") is None,
            "home_location_accuracy_meters": employee.get(
                "home_location_accuracy_meters"
            ),
            "home_location_timestamp": employee.get("home_location_timestamp"),
            "home_location_updated_by": employee.get("home_location_updated_by"),
            "home_location_updated_at": employee.get("home_location_updated_at"),
            "home_location_verification_method": employee.get("home_location_verification_method"),
            "home_location_manually_verified": employee.get("home_location_manually_verified", False),
            "home_location_capture": (
                {
                    "accuracy": employee.get("home_location_accuracy_meters"),
                    "timestamp": employee.get("home_location_timestamp"),
                }
                if employee.get("home_location_accuracy_meters") is not None
                and employee.get("home_location_timestamp") is not None
                else None
            ),
            "face_registered": _face_is_registered(str(employee["_id"])),
        }
        for employee in employees
    ]


@router.put("/wfh/admin/employees/{user_id}")
def update_wfh_employee_settings(
    user_id: str,
    payload: WfhSettingsPayload,
    authorization: str | None = Header(default=None),
):
    admin = _require_wfh_admin(authorization)
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid employee ID.")
    employee = users_collection.find_one(
        {
            "_id": ObjectId(user_id),
            "role": {"$regex": "^(employee|hr admin|hr_admin|administrator|admin|hr manager|manager)$", "$options": "i"},
        }
    )
    if not employee:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Employee not found.")
    has_home_latitude = payload.home_latitude is not None
    has_home_longitude = payload.home_longitude is not None
    if has_home_latitude != has_home_longitude:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Home latitude and longitude must either both be set or both be empty.",
        )
    has_home_location = has_home_latitude and has_home_longitude
    has_approved_location = any(
        isinstance(item, dict)
        and item.get("active", True) is True
        and all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            for value in (
                item.get("latitude"),
                item.get("longitude"),
                item.get("allowed_radius", 100),
            )
        )
        and -90 <= item.get("latitude", 999) <= 90
        and -180 <= item.get("longitude", 999) <= 180
        and 0 < item.get("allowed_radius", 100) <= WFH_MAX_ALLOWED_RADIUS_METERS
        for item in (employee.get("wfh_approved_locations") or [])
    )

    if (
        payload.home_location_accuracy_meters is not None
        and payload.home_location_accuracy_meters
        > _configured_wfh_accuracy_limit()
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "The captured location is not accurate enough to save. Please retry or manually verify and correct the approved coordinates.",
        )
    if (payload.home_location_accuracy_meters is None) != (
        payload.home_location_timestamp is None
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Captured location accuracy and timestamp must be provided together.",
        )
    if has_home_location:
        if payload.home_location_verification_method == "browser":
            if payload.home_location_accuracy_meters is None or payload.home_location_timestamp is None:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    "Browser-captured coordinates must include measured accuracy and capture time.",
                )
            if payload.home_location_manually_verified:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    "Browser-captured coordinates cannot be marked as manually verified.",
                )
        else:
            if not payload.home_location_manually_verified:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    "Confirm that the manually entered coordinates were checked against a trusted map.",
                )
            if payload.home_location_accuracy_meters is not None or payload.home_location_timestamp is not None:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    "Manual coordinates must not be assigned fabricated browser accuracy or capture timestamps.",
                )
    elif payload.home_location_accuracy_meters is not None or payload.home_location_timestamp is not None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Location accuracy and capture time cannot be saved without home coordinates.",
        )
    today = company_today()
    if payload.is_wfh_enabled and (not payload.wfh_permitted_from or not payload.wfh_permitted_to):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Select both WFH permitted from and until dates before enabling WFH.")
    if payload.is_wfh_enabled and (not payload.wfh_permitted_start_time or not payload.wfh_permitted_end_time):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "WFH start time and end time are mandatory.")
    for label, value in (("start", payload.wfh_permitted_from), ("end", payload.wfh_permitted_to)):
        if value:
            try:
                parsed = date.fromisoformat(value)
            except ValueError as exc:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"WFH {label} date must use YYYY-MM-DD format.") from exc
            if parsed < today:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "WFH permission dates must be today or in the future.")
    if payload.wfh_permitted_from and payload.wfh_permitted_to and payload.wfh_permitted_to < payload.wfh_permitted_from:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "WFH end date must be on or after the start date.")
    if payload.wfh_permitted_start_time and payload.wfh_permitted_end_time and payload.wfh_permitted_end_time <= payload.wfh_permitted_start_time:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "WFH end time must be later than start time.")
    if os.getenv("HRMS_ENV", "development").lower() == "development":
        logger.info("WFH admin settings updated for employee %s", user_id)
    now = datetime.now(timezone.utc)
    users_collection.update_one(
        {"_id": employee["_id"]},
        {
            "$set": {
                "is_wfh_enabled": payload.is_wfh_enabled,
                "wfh_permitted_from": payload.wfh_permitted_from,
                "wfh_permitted_to": payload.wfh_permitted_to,
                "wfh_permitted_start_time": payload.wfh_permitted_start_time,
                "wfh_permitted_end_time": payload.wfh_permitted_end_time,
                "allowed_radius": payload.allowed_radius,
                "updated_at": now,
            }
        },
    )
    if payload.is_wfh_enabled:
        try:
            # Reuse the existing general notification infrastructure so the
            # employee receives the standard read/star/delete controls.
            from app.notifications import create_system_notification
            employee_name = str(employee.get("name") or employee.get("employee_id") or "Employee").strip()
            start_label = datetime.strptime(payload.wfh_permitted_start_time, "%H:%M").strftime("%I:%M %p")
            end_label = datetime.strptime(payload.wfh_permitted_end_time, "%H:%M").strftime("%I:%M %p")
            create_system_notification(
                title="Work From Home Approved",
                message=(f"Hello! {employee_name}, your work from home is accepted from "
                         f"{payload.wfh_permitted_from} to {payload.wfh_permitted_to} and from "
                         f"{start_label} to {end_label}."),
                validity=timedelta(days=365),
                event_key=f"wfh-schedule:{user_id}:{payload.wfh_permitted_from}:{payload.wfh_permitted_to}:{payload.wfh_permitted_start_time}:{payload.wfh_permitted_end_time}:{now.isoformat()}",
                user_ids=[user_id],
            )
        except Exception:
            # Settings are already saved; log notification failure without
            # falsely reporting that the schedule itself failed.
            logger.exception("WFH settings saved but employee notification could not be created for %s", user_id)
    return {
        "user_id": user_id,
        "is_wfh_enabled": payload.is_wfh_enabled,
        "wfh_permitted_from": payload.wfh_permitted_from,
        "wfh_permitted_to": payload.wfh_permitted_to,
        "wfh_permitted_start_time": payload.wfh_permitted_start_time,
        "wfh_permitted_end_time": payload.wfh_permitted_end_time,
        "home_latitude": employee.get("home_latitude"),
        "home_longitude": employee.get("home_longitude"),
        "allowed_radius": payload.allowed_radius,
        "home_location_accuracy_meters": employee.get("home_location_accuracy_meters"),
        "home_location_timestamp": employee.get("home_location_timestamp"),
        "home_location_verification_method": employee.get("home_location_verification_method"),
        "home_location_manually_verified": employee.get("home_location_manually_verified", False),
        "home_location_updated_by": employee.get("home_location_updated_by"),
        "home_location_updated_at": employee.get("home_location_updated_at"),
        "home_location_capture": (
            {
                "accuracy": payload.home_location_accuracy_meters,
                "timestamp": payload.home_location_timestamp,
            }
            if payload.home_location_accuracy_meters is not None
            and payload.home_location_timestamp is not None
            else None
        ),
        "face_registered": _face_is_registered(user_id),
    }


@router.post("/wfh/location/register")
def register_my_wfh_location(
    payload: WfhLocationPayload,
    authorization: str | None = Header(default=None),
):
    """Register the authenticated user's current home/WFH location from their own device."""
    user = _require_wfh_employee(authorization)
    if user.get("is_wfh_enabled") is not True:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "WFH is not enabled for your account.")
    if user.get("home_latitude") is not None and user.get("home_longitude") is not None and not user.get("wfh_location_registration_required", False):
        raise HTTPException(status.HTTP_409_CONFLICT, "Your WFH location is already registered. Contact HR Admin to reset it before registering a new location.")
    _validate_wfh_location_timestamp(payload.location_timestamp)
    if payload.accuracy_m > _configured_wfh_accuracy_limit():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Location accuracy must be within ±{round(_configured_wfh_accuracy_limit())} metres. Please retry from your phone with location services enabled.")
    now = datetime.now(timezone.utc)
    users_collection.update_one(
        {"_id": user["_id"]},
        {"$set": {
            "home_latitude": payload.latitude,
            "home_longitude": payload.longitude,
            "home_location_accuracy_meters": payload.accuracy_m,
            "home_location_timestamp": payload.location_timestamp,
            "home_location_verification_method": "browser",
            "home_location_manually_verified": False,
            "home_location_updated_at": now,
            "home_location_updated_by": str(user["_id"]),
            "wfh_location_registration_required": False,
            "updated_at": now,
        }},
    )
    return {"location_registered": True, "latitude": payload.latitude, "longitude": payload.longitude, "accuracy_meters": payload.accuracy_m, "registered_at": now.isoformat()}


@router.delete("/wfh/admin/employees/{user_id}/location")
def reset_wfh_employee_location(
    user_id: str,
    authorization: str | None = Header(default=None),
):
    _require_wfh_admin(authorization)
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid employee ID.")
    result = users_collection.update_one(
        {"_id": ObjectId(user_id), "role": {"$regex": "^(employee|hr admin|hr_admin|administrator|admin|hr manager|manager)$", "$options": "i"}},
        {"$unset": {"home_latitude": "", "home_longitude": "", "home_location_accuracy_meters": "", "home_location_timestamp": "", "home_location_verification_method": "", "home_location_manually_verified": "", "home_location_updated_at": "", "home_location_updated_by": "", "wfh_approved_locations": ""}, "$set": {"wfh_location_registration_required": True, "updated_at": datetime.now(timezone.utc)}},
    )
    if result.matched_count != 1:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found.")
    return {"location_registered": False, "location_registration_required": True}


@router.post("/wfh/admin/employees/{user_id}/approved-locations")
def add_wfh_approved_location(
    user_id: str,
    payload: WfhApprovedLocationPayload,
    authorization: str | None = Header(default=None),
):
    admin = _require_wfh_admin(authorization)
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid employee ID.")
    employee = users_collection.find_one({"_id": ObjectId(user_id), "role": {"$regex": "^employee$", "$options": "i"}})
    if not employee:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Employee not found.")
    if payload.verification_method == "browser":
        if payload.accuracy_meters is None or payload.location_timestamp is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Browser-captured coordinates must include accuracy and timestamp.")
        if payload.accuracy_meters > _configured_wfh_accuracy_limit():
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Location accuracy is too low to approve this place.")
        _validate_wfh_location_timestamp(payload.location_timestamp)
        if payload.manually_verified:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Browser-captured locations cannot be marked as manually verified.")
    else:
        if not payload.manually_verified:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Confirm that these coordinates were checked against a trusted map or physically verified.")
        if payload.accuracy_meters is not None or payload.location_timestamp is not None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Do not assign browser accuracy to manually entered coordinates.")
    location = {
        "id": secrets.token_urlsafe(12),
        "name": payload.name.strip(),
        "latitude": payload.latitude,
        "longitude": payload.longitude,
        "allowed_radius": payload.allowed_radius,
        "verification_method": payload.verification_method,
        "accuracy_meters": payload.accuracy_meters,
        "location_timestamp": payload.location_timestamp,
        "manually_verified": payload.manually_verified,
        "active": True,
        "approved_by": str(admin["_id"]),
        "approved_at": datetime.now(timezone.utc),
    }
    users_collection.update_one({"_id": employee["_id"]}, {"$push": {"wfh_approved_locations": location}, "$set": {"updated_at": datetime.now(timezone.utc)}})
    return {key: location.get(key) for key in ("id", "name", "latitude", "longitude", "allowed_radius", "verification_method", "active")}


@router.delete("/wfh/admin/employees/{user_id}/approved-locations/{location_id}")
def remove_wfh_approved_location(
    user_id: str,
    location_id: str,
    authorization: str | None = Header(default=None),
):
    _require_wfh_admin(authorization)
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid employee ID.")
    result = users_collection.update_one(
        {"_id": ObjectId(user_id), "role": {"$regex": "^employee$", "$options": "i"}},
        {"$pull": {"wfh_approved_locations": {"id": location_id}}, "$set": {"updated_at": datetime.now(timezone.utc)}},
    )
    if result.matched_count != 1:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Employee not found.")
    return {"removed": True, "location_id": location_id}


@router.get("/wfh/admin/employees/{user_id}/face")
def get_wfh_employee_face_details(
    user_id: str,
    authorization: str | None = Header(default=None),
):
    """Return face registration details and an optional stored photograph to HR Admins."""
    _require_wfh_admin(authorization)
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid employee ID.")
    employee = users_collection.find_one(
        {
            "_id": ObjectId(user_id),
            "role": {"$regex": "^(employee|hr admin|hr_admin|administrator|admin|hr manager|manager)$", "$options": "i"},
        },
        {"name": 1, "employee_id": 1, "role": 1},
    )
    if not employee:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found.")

    face_record = wfh_face_templates_collection.find_one(
        {"user_id": user_id},
        {"template_encrypted": 1, "photo_encrypted": 1, "created_at": 1},
    )
    if not face_record:
        return {
            "face_registered": False,
            "employee": {
                "name": employee.get("name", ""),
                "employee_id": employee.get("employee_id", ""),
                "role": employee.get("role", "Employee"),
            },
            "registered_at": None,
            "photograph_available": False,
            "photograph_data_url": None,
        }

    photo_data_url = None
    encrypted_photo = face_record.get("photo_encrypted")
    if encrypted_photo:
        try:
            photo_bytes = decrypt_face_photo(encrypted_photo)
            photo_data_url = "data:image/jpeg;base64," + base64.b64encode(photo_bytes).decode("ascii")
        except (RuntimeError, ValueError) as exc:
            logger.warning("Unable to load stored WFH face photograph for user %s: %s", user_id, exc)
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "The stored face photograph could not be opened.",
            ) from exc

    return {
        "face_registered": True,
        "employee": {
            "name": employee.get("name", ""),
            "employee_id": employee.get("employee_id", ""),
            "role": employee.get("role", "Employee"),
        },
        "registered_at": face_record.get("created_at").isoformat() if face_record.get("created_at") else None,
        "photograph_available": photo_data_url is not None,
        "photograph_data_url": photo_data_url,
    }


@router.delete("/wfh/admin/employees/{user_id}/face")
def reset_wfh_employee_face(
    user_id: str,
    authorization: str | None = Header(default=None),
):
    _require_wfh_admin(authorization)
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid employee ID.")
    employee = users_collection.find_one(
        {
            "_id": ObjectId(user_id),
            "role": {"$regex": "^(employee|hr admin|hr_admin|administrator|admin|hr manager|manager)$", "$options": "i"},
        },
        {"_id": 1},
    )
    if not employee:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found.")
    wfh_face_templates_collection.delete_one({"user_id": user_id})
    return {"face_registered": False}


@router.post("/wfh/location/verify")
def verify_wfh_location(
    payload: WfhLocationVerifyPayload,
    authorization: str | None = Header(default=None),
):
    _ensure_wfh_attendance_enabled()
    user = _require_wfh_employee(authorization)
    _assert_wfh_email_scope(authorization, payload.purpose)
    _assert_wfh_action_allowed(user, payload.purpose)
    distance, radius, location_name, approved_latitude, approved_longitude, location_id = _validate_wfh_location(user, payload)
    return {
        "verified": True,
        "distance_meters": round(distance, 1),
        "allowed_radius": radius,
        "approved_location_name": location_name,
        "approved_location_id": location_id,
        "approved_location_latitude": approved_latitude,
        "approved_location_longitude": approved_longitude,
        "max_location_accuracy_meters": _configured_wfh_accuracy_limit(),
    }


@router.post("/wfh/face/challenge")
def create_wfh_face_challenge(
    payload: WfhChallengePayload,
    authorization: str | None = Header(default=None),
):
    if payload.purpose != "register":
        _ensure_wfh_attendance_enabled()
    user = _require_wfh_employee(authorization)
    _assert_wfh_email_scope(authorization, payload.purpose)
    user_id = str(user["_id"])
    if payload.purpose == "register" and _face_is_registered(user_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Your face is already registered. Contact HR Admin if it must be reset.",
        )
    if payload.purpose != "register":
        _assert_wfh_action_allowed(user, payload.purpose)
        if (
            payload.latitude is None
            or payload.longitude is None
            or payload.accuracy_m is None
            or payload.location_timestamp is None
        ):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "A fresh location is required before face verification can start.",
            )
        location = WfhLocationPayload(
            latitude=payload.latitude,
            longitude=payload.longitude,
            accuracy_m=payload.accuracy_m,
            location_timestamp=payload.location_timestamp,
        )
        _validate_wfh_location(user, location)
        if not _face_is_registered(user_id):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Face registration is required before WFH attendance can be taken.",
            )

    turn = secrets.choice(("turn_left", "turn_right"))
    actions = list(
        secrets.choice(
            (
                ("blink", turn),
                (turn, "blink"),
            )
        )
    )
    challenge_id = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=2)
    wfh_liveness_challenges_collection.insert_one(
        {
            "challenge_id": challenge_id,
            "user_id": user_id,
            "purpose": payload.purpose,
            "actions": actions,
            "expires_at": expires_at,
        }
    )
    return {
        "challenge_id": challenge_id,
        "actions": actions,
        "expires_at": expires_at,
    }


def _consume_wfh_face_challenge(
    user_id: str,
    challenge_id: str,
    purpose: str,
) -> dict[str, Any]:
    challenge = wfh_liveness_challenges_collection.find_one_and_delete(
        {
            "challenge_id": challenge_id,
            "user_id": user_id,
            "purpose": purpose,
            "expires_at": {"$gt": datetime.now(timezone.utc)},
        }
    )
    if not challenge:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Camera verification expired. Please start again.",
        )
    return challenge


def _decode_and_verify_face(
    encoded_frames: list[str],
    actions: list[str],
    user_id: str,
) -> list[float] | None:
    try:
        frames = decode_frames(encoded_frames)
        if actions not in (
            ["blink", "turn_left"],
            ["blink", "turn_right"],
            ["turn_left", "blink"],
            ["turn_right", "blink"],
        ):
            raise RuntimeError("The camera liveness challenge is invalid.")
        if user_id:
            template = wfh_face_templates_collection.find_one(
                {"user_id": user_id},
                {"template_encrypted": 1},
            )
            if not template:
                raise ValueError(
                    "Your face is not registered. Please contact HR Admin."
                )
            verify_face_template(
                template["template_encrypted"],
                frames,
                actions,
            )
            return None
        return create_face_template(frames, actions)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


@router.post("/wfh/face/register")
def register_wfh_face(
    payload: WfhFacePayload,
    authorization: str | None = Header(default=None),
):
    user = _require_wfh_employee(authorization)
    _assert_wfh_email_scope(authorization, "register")
    user_id = str(user["_id"])
    if _face_is_registered(user_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Your face is already registered. Contact HR Admin if it must be reset.",
        )
    challenge = _consume_wfh_face_challenge(
        user_id,
        payload.challenge_id,
        "register",
    )
    template = _decode_and_verify_face(
        payload.frames,
        challenge["actions"],
        "",
    )
    if template is None:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Face registration failed. Please try again.",
        )
    try:
        encrypted_template = encrypt_face_template(template)
        # Retain one frame from the successful liveness capture for the HR Admin
        # face-details view. The photograph is encrypted at rest with the same
        # server-managed biometric key; raw frames are never returned to the client.
        registration_frames = decode_frames(payload.frames)
        encrypted_photo = encrypt_face_photo(registration_frames[-1])
        registered_at = datetime.now(timezone.utc)
        wfh_face_templates_collection.insert_one(
            {
                "user_id": user_id,
                "template_encrypted": encrypted_template,
                "photo_encrypted": encrypted_photo,
                "created_at": registered_at,
            }
        )
    except DuplicateKeyError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "A face is already registered. Contact HR Admin if it must be reset.",
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    _consume_wfh_email_scope(authorization, "register")
    return {"face_registered": True}


def _validate_wfh_biometric_attendance(
    user: dict[str, Any],
    payload: WfhAttendancePayload,
    purpose: str,
) -> tuple[float, float, str, float, float, str, dict[str, Any]]:
    user_id = str(user["_id"])
    _assert_wfh_action_allowed(user, purpose)
    distance, radius, location_name, approved_latitude, approved_longitude, location_id = _validate_wfh_location(user, payload)
    challenge = _consume_wfh_face_challenge(
        user_id,
        payload.challenge_id,
        purpose,
    )
    _decode_and_verify_face(
        payload.frames,
        challenge["actions"],
        user_id,
    )
    return distance, radius, location_name, approved_latitude, approved_longitude, location_id, challenge


@router.post("/wfh/check-in")
def wfh_check_in(
    payload: WfhAttendancePayload,
    authorization: str | None = Header(default=None),
):
    _ensure_wfh_attendance_enabled()
    user = _require_wfh_employee(authorization)
    _assert_wfh_email_scope(authorization, "check_in")
    user_id = str(user["_id"])
    assert_on_or_after_joining(user, company_today())
    distance, radius, approved_location_name, approved_latitude, approved_longitude, approved_location_id, _ = _validate_wfh_biometric_attendance(
        user,
        payload,
        "check_in",
    )
    now = company_now()
    verified_at = now.astimezone(timezone.utc)
    date_iso = now.date().isoformat()
    # Link the approved late permission that authorised this check-in (audit trail).
    late_permission = None
    if user.get("wfh_permitted_start_time") and now > _wfh_check_in_deadlines(user, now)[1]:
        late_permission = _wfh_late_check_in_request(user_id, date_iso)
    try:
        result = attendance_collection.insert_one(
            {
                "user_id": user_id,
                "employee_id": user.get("employee_id", ""),
                "attendance_date": date_iso,
                "status": "Present",
                "check_in_time": now.strftime("%H:%M"),
                "check_in_at": verified_at,
                "check_out_time": None,
                "wfh_breaks": [],
                "attendance_type": "WFH",
                "attendance_source": "WFH_FACE_GPS",
                "check_in_latitude": payload.latitude,
                "check_in_longitude": payload.longitude,
                "check_in_distance": round(distance, 1),
                "check_in_accuracy_meters": payload.accuracy_m,
                "check_in_location_timestamp": payload.location_timestamp,
                "face_verification_status": "verified",
                "check_in_location_verified": True,
                "check_in_face_verified": True,
                "check_in_liveness_verified": True,
                "check_in_approved_home_latitude": approved_latitude,
                "check_in_approved_home_longitude": approved_longitude,
                "check_in_approved_location_id": approved_location_id,
                "check_in_approved_radius_meters": radius,
                "check_in_approved_location_name": approved_location_name,
                "check_in_verified_at": verified_at,
                "late_check_in_permission_request_id": (
                    str(late_permission["_id"])
                    if late_permission and late_permission.get("status") == "approved"
                    else None
                ),
                "marked_by": user_id,
                "created_at": verified_at,
                "updated_at": verified_at,
            }
        )
    except DuplicateKeyError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Attendance has already been marked for today.",
        ) from exc
    record = attendance_collection.find_one({"_id": result.inserted_id})
    if not record:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "WFH Check-In was saved but could not be loaded.",
        )
    _consume_wfh_email_scope(authorization, "check_in")
    return _to_response(record, user)


@router.post("/wfh/check-out")
def wfh_check_out(
    payload: WfhAttendancePayload,
    authorization: str | None = Header(default=None),
):
    _ensure_wfh_attendance_enabled()
    user = _require_wfh_employee(authorization)
    _assert_wfh_email_scope(authorization, "check_out")
    user_id = str(user["_id"])
    assert_on_or_after_joining(user, company_today())
    distance, radius, approved_location_name, approved_latitude, approved_longitude, approved_location_id, _ = _validate_wfh_biometric_attendance(
        user,
        payload,
        "check_out",
    )
    now = company_now()
    verified_at = now.astimezone(timezone.utc)
    date_iso = now.date().isoformat()
    result = attendance_collection.update_one(
        {
            "user_id": user_id,
            "attendance_date": date_iso,
            "attendance_source": "WFH_FACE_GPS",
            "check_in_time": {"$ne": None},
            "check_out_time": None,
            "wfh_breaks": {"$not": {"$elemMatch": {"ended_at": None}}},
        },
        {
            "$set": {
                "check_out_time": now.strftime("%H:%M"),
                "check_out_at": verified_at,
                "attendance_type": "WFH",
                "check_out_latitude": payload.latitude,
                "check_out_longitude": payload.longitude,
                "check_out_distance": round(distance, 1),
                "check_out_accuracy_meters": payload.accuracy_m,
                "check_out_location_timestamp": payload.location_timestamp,
                "face_verification_status": "verified",
                "check_out_location_verified": True,
                "check_out_face_verified": True,
                "check_out_liveness_verified": True,
                "check_out_approved_home_latitude": approved_latitude,
                "check_out_approved_home_longitude": approved_longitude,
                "check_out_approved_location_id": approved_location_id,
                "check_out_approved_radius_meters": radius,
                "check_out_approved_location_name": approved_location_name,
                "check_out_verified_at": verified_at,
                "updated_at": verified_at,
            }
        },
    )
    if result.matched_count != 1:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "A valid WFH Check-In is required before Check-Out, or you have already checked out.",
        )
    record = attendance_collection.find_one(
        {"user_id": user_id, "attendance_date": date_iso}
    )
    if not record:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "WFH Check-Out was saved but could not be loaded.",
        )
    _consume_wfh_email_scope(authorization, "check_out")
    return _to_response(record, user)


# ---------------------------------------------------------------------------
# GET /api/attendance
# ---------------------------------------------------------------------------

@router.get(
    "",
    response_model=list[AttendanceResponse],
)
def list_attendance(
    attendance_date: str = Query(...),
    authorization: str | None = Header(
        default=None
    ),
):
    """
    Get attendance for a particular date.

    Leave is lazily synchronized before records are returned.
    """

    _require_hr_admin(
        authorization,
        "Only HR Admin or Administrator can manage attendance",
    )

    selected = _validate_date(
        attendance_date
    )

    today = company_today()

    # Future dates never show attendance or leave.
    if selected > today:
        return []

    # Automatically create approved Leave attendance
    # for the selected date if necessary.
    _sync_leave_for_date(
        attendance_date
    )

    records = list(
        attendance_collection.find(
            {
                "attendance_date": attendance_date,
            }
        ).sort(
            "created_at",
            -1,
        )
    )

    object_ids = [
        ObjectId(r["user_id"])
        for r in records
        if ObjectId.is_valid(
            r.get("user_id", "")
        )
    ]

    users = {
        str(u["_id"]): u
        for u in users_collection.find(
            {
                "_id": {
                    "$in": object_ids
                }
            }
        )
    }

    response_rows = []
    for record in records:
        record_user_id = record.get("user_id")
        if record_user_id not in users:
            continue
        row = _to_response(record, users[record_user_id])
        # Attach the same shared interval calculation used by the detailed views.
        daily = _calculate_daily_attendance(
            record_user_id,
            selected,
            users[record_user_id],
        )
        row.update(daily)
        permission_map = _collect_permissions(record_user_id, selected.isoformat(), (selected + timedelta(days=1)).isoformat(), {selected.isoformat(): record})
        permission_entries = permission_map.get(selected.isoformat(), [])
        permission_minutes = _union_minutes([entry["_interval"] for entry in permission_entries])
        check_in = daily.get("first_check_in") or record.get("check_in_time")
        check_out = daily.get("last_check_out") or record.get("check_out_time")
        # Add only approved permission minutes outside the actual check-in/out
        # span. Permission already covered by the attendance duration must not
        # be counted a second time.
        permission_additional = permission_minutes_outside_total_duration(
            [entry["_interval"] for entry in permission_entries],
            check_in,
            check_out,
        )
        effective_minutes = effective_working_minutes(
            daily.get("working_minutes"), permission_additional
        )
        completed = bool(selected < company_today() or (check_out and daily.get("attendance_state") != "In Progress"))
        half = classify_half_day(
            total_duration_minutes=int(daily.get("elapsed_minutes") or daily.get("total_duration_minutes") or 0),
            permission_intervals=[entry["_interval"] for entry in permission_entries],
            recalculated_base=_is_automatic_attendance_record(record),
            half_day_threshold_minutes=int(daily.get("half_day_working_duration") or 0),
            scheduled_start=daily.get("scheduled_work_start_time"),
            scheduled_end=daily.get("scheduled_work_end_time"),
            check_in=check_in, check_out=check_out, day_completed=completed,
            base_status=record.get("status") or "Not Marked",
            leave_day_part=record.get("leave_day_part"),
            leave_is_full_day=bool(record.get("status") == "Leave" and (record.get("leave_day_part") or "full_day") == "full_day"),
            working_intervals=daily.get("working_intervals") or [],
        )
        if record.get("first_half_status") and record.get("second_half_status") and _preserve_saved_half_day_status(record):
            half.update(combine_half_day_statuses(record["first_half_status"], record["second_half_status"]))
            half["half_day_eligibility"] = record.get("half_day_eligibility")
            half["actual_working_minutes"] = record.get("actual_working_minutes", effective_minutes)
        issue = attendance_issue(check_in, check_out, daily.get("working_intervals") or [])
        early, late = _going_by_minutes(check_out, daily.get("scheduled_work_end_time"))
        row.update({**half, **issue,
            "actual_elapsed_minutes": int(daily.get("elapsed_minutes") or 0),
            "total_duration_minutes": int(daily.get("elapsed_minutes") or 0),
            "total_duration": _format_duration(int(daily.get("elapsed_minutes") or 0)),
            "effective_working_minutes": effective_minutes,
            "permission_total_minutes": permission_minutes,
            "early_going_by_minutes": early, "late_going_by_minutes": late,
            "early_going_by": _format_duration(early) if early is not None else "—",
            "late_going_by": _format_duration(late) if late is not None else "—",
        })
        calculated_status = row.get("status") or "Not Marked"
        _status_override = half_day_status_override(
            half,
            total_duration_minutes=int(daily.get("elapsed_minutes") or daily.get("total_duration_minutes") or 0),
            half_day_threshold_minutes=int(daily.get("half_day_working_duration") or 0),
            check_in=check_in, check_out=check_out, day_completed=completed,
            current_status=calculated_status,
            recalculated=_is_automatic_attendance_record(record),
        )
        if _status_override:
            calculated_status = _status_override
        row["calculated_attendance_status"] = calculated_status
        response_rows.append(row)
    return response_rows


# ---------------------------------------------------------------------------
# POST /api/attendance
# ---------------------------------------------------------------------------

@router.post(
    "",
    response_model=AttendanceResponse,
)
def save_attendance(
    payload: AttendanceCreate,
    authorization: str | None = Header(
        default=None
    ),
):
    """
    Create/update attendance.

    Supported statuses:
        Present
        Absent
        Holiday
        Leave

    Leave is special:
        - It must correspond to an approved leave.
        - Leave metadata is resolved from MongoDB.
        - Frontend cannot fake leave information.
        - Full-day approved leave cannot be changed to another status.
        - Saving Leave itself is idempotent and succeeds.
    """

    admin = _require_hr_admin(
        authorization,
        "Only HR Admin or Administrator can mark attendance",
    )

    attendance_status = (
        payload.status.strip().capitalize()
    )

    # ------------------------------------------------------------------
    # Validate status
    # ------------------------------------------------------------------

    if attendance_status not in {
        "Present",
        "Absent",
        "Holiday",
        "Leave",
    }:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Status must be Present, Absent, Holiday or Leave",
        )

    # ------------------------------------------------------------------
    # Validate date
    # ------------------------------------------------------------------

    selected = _validate_date(
        payload.attendance_date
    )

    today = company_today()

    if selected != today:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "The Daily Attendance page can modify the current date only. Use Historical Attendance Modification for eligible previous months.",
        )

    # ------------------------------------------------------------------
    # Validate user
    # ------------------------------------------------------------------

    if not ObjectId.is_valid(
        payload.user_id
    ):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Invalid user ID",
        )

    user = users_collection.find_one(
        {
            "_id": ObjectId(
                payload.user_id
            )
        }
    )

    if not user:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "User not found",
        )

    user_id = str(user["_id"])
    employee_id_for_lock = str(user.get("employee_id") or "")
    payroll_row = db["monthly_payroll_records"].find_one({"employee_id": employee_id_for_lock, "period": selected.strftime("%Y-%m")}, {"status": 1}) if employee_id_for_lock else None
    payroll_status = (payroll_row or {}).get("status")
    if payroll_status in {"Approved", "Processed", "Paid"}:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Attendance editing is unavailable because payroll for {selected.strftime('%B %Y')} has already reached {payroll_status} status.")

    assert_on_or_after_joining(user, selected)
    if not payload.remove_permission and (payload.permission_start_time or payload.permission_end_time):
        if not payload.permission_start_time or not payload.permission_end_time:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Both Permission start and end times are required.")
        _validate_time(payload.permission_start_time, "Permission start")
        _validate_time(payload.permission_end_time, "Permission end")
        if payload.permission_end_time <= payload.permission_start_time:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Permission end time must be later than start time.")

    # ------------------------------------------------------------------
    # Find approved leave for this employee/date
    # ------------------------------------------------------------------

    approved_leaves = _approved_leaves_for_date(
        payload.attendance_date,
        user_id,
    )

    # There should normally be one approved leave.
    # Use the first valid approved request.
    approved_leave = (
        approved_leaves[0]
        if approved_leaves
        else None
    )

    # ------------------------------------------------------------------
    # LEAVE SAVE
    # ------------------------------------------------------------------

    if attendance_status == "Leave":

        # IMPORTANT:
        # Leave cannot be created manually without an approved
        # leave request in the Leave module.
        if not approved_leave:

            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "No approved leave exists for this employee on the selected date.",
            )

        now = datetime.now(
            timezone.utc
        )

        marked_by = str(
            admin["_id"]
        )

        key = {
            "user_id": user_id,
            "attendance_date": payload.attendance_date,
        }

        leave_fields = (
            _build_leave_attendance_fields(
                approved_leave,
                now,
            )
        )

        # Keep HR admin as the person who explicitly saved it.
        leave_fields["marked_by"] = marked_by
        existing_leave_attendance = attendance_collection.find_one(key)
        leave_fields["attendance_source"] = "MANUAL"
        leave_fields["attendance_type"] = "OFFICE"

        try:
            attendance_collection.update_one(
                key,
                {
                    "$set": leave_fields,
                    "$setOnInsert": {
                        "employee_id": user.get(
                            "employee_id",
                            "",
                        ),
                        "created_at": now,
                    },
                    **(
                        {
                            "$unset": {
                                "first_punch": "",
                                "last_punch": "",
                                "last_essl_sync_at": "",
                                "last_essl_sync_id": "",
                            }
                        }
                        if existing_leave_attendance
                        and existing_leave_attendance.get("attendance_source") == "ESSL"
                        else {}
                    ),
                },
                upsert=True,
            )

        except DuplicateKeyError:
            # Safe retry in case two requests arrive together.
            attendance_collection.update_one(
                key,
                {
                    "$set": leave_fields,
                    **(
                        {
                            "$unset": {
                                "first_punch": "",
                                "last_punch": "",
                                "last_essl_sync_at": "",
                                "last_essl_sync_id": "",
                            }
                        }
                        if existing_leave_attendance
                        and existing_leave_attendance.get("attendance_source") == "ESSL"
                        else {}
                    ),
                },
            )

        if (
            existing_leave_attendance
            and existing_leave_attendance.get("attendance_source") == "ESSL"
        ):
            audit_logs_collection.insert_one(
                {
                    "user_id": marked_by,
                    "action": "attendance.manual_override",
                    "old_value": {
                        "attendance_id": str(existing_leave_attendance["_id"]),
                        "user_id": user_id,
                        "attendance_date": payload.attendance_date,
                        "status": existing_leave_attendance.get("status"),
                        "check_in_time": existing_leave_attendance.get("check_in_time"),
                        "check_out_time": existing_leave_attendance.get("check_out_time"),
                        "attendance_source": "ESSL",
                    },
                    "new_value": {
                        "attendance_date": payload.attendance_date,
                        "status": "Leave",
                        "attendance_source": "MANUAL",
                    },
                    "timestamp": now,
                }
            )

        record = attendance_collection.find_one(
            key
        )

        if not record:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "Unable to save Leave attendance.",
            )

        old_leave_status = (existing_leave_attendance or {}).get("status")
        old_leave_type = (existing_leave_attendance or {}).get("leave_type_code")
        if old_leave_status != "Leave" or old_leave_type != record.get("leave_type_code"):
            try:
                change_details = {"status": {"before": old_leave_status or "Not Marked", "after": "Leave"}}
                if old_leave_type != record.get("leave_type_code"):
                    change_details["leave_type"] = {"before": old_leave_type, "after": record.get("leave_type_code")}
                db["attendance_modification_history"].insert_one({
                    "user_id": user_id, "employee_id": user.get("employee_id", ""), "employee_name": user.get("name", ""),
                    "attendance_date": payload.attendance_date, "changes": change_details,
                    "reason": (payload.modification_reason or "Attendance leave correction by HR Admin").strip(),
                    "modified_by": marked_by, "modified_by_name": admin.get("name") or admin.get("work_email") or "HR Admin",
                    "modified_by_role": str(admin.get("role") or ""), "modified_at": now,
                })
                from app.notifications import create_system_notification
                create_system_notification(
                    title="Employee Attendance Modified",
                    message=f"Your attendance record for {payload.attendance_date} was changed from {old_leave_status or 'Not Marked'} to Leave by HR Admin. Leave type: {record.get('leave_type_code') or 'Approved Leave'}. Reason: {(payload.modification_reason or 'Attendance leave correction').strip()}",
                    validity=timedelta(days=90), event_key=f"attendance-admin-leave-edit:{user_id}:{payload.attendance_date}:{now.isoformat()}",
                    user_ids=[user_id],
                )
            except Exception:
                logger.exception("Leave attendance was saved but its audit/notification could not be created")

        return _to_response(record, user)

    # ------------------------------------------------------------------
    # FULL-DAY LEAVE LOCK
    # ------------------------------------------------------------------

    if approved_leave:

        day_part = approved_leave.get(
            "day_part",
            "full_day",
        )

        if day_part == "full_day":

            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Employee is on approved full-day leave. Cancel the leave to change attendance.",
            )

    # ------------------------------------------------------------------
    # Present validation
    # ------------------------------------------------------------------

    check_in_time = None
    check_out_time = None

    if attendance_status == "Present":

        if not payload.check_in_time:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Check-in time is required for Present attendance",
            )

        _validate_time(
            payload.check_in_time,
            "check-in",
        )

        check_in_time = (
            payload.check_in_time
        )

        if payload.check_out_time:

            _validate_time(
                payload.check_out_time,
                "check-out",
            )

            if (
                payload.check_out_time
                < payload.check_in_time
            ):
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "Check-out time cannot be earlier than check-in time",
                )

            check_out_time = (
                payload.check_out_time
            )

        # Today cannot contain future attendance times.
        if selected == today:

            current_time = (
                company_now().strftime(
                    "%H:%M"
                )
            )

            if (
                payload.check_in_time
                > current_time
            ):
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "Check-in time cannot be in the future",
                )

            if (
                payload.check_out_time
                and payload.check_out_time
                > current_time
            ):
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "Check-out time cannot be in the future",
                )

    # ------------------------------------------------------------------
    # Absent / Holiday
    # ------------------------------------------------------------------

    if attendance_status in {
        "Absent",
        "Holiday",
    }:
        check_in_time = None
        check_out_time = None

    # ------------------------------------------------------------------
    # Save normal attendance
    # ------------------------------------------------------------------

    now = datetime.now(
        timezone.utc
    )

    marked_by = str(
        admin["_id"]
    )

    key = {
        "user_id": user_id,
        "attendance_date": payload.attendance_date,
    }

    existing_attendance = attendance_collection.find_one(key)
    values = {
        "status": attendance_status,
        "check_in_time": check_in_time,
        "check_out_time": check_out_time,
        "marked_by": marked_by,
        "attendance_source": "MANUAL",
        "attendance_type": (existing_attendance or {}).get("attendance_type") or "OFFICE",
        "updated_at": now,
    }
    permission_before = (existing_attendance or {}).get("approved_permission")
    if payload.remove_permission:
        values["approved_permission"] = None
    elif payload.permission_start_time and payload.permission_end_time:
        requested_permission = {
            "start_time": payload.permission_start_time,
            "end_time": payload.permission_end_time,
            "reason": (payload.permission_reason or "Updated by HR Admin").strip(),
        }
        current_permission = permission_before or {}
        if any(current_permission.get(field) != value for field, value in requested_permission.items()):
            values["approved_permission"] = {
                **requested_permission,
                "approved_by": marked_by,
                "approved_at": now,
                "source": "HR_ADMIN_ATTENDANCE_EDIT",
            }

    # If an approved half-day leave previously created a Leave
    # record and HR changes it to Present/Absent/Holiday,
    # remove the old leave metadata.
    if approved_leave:
        values.update(
            {
                "leave_request_id": None,
                "leave_day_part": None,
                "leave_type_code": None,
            }
        )

    try:

        attendance_collection.update_one(
            key,
            {
                "$set": values,
                "$setOnInsert": {
                    "employee_id": user.get(
                        "employee_id",
                        "",
                    ),
                    "created_at": now,
                },
                **(
                    {
                        "$unset": {
                            "first_punch": "",
                            "last_punch": "",
                            "last_essl_sync_at": "",
                            "last_essl_sync_id": "",
                        }
                    }
                    if existing_attendance
                    and existing_attendance.get("attendance_source") == "ESSL"
                    else {}
                ),
            },
            upsert=True,
        )

    except DuplicateKeyError:

        update_values = {"$set": values}
        if existing_attendance and existing_attendance.get("attendance_source") == "ESSL":
            update_values["$unset"] = {
                "first_punch": "",
                "last_punch": "",
                "last_essl_sync_at": "",
                "last_essl_sync_id": "",
            }
        attendance_collection.update_one(key, update_values)

    # Preserve an immutable before/after record for every HR Admin correction.
    changed_fields = {}
    before_values = existing_attendance or {}
    for field, new_value in (("status", attendance_status), ("check_in_time", check_in_time), ("check_out_time", check_out_time)):
        old_value = before_values.get(field)
        if old_value != new_value:
            changed_fields[field] = {"before": old_value, "after": new_value}
    if permission_before != values.get("approved_permission", permission_before):
        changed_fields["permission"] = {"before": permission_before, "after": values.get("approved_permission")}
    if changed_fields:
        try:
            db["attendance_modification_history"].insert_one({
                "user_id": user_id, "employee_id": user.get("employee_id", ""),
                "employee_name": user.get("name", ""), "attendance_date": payload.attendance_date,
                "changes": changed_fields, "reason": (payload.modification_reason or payload.permission_reason or "Attendance correction by HR Admin").strip(),
                "modified_by": marked_by, "modified_by_name": admin.get("name") or admin.get("work_email") or "HR Admin",
                "modified_by_role": str(admin.get("role") or ""), "modified_at": now,
            })
            audit_logs_collection.insert_one({
                "user_id": marked_by, "action": "attendance.admin_modified",
                "old_value": {"user_id": user_id, "attendance_date": payload.attendance_date, "changes": {k: v["before"] for k, v in changed_fields.items()}},
                "new_value": {"user_id": user_id, "attendance_date": payload.attendance_date, "changes": {k: v["after"] for k, v in changed_fields.items()}},
                "timestamp": now,
            })
            from app.notifications import create_system_notification
            lines = []
            for field, change in changed_fields.items():
                if field == "permission":
                    before = change["before"] or {}
                    after = change["after"] or {}
                    before_text = f"{before.get('start_time', '')}–{before.get('end_time', '')}" if before else "None"
                    after_text = f"{after.get('start_time', '')}–{after.get('end_time', '')}" if after else "None (removed)"
                    lines.append(f"Permission: {before_text} → {after_text}")
                else:
                    lines.append(f"{field.replace('_', ' ').title()}: {change['before'] or '—'} → {change['after'] or '—'}")
            create_system_notification(
                title="Employee Attendance Modified",
                message=f"Your attendance record for {payload.attendance_date} was modified by HR Admin.\n" + "\n".join(lines) + f"\nReason: {(payload.modification_reason or payload.permission_reason or 'Attendance correction').strip()}",
                validity=timedelta(days=90), event_key=f"attendance-admin-edit:{user_id}:{payload.attendance_date}:{now.isoformat()}",
                user_ids=[user_id],
            )
        except Exception:
            logger.exception("Attendance was saved but audit/notification creation failed for %s %s", user_id, payload.attendance_date)

    # A calculated payroll is a draft; mark it outdated so it must be recalculated before approval.
    if payroll_status == "Calculated" and employee_id_for_lock:
        db["monthly_payroll_records"].update_one(
            {"employee_id": employee_id_for_lock, "period": selected.strftime("%Y-%m"), "status": "Calculated"},
            {"$set": {"attendance_outdated": True, "attendance_outdated_at": now, "attendance_outdated_reason": "Attendance was modified by HR Admin."}, "$addToSet": {"attendance_outdated_dates": payload.attendance_date}},
        )

    if existing_attendance and existing_attendance.get("attendance_source") == "ESSL":
        audit_logs_collection.insert_one(
            {
                "user_id": marked_by,
                "action": "attendance.manual_override",
                "old_value": {
                    "attendance_id": str(existing_attendance["_id"]),
                    "user_id": user_id,
                    "attendance_date": payload.attendance_date,
                    "status": existing_attendance.get("status"),
                    "check_in_time": existing_attendance.get("check_in_time"),
                    "check_out_time": existing_attendance.get("check_out_time"),
                    "attendance_source": existing_attendance.get(
                        "attendance_source"
                    ),
                },
                "new_value": {
                    "attendance_date": payload.attendance_date,
                    "status": attendance_status,
                    "check_in_time": check_in_time,
                    "check_out_time": check_out_time,
                    "attendance_source": "MANUAL",
                },
                "timestamp": now,
            }
        )

    # ------------------------------------------------------------------
    # Return saved MongoDB record
    # ------------------------------------------------------------------

    record = attendance_collection.find_one(
        key
    )

    if not record:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Unable to save attendance.",
        )

    return _to_response(
        record,
        user,
    )


# ---------------------------------------------------------------------------
# Historical attendance: read from joining month; modify previous three completed months and prior dates in the current month
# ---------------------------------------------------------------------------

class HistoricalAttendanceUpdate(BaseModel):
    user_id: str
    attendance_date: str
    status: str
    check_in_time: str | None = None
    check_out_time: str | None = None
    first_half_status: str | None = None
    second_half_status: str | None = None
    first_half_check_in_time: str | None = None
    first_half_check_out_time: str | None = None
    second_half_check_in_time: str | None = None
    second_half_check_out_time: str | None = None
    permission_start_time: str | None = None
    permission_end_time: str | None = None
    permission_reason: str | None = None
    modification_reason: str = Field(min_length=3, max_length=500)
    expected_updated_at: datetime | None = None
    remove_permission: bool = False


def _historical_month_window(today: date) -> tuple[date, date]:
    current_start = date(today.year, today.month, 1)
    month_index = today.year * 12 + today.month - 1 - 3
    allowed_start = date(month_index // 12, month_index % 12 + 1, 1)
    return allowed_start, current_start


def _validate_historical_read_month(user: dict[str, Any], year: int, month: int) -> tuple[date, date]:
    today = company_today()
    if month < 1 or month > 12 or year < 2000:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid month or year.")
    selected_start = date(year, month, 1)
    current_start = date(today.year, today.month, 1)
    joining_value = user.get("joining_date")
    if isinstance(joining_value, datetime):
        joining_value = joining_value.date()
    elif isinstance(joining_value, str):
        try:
            joining_value = date.fromisoformat(joining_value[:10])
        except ValueError:
            joining_value = None
    if selected_start > current_start or (joining_value and selected_start < date(joining_value.year, joining_value.month, 1)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Select a month between the employee's joining month and the current month.")
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return selected_start, end


def _require_historical_attendance_permission(authorization: str | None) -> dict[str, Any]:
    """Require an explicit role permission; HR Admin role alone is not sufficient."""
    actor = _require_hr_admin(
        authorization,
        "Only an authorized HR Admin or Administrator can modify historical attendance.",
    )
    role_id = str(actor.get("role_id") or str(actor.get("role") or "").strip().lower().replace(" ", "_"))
    permission_names = {
        "attendance.historical.modify",
        "historical_attendance.modify",
        "attendance_modify_historical",
    }
    assigned = False
    if role_id:
        assigned = db["permissions"].find_one({
            "role_id": role_id,
            "permission": {"$in": list(permission_names)},
        }) is not None
    user_permissions = actor.get("permissions") or []
    if isinstance(user_permissions, dict):
        assigned = assigned or bool(user_permissions.get("attendance.historical.modify"))
    elif isinstance(user_permissions, list):
        assigned = assigned or bool(permission_names.intersection({str(x) for x in user_permissions}))
    if not assigned:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Historical attendance modification permission is not assigned to your role. Ask the system administrator to grant attendance.historical.modify.",
        )
    return actor


def _validate_historical_month(year: int, month: int) -> tuple[date, date]:
    today = company_today()
    if month < 1 or month > 12 or year < 2000:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid month or year.")
    start, current_start = _historical_month_window(today)
    selected_start = date(year, month, 1)
    if selected_start < start or selected_start > current_start:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Attendance modification is available for previous dates in the current month and the previous three completed months. Today and future dates are view-only.",
        )
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return selected_start, end


@router.get("/historical/user/{user_id}")
def get_historical_attendance_month(
    user_id: str,
    year: int = Query(...),
    month: int = Query(...),
    authorization: str | None = Header(default=None),
):
    _require_historical_attendance_permission(authorization)
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid employee ID.")
    employee = users_collection.find_one({"_id": ObjectId(user_id), "is_active": True})
    if not employee:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Active employee not found.")
    start, end = _validate_historical_read_month(employee, year, month)
    employee_id = str(employee.get("employee_id") or "")
    payroll = db["monthly_payroll_records"].find_one(
        {"employee_id": employee_id, "period": f"{year:04d}-{month:02d}"}, {"status": 1}
    ) if employee_id else None
    payroll_status = (payroll or {}).get("status")
    payroll_locked = payroll_status in {"Approved", "Processed", "Paid"}
    if not payroll_locked:
        _sync_leave_for_user_range(user_id, start.isoformat(), (end - timedelta(days=1)).isoformat())
    records = {
        str(item.get("attendance_date", ""))[:10]: item
        for item in attendance_collection.find({
            "user_id": user_id,
            "attendance_date": {"$gte": start.isoformat(), "$lt": end.isoformat()},
        })
    }
    # Reuse the Leave module's canonical holiday calendar and company weekly-off policy.
    from app.leave import _holiday_dates
    holidays = _holiday_dates(employee, start, end - timedelta(days=1))
    rows = []
    modifiable_start, current_month_start = _historical_month_window(company_today())
    month_in_edit_window = modifiable_start <= start <= current_month_start
    today = company_today()
    cursor = start
    while cursor < end and cursor <= today:
        date_iso = cursor.isoformat()
        record = records.get(date_iso) or {}
        details = _calculate_daily_attendance(user_id, cursor, employee)
        permission = record.get("approved_permission") or {}
        day_check_in = details.get("first_check_in") or record.get("check_in_time")
        day_check_out = details.get("last_check_out") or record.get("check_out_time")
        permission_map = _collect_permissions(
            user_id, date_iso, (cursor + timedelta(days=1)).isoformat(), {date_iso: record}
        )
        permission_entries = permission_map.get(date_iso, [])
        permission_additional_minutes = permission_minutes_outside_total_duration(
            [entry["_interval"] for entry in permission_entries], day_check_in, day_check_out
        )
        effective_minutes = effective_working_minutes(
            int(details.get("working_minutes") or 0), permission_additional_minutes
        )
        half = classify_half_day(
            total_duration_minutes=int(details.get("elapsed_minutes") or details.get("total_duration_minutes") or 0),
            permission_intervals=[entry["_interval"] for entry in permission_entries],
            recalculated_base=_is_automatic_attendance_record(record),
            half_day_threshold_minutes=int(details.get("half_day_working_duration") or 0),
            scheduled_start=details.get("scheduled_work_start_time"),
            scheduled_end=details.get("scheduled_work_end_time"),
            check_in=day_check_in,
            check_out=day_check_out,
            day_completed=cursor < today or bool(day_check_out and details.get("attendance_state") != "In Progress"),
            base_status=record.get("status") or details.get("status") or "Not Marked",
            leave_day_part=record.get("leave_day_part"),
            leave_is_full_day=bool(record.get("status") == "Leave" and (record.get("leave_day_part") or "full_day") == "full_day"),
            working_intervals=details.get("working_intervals") or [],
        )
        if record.get("first_half_status") and record.get("second_half_status") and _preserve_saved_half_day_status(record):
            half.update(combine_half_day_statuses(record["first_half_status"], record["second_half_status"]))
            half["half_day_eligibility"] = record.get("half_day_eligibility")
        if record.get("first_half_status") and record.get("second_half_status") and _preserve_saved_half_day_status(record):
            status_value = record.get("status") or half.get("status") or "Not Marked"
        elif record.get("status") in {"Leave", "Medical Leave", "Holiday", "Weekly Off", "Permission", "Not Applicable"}:
            status_value = record["status"]
        elif cursor in holidays:
            status_value = "Holiday"
        else:
            status_value = record.get("status") or details.get("status") or "Not Marked"
            _status_override = half_day_status_override(
                half,
                total_duration_minutes=int(details.get("elapsed_minutes") or details.get("total_duration_minutes") or 0),
                half_day_threshold_minutes=int(details.get("half_day_working_duration") or 0),
                check_in=day_check_in, check_out=day_check_out,
                day_completed=cursor < today or bool(day_check_out and details.get("attendance_state") != "In Progress"),
                current_status=status_value,
                recalculated=_is_automatic_attendance_record(record),
            )
            if _status_override:
                status_value = _status_override

        # Historical ESSL/WFH attendance is recalculated with the same current
        # half-day classifier used by daily attendance. Persist only automatic,
        # non-admin-modified records and never touch payroll-finalized months.
        # Manual/approved attendance remains authoritative.
        if (
            not payroll_locked
            and record
            and record.get("attendance_source") in {"ESSL", "WFH_FACE_GPS"}
            and not record.get("hr_admin_modified")
            and status_value not in {"Leave", "Medical Leave", "Holiday", "Weekly Off", "Permission", "Not Applicable"}
        ):
            recalc_fields = {
                "status": status_value,
                "half_day_present": half.get("half_day_present"),
                "half_day_present_side": half.get("half_day_present_side"),
                "half_day_eligibility": half.get("half_day_eligibility"),
                "first_half_status": half.get("first_half_status"),
                "second_half_status": half.get("second_half_status"),
                "actual_working_minutes": int(details.get("working_minutes") or 0),
                "half_day_working_duration": int(details.get("half_day_working_duration") or 0),
                "attendance_calculation_updated_at": datetime.now(timezone.utc),
                "attendance_calculation_version": "current",
                "updated_at": datetime.now(timezone.utc),
            }
            attendance_collection.update_one(
                {"_id": record["_id"], "user_id": user_id, "attendance_date": date_iso,
                 "attendance_source": record.get("attendance_source"), "hr_admin_modified": {"$ne": True}},
                {"$set": recalc_fields},
            )
            record.update(recalc_fields)

        rows.append({
            "attendance_date": date_iso,
            "status": status_value,
            "check_in_time": _attendance_clock_hhmm(record.get("check_in_time") or details.get("first_check_in")),
            "check_out_time": _attendance_clock_hhmm(record.get("check_out_time") or details.get("last_check_out")),
            **half,
            "first_half_check_in_time": _attendance_clock_hhmm(record.get("first_half_check_in_time")),
            "first_half_check_out_time": _attendance_clock_hhmm(record.get("first_half_check_out_time")),
            "second_half_check_in_time": _attendance_clock_hhmm(record.get("second_half_check_in_time")),
            "second_half_check_out_time": _attendance_clock_hhmm(record.get("second_half_check_out_time")),
            # Same current calculation as the daily and monthly views. Payroll-locked
            # months are displayed with it too but are never written (see above).
            "first_half_status": half.get("first_half_status"),
            "second_half_status": half.get("second_half_status"),
            "half_day_present": half.get("half_day_present"),
            "half_day_present_side": half.get("half_day_present_side"),
            "half_day_eligibility": half.get("half_day_eligibility", record.get("half_day_eligibility")),
            "half_day_review_required": bool(half.get("half_day_review_required")),
            "actual_working_minutes": record.get("actual_working_minutes", details.get("working_minutes")),
            "attendance_source": record.get("attendance_source"),
            "attendance_type": record.get("attendance_type"),
            "permission_start_time": _attendance_clock_hhmm(permission.get("start_time")),
            "permission_end_time": _attendance_clock_hhmm(permission.get("end_time")),
            "permission_reason": permission.get("reason"),
            "working_duration": details.get("working_duration") or details.get("total_working_duration") or "0h 00m",
            "working_minutes": int(details.get("working_minutes") or 0),
            "total_duration": _format_duration(int(details.get("elapsed_minutes") or ((details.get("working_minutes") or 0) + (details.get("outside_minutes") or 0)))),
            "scheduled_work_start_time": details.get("scheduled_work_start_time") or "",
            "scheduled_work_end_time": details.get("scheduled_work_end_time") or "",
            "half_day_working_duration_text": details.get("half_day_working_duration_text") or "",
            "half_day_threshold_met": bool(details.get("half_day_working_duration") and int(details.get("elapsed_minutes") or details.get("total_duration_minutes") or 0) >= int(details.get("half_day_working_duration") or 0)),
            "break_duration": details.get("break_duration") or "0h 00m",
            "outside_duration": details.get("outside_duration") or "0h 00m",
            "working_intervals": details.get("working_intervals") or [],
            "break_intervals": details.get("break_intervals") or [],
            "attendance_state": details.get("attendance_state"),
            "updated_at": record.get("updated_at"),
            "is_before_joining": bool(employee.get("joining_date") and date_iso < str(employee.get("joining_date"))[:10]),
            "payroll_status": payroll_status,
            "payroll_locked": payroll_locked,
            "can_modify": bool(month_in_edit_window and not payroll_locked and cursor <= today and not (employee.get("joining_date") and date_iso < str(employee.get("joining_date"))[:10])),
        })
        cursor += timedelta(days=1)
    return {
        "employee": {"user_id": user_id, "employee_id": employee.get("employee_id", ""), "name": employee.get("name", "")},
        "year": year,
        "month": month,
        "records": rows,
    }


@router.get("/historical/user/{user_id}/history")
def get_historical_attendance_audit_history(
    user_id: str,
    attendance_date: str = Query(...),
    authorization: str | None = Header(default=None),
):
    _require_historical_attendance_permission(authorization)
    selected = _validate_date(attendance_date)
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid employee ID.")
    employee = users_collection.find_one({"_id": ObjectId(user_id), "is_active": True})
    if not employee:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Active employee not found.")
    _validate_historical_read_month(employee, selected.year, selected.month)
    rows = list(db["attendance_modification_history"].find({
        "user_id": user_id,
        "attendance_date": selected.isoformat(),
    }).sort("modified_at", -1))
    # Resolve display names for the actors and permission approvers so the UI
    # never has to show raw user ids. Leave-correction rows were saved without
    # the actor's name.
    def permission_pairs(changes: Any):
        permission = changes.get("permission") if isinstance(changes, dict) else None
        for side in ("before", "after"):
            value = permission.get(side) if isinstance(permission, dict) else None
            if isinstance(value, dict):
                yield value

    wanted_ids = set()
    for row in rows:
        if not row.get("modified_by_name") and row.get("modified_by"):
            wanted_ids.add(str(row["modified_by"]))
        for permission in permission_pairs(row.get("changes")):
            if permission.get("approved_by"):
                wanted_ids.add(str(permission["approved_by"]))
    user_names = {
        str(item["_id"]): item.get("name") or item.get("work_email") or ""
        for item in users_collection.find(
            {"_id": {"$in": [ObjectId(value) for value in wanted_ids if ObjectId.is_valid(value)]}},
            {"name": 1, "work_email": 1},
        )
    } if wanted_ids else {}
    result = []
    for row in rows:
        modified_by = str(row.get("modified_by", "") or "")
        changes = _utc_iso(row.get("changes", {}))
        for permission in permission_pairs(changes):
            approver = str(permission.get("approved_by") or "")
            permission["approved_by_name"] = user_names.get(approver, "") if approver else ""
        modified_at_value = row.get("modified_at")
        if isinstance(modified_at_value, datetime):
            if modified_at_value.tzinfo is None:
                modified_at_value = modified_at_value.replace(tzinfo=timezone.utc)
            modified_at_ist = modified_at_value.astimezone(ZoneInfo("Asia/Kolkata")).strftime("%d %b %Y, %I:%M:%S %p IST")
        else:
            modified_at_ist = ""
        result.append({
            "id": str(row.get("_id")),
            "changes": changes,
            "reason": row.get("reason", ""),
            "modified_by": modified_by,
            "modified_by_name": row.get("modified_by_name") or user_names.get(modified_by, ""),
            # Stored as UTC (``datetime.now(timezone.utc)``) but PyMongo returns
            # it naive. Send an explicit offset so clients convert it exactly once.
            "modified_at": _utc_iso(row.get("modified_at")),
            "modified_at_ist": modified_at_ist,
        })
    return result




class GenerateWeeklyOffPreviewPayload(BaseModel):
    user_id: str
    year: int = Field(ge=2000, le=2100)
    month: int = Field(ge=1, le=12)


class GenerateWeeklyOffApprovalPayload(BaseModel):
    user_id: str
    year: int = Field(ge=2000, le=2100)
    month: int = Field(ge=1, le=12)
    attendance_dates: list[str] = Field(min_length=1, max_length=4)
    expected_updated_at: dict[str, str | None] = Field(default_factory=dict)


_PAYROLL_LOCKED_STATUSES = {"Approved", "Processed", "Paid"}


def _weekly_off_month_facts(employee: dict[str, Any], year: int, month: int) -> dict[str, Any]:
    """Month-level facts shared by preview and approve (no candidate scan).

    Weekly Off generation works for any month from the employee's joining month
    through the current month; it is not limited to the HR correction window and
    does not need company Weekly Off weekdays. Payroll-finalized months are
    protected.
    """
    _validate_historical_read_month(employee, year, month)
    employee_id = str(employee.get("employee_id") or "")
    period = f"{year:04d}-{month:02d}"
    payroll = db["monthly_payroll_records"].find_one(
        {"employee_id": employee_id, "period": period}, {"status": 1}
    ) if employee_id else None
    payroll_status = (payroll or {}).get("status")
    first = date(year, month, 1)
    last = date(year, month, calendar.monthrange(year, month)[1])
    policy = db["leave_policies"].find_one({"leave_type_id": "__company_policy__"},
                                           {"weekly_offs_per_month": 1}) or {}
    try:
        policy_limit = int(policy.get("weekly_offs_per_month", MAX_WEEKLY_OFFS_PER_MONTH))
    except (TypeError, ValueError):
        policy_limit = MAX_WEEKLY_OFFS_PER_MONTH
    monthly_limit = min(MAX_WEEKLY_OFFS_PER_MONTH, max(0, policy_limit))
    return {
        "first": first, "last": last, "period": period, "payroll_status": payroll_status,
        "payroll_locked": payroll_status in _PAYROLL_LOCKED_STATUSES, "monthly_limit": monthly_limit,
    }


def _count_month_weekly_offs(user_id: str, first: date, last: date) -> int:
    return attendance_collection.count_documents({
        "user_id": user_id, "attendance_date": {"$gte": first.isoformat(), "$lte": last.isoformat()},
        "status": "Weekly Off",
    })


def _weekly_off_candidate_analysis(employee: dict[str, Any], year: int, month: int) -> dict[str, Any]:
    """Find the ``Not Marked`` dates that can safely become Weekly Off and suggest up to four.

    A date is a candidate only when it is in the selected month, not in the
    future, on/after joining, has no attendance status or punches, is not a
    holiday, approved leave or pending attendance request, and was not touched
    by HR Admin. Present/Absent/Leave/Holiday/Weekly Off/etc. are never candidates.
    """
    facts = _weekly_off_month_facts(employee, year, month)
    first, last = facts["first"], facts["last"]
    base = {"eligible_dates": [], "suggested_dates": [], "payroll_status": facts["payroll_status"],
            "payroll_locked": facts["payroll_locked"], "monthly_limit": facts["monthly_limit"],
            "existing_weekly_offs": 0, "remaining_limit": 0, "limit_reached": False,
            "can_generate": False, "suggested_weekdays": []}
    if facts["payroll_locked"]:
        return {**base, "reason": f"Payroll is {facts['payroll_status']}; attendance is protected."}

    user_id = str(employee["_id"])
    monthly_limit = facts["monthly_limit"]
    existing_weekly = _count_month_weekly_offs(user_id, first, last)
    remaining = max(0, monthly_limit - existing_weekly)
    base.update({"existing_weekly_offs": existing_weekly, "remaining_limit": remaining})
    if remaining == 0:
        return {**base, "limit_reached": True,
                "reason": f"All {monthly_limit} Weekly Off days already exist for this month."}

    from app.leave import _holiday_dates
    today = company_today()
    joining = employee_joining_date(employee)
    scan_end = min(last, today)
    scan_start = max(first, joining) if joining else first
    if scan_start > scan_end:
        return {**base, "reason": "No attendance dates are available in this month yet."}

    holidays = _holiday_dates(employee, scan_start, scan_end)
    existing = {str(row.get("attendance_date"))[:10]: row for row in attendance_collection.find({
        "user_id": user_id, "attendance_date": {"$gte": scan_start.isoformat(), "$lte": scan_end.isoformat()}
    })}
    pending_claims: set[str] = set()
    for request in db["attendance_requests"].find(
            {"user_id": user_id, "status": {"$in": ["pending", "processing"]}}, {"dates.attendance_date": 1}):
        for item in request.get("dates") or []:
            if item.get("attendance_date"):
                pending_claims.add(str(item["attendance_date"])[:10])

    punch_keys = ("check_in_time", "check_out_time", "first_half_check_in_time", "second_half_check_in_time",
                  "first_punch", "last_punch", "check_in_at", "check_out_at")
    candidates: list[date] = []
    meta: dict[str, dict[str, Any]] = {}
    cursor = scan_start
    while cursor <= scan_end:
        iso = cursor.isoformat()
        row = existing.get(iso) or {}
        status_text = str(row.get("status") or "Not Marked").strip().casefold()
        valid = (
            status_text in {"", "not marked"}
            and not any(row.get(key) for key in punch_keys)
            and not row.get("hr_admin_modified") and not row.get("leave_request_id")
            and cursor not in holidays and iso not in pending_claims
            and not _approved_leaves_for_date(iso, user_id)
        )
        if valid:
            # Raw device punches may exist without an attendance record; such a day is not "Not Marked".
            try:
                details = _calculate_daily_attendance(user_id, cursor, employee)
                has_punches = bool(details.get("first_check_in") or details.get("last_check_out")
                                   or int(details.get("working_minutes") or 0) > 0)
            except Exception:  # Cannot prove the day is empty, so never offer it.
                logger.exception("Weekly Off scan could not evaluate %s for %s", iso, user_id)
                has_punches = True
            valid = not has_punches
        if valid:
            candidates.append(cursor)
            meta[iso] = {
                "attendance_date": iso, "day": cursor.strftime("%A"),
                "current_status": row.get("status") or "Not Marked",
                "expected_updated_at": _utc_iso(row.get("updated_at")) if row.get("updated_at") else None,
            }
        cursor += timedelta(days=1)

    configured = company_weekly_off_weekdays()  # Optional hint only; never required.
    suggested = suggest_weekly_off_dates(candidates, remaining, configured)
    ranked = rank_weekdays(candidates, configured)
    reason = None if candidates else "No Not Marked dates are available for Weekly Off in this month."
    return {**base, "eligible_dates": [meta[item.isoformat()] for item in candidates],
            "suggested_dates": [item.isoformat() for item in suggested],
            "suggested_weekdays": [WEEKDAY_NAMES[day].capitalize() for day in ranked],
            "can_generate": bool(candidates), "reason": reason}


@router.post("/historical/weekly-off-preview")
def preview_historical_weekly_off(payload: GenerateWeeklyOffPreviewPayload,
                                  authorization: str | None = Header(default=None)):
    _require_historical_attendance_permission(authorization)
    if not ObjectId.is_valid(payload.user_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid employee ID.")
    employee = users_collection.find_one({"_id": ObjectId(payload.user_id), "is_active": True})
    if not employee:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Active employee not found.")
    result = _weekly_off_candidate_analysis(employee, payload.year, payload.month)
    return {
        "employee": {"user_id": payload.user_id, "employee_id": employee.get("employee_id", ""),
                     "name": employee.get("name", ""), "department": employee.get("department_name") or employee.get("department", "")},
        "year": payload.year, "month": payload.month, **result,
        "total_eligible": len(result["eligible_dates"]),
    }


@router.post("/historical/weekly-off-approve")
def approve_historical_weekly_off(payload: GenerateWeeklyOffApprovalPayload,
                                  authorization: str | None = Header(default=None)):
    actor = _require_historical_attendance_permission(authorization)
    if not ObjectId.is_valid(payload.user_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid employee ID.")
    employee = users_collection.find_one({"_id": ObjectId(payload.user_id), "is_active": True})
    if not employee:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Active employee not found.")
    for value in payload.attendance_dates:
        try:
            parsed = date.fromisoformat(value)
        except (TypeError, ValueError):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid date: {value!r}.")
        if parsed.isoformat() != value or (parsed.year, parsed.month) != (payload.year, payload.month):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{value} is not in the selected month.")

    # Everything is re-derived here; nothing the browser sends is trusted.
    analysis = _weekly_off_candidate_analysis(employee, payload.year, payload.month)
    if analysis.get("payroll_locked"):
        raise HTTPException(status.HTTP_409_CONFLICT, analysis.get("reason") or "Payroll is finalized.")
    problems = validate_weekly_off_selection(
        payload.attendance_dates, [item["attendance_date"] for item in analysis["eligible_dates"]],
        analysis["existing_weekly_offs"], analysis["monthly_limit"],
    )
    if problems:
        raise HTTPException(status.HTTP_409_CONFLICT, " ".join(problems) + " Refresh the preview and try again.")
    eligible_by_date = {item["attendance_date"]: item for item in analysis["eligible_dates"]}
    first, last = date(payload.year, payload.month, 1), date(payload.year, payload.month, calendar.monthrange(payload.year, payload.month)[1])
    employee_id = str(employee.get("employee_id") or "")
    actor_name = actor.get("name") or actor.get("work_email") or "HR Admin"

    now = datetime.now(timezone.utc)
    created, skipped = [], []
    for iso in sorted(payload.attendance_dates):
        if _count_month_weekly_offs(payload.user_id, first, last) >= analysis["monthly_limit"]:
            skipped.append({"attendance_date": iso, "reason": "The monthly Weekly Off limit was reached by another change."})
            continue
        expected = payload.expected_updated_at.get(iso, eligible_by_date[iso]["expected_updated_at"])
        current = attendance_collection.find_one({"user_id": payload.user_id, "attendance_date": iso})
        current_updated = _utc_iso(current.get("updated_at")) if current and current.get("updated_at") else None
        if current_updated != expected:
            skipped.append({"attendance_date": iso, "reason": "Attendance changed after preview; refresh and review this date."})
            continue
        update = {
            "employee_id": employee_id, "status": "Weekly Off",
            "attendance_source": "MANUAL_WEEKLY_OFF", "is_auto_generated_weekly_off": False,
            "weekly_off_generated_by": str(actor["_id"]), "weekly_off_generated_at": now,
            "marked_by": str(actor["_id"]), "updated_at": now,
        }
        if current:
            result = attendance_collection.update_one(
                {"_id": current["_id"], "status": current.get("status"), "updated_at": current.get("updated_at")},
                {"$set": update},
            )
            if not result.modified_count:
                skipped.append({"attendance_date": iso, "reason": "Attendance changed during approval; record was not overwritten."})
                continue
            old_status = current.get("status") or "Not Marked"
            old_source = current.get("attendance_source")
        else:
            try:
                attendance_collection.insert_one({"user_id": payload.user_id, "attendance_date": iso,
                                                  "created_at": now, **update})
            except DuplicateKeyError:
                skipped.append({"attendance_date": iso, "reason": "An attendance record was created concurrently."})
                continue
            old_status, old_source = "Not Marked", None
        db["attendance_modification_history"].insert_one({
            "modification_id": secrets.token_hex(12),
            "user_id": payload.user_id, "employee_id": employee_id,
            "employee_name": employee.get("name", ""), "attendance_date": iso,
            "changes": {
                "status": {"before": old_status, "after": "Weekly Off"},
                "attendance_source": {"before": old_source, "after": "MANUAL_WEEKLY_OFF"},
            },
            "reason": "Weekly Off generated by HR Admin.",
            "modified_by": str(actor["_id"]), "modified_by_name": actor_name,
            "modified_by_role": str(actor.get("role") or actor.get("role_name") or "HR Admin"),
            "action": "MANUAL_WEEKLY_OFF_GENERATED", "old_status": old_status, "new_status": "Weekly Off",
            "operation_type": "WEEKLY_OFF_APPROVAL", "approved_at": now, "modified_at": now,
        })
        audit_logs_collection.insert_one({
            "user_id": str(actor["_id"]), "action": "attendance.weekly_off_manually_generated",
            "old_value": {"employee_user_id": payload.user_id, "attendance_date": iso, "status": old_status},
            "new_value": {"employee_user_id": payload.user_id, "attendance_date": iso, "status": "Weekly Off"},
            "timestamp": now,
        })
        created.append(iso)

    if created and analysis.get("payroll_status") == "Calculated" and employee_id:
        db["monthly_payroll_records"].update_one(
            {"employee_id": employee_id, "period": f"{payload.year:04d}-{payload.month:02d}", "status": "Calculated"},
            {"$set": {"attendance_outdated": True, "attendance_outdated_at": now,
                      "attendance_outdated_reason": "Weekly Off was generated by HR Admin."},
             "$addToSet": {"attendance_outdated_dates": {"$each": created}}},
        )
    weekly_total = _count_month_weekly_offs(payload.user_id, first, last)
    return {"message": f"Generated {len(created)} Weekly Off date(s)." if created else "No Weekly Off dates were generated.",
            "generated_dates": created, "skipped_dates": skipped, "failed_dates": [], "payroll_locked_dates": [],
            "total_generated": len(created), "total_skipped": len(skipped),
            "weekly_off_total": weekly_total, "monthly_limit": analysis["monthly_limit"]}


class LegacyGenerateWeeklyOffPayload(BaseModel):
    user_id: str
    attendance_date: str


@router.post("/historical/generate-weekly-off")
def legacy_generate_historical_weekly_off(payload: LegacyGenerateWeeklyOffPayload,
                                           authorization: str | None = Header(default=None)):
    """Backward-compatible request shape; returns preview only, never writes without approval."""
    selected = _validate_date(payload.attendance_date)
    if not ObjectId.is_valid(payload.user_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid employee ID.")
    employee = users_collection.find_one({"_id": ObjectId(payload.user_id), "is_active": True})
    if not employee:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Active employee not found.")
    _require_historical_attendance_permission(authorization)
    result = _weekly_off_candidate_analysis(employee, selected.year, selected.month)
    return {
        "employee": {"user_id": payload.user_id, "employee_id": employee.get("employee_id", ""),
                     "name": employee.get("name", ""), "department": employee.get("department_name") or employee.get("department", "")},
        "year": selected.year, "month": selected.month, **result,
        "total_eligible": len(result["eligible_dates"]),
    }


@router.post("/historical/modify")
def modify_historical_attendance(
    payload: HistoricalAttendanceUpdate,
    authorization: str | None = Header(default=None),
):
    actor = _require_historical_attendance_permission(authorization)
    selected = _validate_date(payload.attendance_date)
    _validate_historical_month(selected.year, selected.month)
    if selected > company_today():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Future attendance dates cannot be modified.")
    if not ObjectId.is_valid(payload.user_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid employee ID.")
    employee = users_collection.find_one({"_id": ObjectId(payload.user_id), "is_active": True})
    if not employee:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Active employee not found.")
    assert_on_or_after_joining(employee, selected)
    status_value = payload.status.strip().title()
    allowed_statuses = {"Present", "Half-Day Present", "Absent", "Holiday", "Leave", "Weekly Off", "Medical Leave", "Permission", "Not Applicable", "Not Marked"}
    if status_value not in allowed_statuses:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unsupported attendance status.")
    employee_id = str(employee.get("employee_id") or "")
    payroll = db["monthly_payroll_records"].find_one({"employee_id": employee_id, "period": selected.strftime("%Y-%m")}, {"status": 1}) if employee_id else None
    payroll_status = (payroll or {}).get("status")
    if payroll_status in {"Approved", "Processed", "Paid"}:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Attendance is locked because payroll is {payroll_status}.")
    if payload.permission_start_time or payload.permission_end_time:
        if not payload.permission_start_time or not payload.permission_end_time:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Both permission start and end times are required.")
        _validate_time(payload.permission_start_time, "Permission start")
        _validate_time(payload.permission_end_time, "Permission end")
        if payload.permission_end_time <= payload.permission_start_time:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Permission end time must be later than start time.")
    half_fields = (
        "first_half_status", "second_half_status",
        "first_half_check_in_time", "first_half_check_out_time",
        "second_half_check_in_time", "second_half_check_out_time",
        "first_half_working_intervals", "second_half_working_intervals",
        "working_intervals",
        "half_day_present", "half_day_present_side", "half_day_eligibility",
        "half_day_working_duration", "actual_working_minutes",
    )
    half_values: dict[str, Any] = {}
    if status_value == "Half-Day Present":
        half_statuses = {
            "first": (payload.first_half_status or "").strip().title(),
            "second": (payload.second_half_status or "").strip().title(),
        }
        schedule = applicable_schedule(payload.user_id, selected)
        threshold = int(schedule.get("half_day_working_duration") or 0)
        time_fields = (
            (payload.first_half_check_in_time, payload.first_half_check_out_time),
            (payload.second_half_check_in_time, payload.second_half_check_out_time),
        )
        for side, values_for_side in zip(("First", "Second"), time_fields):
            if half_statuses[side.casefold()] == "Present":
                for value, label in zip(values_for_side, ("check-in", "check-out")):
                    if value:
                        _validate_time(value, f"{side} Half {label}")
        try:
            half_values = calculate_half_day_portions(
                first_half_status=half_statuses["first"],
                second_half_status=half_statuses["second"],
                first_half_check_in_time=payload.first_half_check_in_time,
                first_half_check_out_time=payload.first_half_check_out_time,
                second_half_check_in_time=payload.second_half_check_in_time,
                second_half_check_out_time=payload.second_half_check_out_time,
                half_day_threshold_minutes=threshold,
                scheduled_start=schedule.get("scheduled_work_start_time"),
                scheduled_end=schedule.get("scheduled_work_end_time"),
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

        approved_leave = _approved_leaves_for_date(selected.isoformat(), payload.user_id)
        full_day_leave = next((item for item in approved_leave if item.get("day_part", "full_day") == "full_day"), None)
        if full_day_leave:
            raise HTTPException(status.HTTP_409_CONFLICT, "Approved full-day leave locks this attendance date. Cancel the leave first.")
        leave_side = next((side for side, value in half_statuses.items() if value in {"Leave", "Medical Leave"}), None)
        leave_record = None
        if leave_side:
            expected_part = f"{leave_side}_half"
            leave_record = next(
                (item for item in approved_leave if item.get("day_part") == expected_part),
                None,
            )
            if not leave_record:
                raise HTTPException(status.HTTP_409_CONFLICT, f"Approved {leave_side}-half leave is required to record this leave status.")
            if half_statuses[leave_side] == "Medical Leave" and not leave_record.get("medical_leave"):
                raise HTTPException(status.HTTP_409_CONFLICT, "An approved Medical Leave is required for this half.")

        present_times = [pair for pair in time_fields if pair[0] and pair[1]]
        check_in = min((item[0] for item in present_times), default=None)
        check_out = max((item[1] for item in present_times), default=None)
        if leave_record:
            half_values.update({
                "leave_request_id": str(leave_record.get("_id")),
                "leave_day_part": f"{leave_side}_half",
                "leave_type_code": leave_record.get("leave_type_code"),
                "medical_leave": bool(leave_record.get("medical_leave")),
            })
    else:
        check_in = payload.check_in_time if status_value == "Present" else None
        check_out = payload.check_out_time if status_value == "Present" else None
    if status_value == "Permission" and not (payload.permission_start_time and payload.permission_end_time):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Permission status requires permission start and end times.")
    if status_value == "Present":
        if not check_in:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Check-in time is required for this attendance status.")
        _validate_time(check_in, "Check-in")
        if check_out:
            _validate_time(check_out, "Check-out")
            if check_out < check_in:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Check-out time cannot be earlier than check-in time.")
    key = {"user_id": payload.user_id, "attendance_date": selected.isoformat()}
    previous = attendance_collection.find_one(key) or {}
    if payload.expected_updated_at is not None:
        expected = payload.expected_updated_at
        actual = previous.get("updated_at")
        if actual is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "This attendance record changed after it was loaded. Refresh the history and try again.")
        if actual.tzinfo is None:
            actual = actual.replace(tzinfo=timezone.utc)
        if expected.tzinfo is None:
            expected = expected.replace(tzinfo=timezone.utc)
        if actual != expected:
            raise HTTPException(status.HTTP_409_CONFLICT, "This attendance record was modified by someone else. Refresh the history before making another change.")
    approved_leave = _approved_leaves_for_date(selected.isoformat(), payload.user_id)
    if approved_leave and approved_leave[0].get("day_part", "full_day") == "full_day" and status_value not in {"Leave"}:
        raise HTTPException(status.HTTP_409_CONFLICT, "Approved full-day leave locks this attendance date. Cancel the leave first.")
    if status_value == "Leave":
        if not approved_leave:
            raise HTTPException(status.HTTP_409_CONFLICT, "No approved leave exists for this employee on the selected date.")
        values = _build_leave_attendance_fields(approved_leave[0], datetime.now(timezone.utc))
        values["attendance_source"] = "MANUAL"
        values["attendance_type"] = (previous or {}).get("attendance_type") or "OFFICE"
    else:
        values = {
            "status": half_values.get("status", status_value),
            "check_in_time": check_in,
            "check_out_time": check_out,
            "attendance_source": "MANUAL",
            "attendance_type": previous.get("attendance_type") or "OFFICE",
            "marked_by": str(actor["_id"]),
            "updated_at": datetime.now(timezone.utc),
            "leave_request_id": None,
            "leave_day_part": None,
            "leave_type_code": None,
        }
        values.update(half_values)
        if status_value == "Not Marked":
            values["check_in_time"] = None
            values["check_out_time"] = None
        if payload.remove_permission:
            values["approved_permission"] = None
        elif payload.permission_start_time and payload.permission_end_time:
            values["approved_permission"] = {
                "start_time": payload.permission_start_time,
                "end_time": payload.permission_end_time,
                "reason": (payload.permission_reason or "Historical attendance correction").strip(),
                "approved_by": str(actor["_id"]),
                "approved_at": datetime.now(timezone.utc),
                "source": "HISTORICAL_ATTENDANCE_EDIT",
            }
    now = datetime.now(timezone.utc)
    values["marked_by"] = str(actor["_id"])
    values["updated_at"] = now
    update_query = dict(key)
    if payload.expected_updated_at is not None and previous.get("updated_at") is not None:
        update_query["updated_at"] = previous.get("updated_at")
    try:
        update_result = attendance_collection.update_one(
            update_query,
            {
                "$set": values,
                "$setOnInsert": {"employee_id": employee_id, "created_at": now},
                **({"$unset": {field: "" for field in half_fields}} if status_value != "Half-Day Present" else {}),
            },
            upsert=not bool(previous),
        )
    except DuplicateKeyError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "This attendance record was modified concurrently. Refresh the history and try again.") from exc
    if previous and update_result.matched_count == 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "This attendance record was modified concurrently. Refresh the history and try again.")
    saved = attendance_collection.find_one(key) or {}
    permission_before = previous.get("approved_permission") or None
    permission_after = saved.get("approved_permission") or None
    before_after = {
        "status": {"before": previous.get("status") or "Not Marked", "after": saved.get("status") or status_value},
        "check_in_time": {"before": previous.get("check_in_time"), "after": saved.get("check_in_time")},
        "check_out_time": {"before": previous.get("check_out_time"), "after": saved.get("check_out_time")},
        "first_half_status": {"before": previous.get("first_half_status"), "after": saved.get("first_half_status")},
        "second_half_status": {"before": previous.get("second_half_status"), "after": saved.get("second_half_status")},
        "first_half_check_in_time": {"before": previous.get("first_half_check_in_time"), "after": saved.get("first_half_check_in_time")},
        "first_half_check_out_time": {"before": previous.get("first_half_check_out_time"), "after": saved.get("first_half_check_out_time")},
        "second_half_check_in_time": {"before": previous.get("second_half_check_in_time"), "after": saved.get("second_half_check_in_time")},
        "second_half_check_out_time": {"before": previous.get("second_half_check_out_time"), "after": saved.get("second_half_check_out_time")},
        "half_day_present": {"before": previous.get("half_day_present"), "after": saved.get("half_day_present")},
        "half_day_present_side": {"before": previous.get("half_day_present_side"), "after": saved.get("half_day_present_side")},
        "actual_working_minutes": {"before": previous.get("actual_working_minutes"), "after": saved.get("actual_working_minutes")},
        "attendance_source": {"before": previous.get("attendance_source"), "after": saved.get("attendance_source")},
        "permission": {"before": permission_before, "after": permission_after},
    }
    changes = {name: values_pair for name, values_pair in before_after.items() if values_pair["before"] != values_pair["after"]}
    if not changes:
        return {"message": "No attendance changes were detected.", "record": _to_response(saved, employee), "changed": False}
    modification_id = secrets.token_hex(12)
    actor_name = actor.get("name") or actor.get("work_email") or "HR Admin"
    db["attendance_modification_history"].insert_one({
        "modification_id": modification_id,
        "user_id": payload.user_id,
        "employee_id": employee_id,
        "employee_name": employee.get("name", ""),
        "attendance_date": selected.isoformat(),
        "changes": changes,
        "reason": payload.modification_reason.strip(),
        "modified_by": str(actor["_id"]),
        "modified_by_name": actor_name,
        "modified_by_role": str(actor.get("role") or ""),
        "modified_at": now,
    })
    audit_logs_collection.insert_one({
        "user_id": str(actor["_id"]),
        "action": "attendance.historical_modified",
        "old_value": {"user_id": payload.user_id, "attendance_date": selected.isoformat(), "changes": {k: v["before"] for k, v in changes.items()}},
        "new_value": {"user_id": payload.user_id, "attendance_date": selected.isoformat(), "changes": {k: v["after"] for k, v in changes.items()}, "reason": payload.modification_reason.strip()},
        "timestamp": now,
    })
    try:
        from app.notifications import create_system_notification
        from zoneinfo import ZoneInfo
        modified_at_ist = now.astimezone(ZoneInfo("Asia/Kolkata")).strftime("%d-%m-%Y %I:%M:%S %p IST")
        def display(value):
            if value is None or value == "":
                return "—"
            if isinstance(value, dict):
                return f"{value.get('start_time', '—')}–{value.get('end_time', '—')}"
            return str(value)
        details = "\n".join(
            f"{field.replace('_', ' ').title()}: {display(pair['before'])} → {display(pair['after'])}"
            for field, pair in changes.items()
        )
        create_system_notification(
            title="Historical Attendance Modified",
            message=f"Hello {employee.get('name', 'Employee')}, your attendance for {selected.isoformat()} was modified by {actor_name}.\n{details}\nReason: {payload.modification_reason.strip()}\nModified at: {modified_at_ist}",
            validity=timedelta(days=90),
            event_key=f"attendance-historical-modify:{modification_id}",
            user_ids=[payload.user_id],
        )
    except Exception:
        logger.exception("Historical attendance saved and audited, but employee notification failed for %s %s", payload.user_id, selected.isoformat())
    if payroll_status == "Calculated" and employee_id:
        db["monthly_payroll_records"].update_one(
            {"employee_id": employee_id, "period": selected.strftime("%Y-%m"), "status": "Calculated"},
            {"$set": {"attendance_outdated": True, "attendance_outdated_at": now, "attendance_outdated_reason": "Historical attendance was modified by HR Admin."}, "$addToSet": {"attendance_outdated_dates": selected.isoformat()}},
        )
    return {"message": "Historical attendance updated successfully.", "record": _to_response(saved, employee), "changed": True, "modification_id": modification_id}


# ---------------------------------------------------------------------------
# GET /api/attendance/my
# ---------------------------------------------------------------------------

@router.get(
    "/my",
    response_model=list[AttendanceResponse],
)
def get_my_attendance(
    year: int = Query(...),
    month: int = Query(...),
    authorization: str | None = Header(
        default=None
    ),
):
    """
    Employee's own attendance.

    Approved leave is synchronized only up to today.
    Future leave dates are never displayed as attendance.
    """

    user = _current_user(
        authorization
    )

    user_id = str(
        user["_id"]
    )

    # Joining month through the 90-day calendar window, as on My Attendance.
    validate_calendar_month(
        year,
        month,
        employee_joining_date(user),
        company_today(),
    )

    start_date = (
        f"{year:04d}-{month:02d}-01"
    )

    end_date = (
        f"{year + 1:04d}-01-01"
        if month == 12
        else f"{year:04d}-{month + 1:02d}-01"
    )

    # Last calendar day of selected month.
    last_day_of_month = (
        date.fromisoformat(
            end_date
        )
        - timedelta(days=1)
    ).isoformat()

    # Lazy-sync approved leave only until today.
    _sync_leave_for_user_range(
        user_id,
        start_date,
        last_day_of_month,
    )

    records = (
        attendance_collection.find(
            {
                "user_id": user_id,
                "attendance_date": {
                    "$gte": start_date,
                    "$lt": end_date,
                },
            }
        )
        .sort(
            "attendance_date",
            1,
        )
    )

    response_rows = []
    for record in records:
        row = _to_response(record, user)
        # Empty punch pairs are Not Marked in the employee-facing attendance view.
        # This also normalizes legacy auto-generated Absent rows without altering
        # explicit attendance records in the database.
        if not row.get("check_in_time") and not row.get("check_out_time") and str(row.get("status") or "").casefold() == "absent":
            row["status"] = "Not Marked"
        response_rows.append(row)
    return response_rows

# ---------------------------------------------------------------------------
# GET /api/attendance/employees  (HR Admin / Administrator)
# ---------------------------------------------------------------------------

@router.get("/employees")
def list_attendance_employees(
    authorization: str | None = Header(default=None),
):
    """
    Light employee list for the "Employee Attendance" picker.
    """

    _require_hr_admin(
        authorization,
        "Only HR Admin or Administrator can view employee attendance",
    )

    rows = []

    for user in users_collection.find({"is_active": True}):
        employee_id = str(user.get("employee_id") or "").strip()

        if not employee_id:
            continue

        rows.append(
            {
                "user_id": str(user["_id"]),
                "employee_id": employee_id,
                "name": user.get("name", ""),
                "email": user.get("work_email", ""),
                "role": user.get("role", ""),
            }
        )

    rows.sort(key=lambda row: row["name"].casefold())

    return rows




def _utc_iso(value: Any) -> Any:
    """Serialize datetimes as explicit-UTC ISO strings (recursively).

    PyMongo returns naive datetimes for values stored in UTC. Naive ISO strings
    are ambiguous for clients, so attach the UTC offset before sending them.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, dict):
        return {key: _utc_iso(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_utc_iso(item) for item in value]
    return value


def _department_key(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u2013", "-").replace("\u2014", "-")).strip().casefold()


def _department_directory() -> tuple[dict[str, str], dict[str, set[str]], dict[str, str]]:
    """Department master: ``({id: name}, {normalised alias: {ids}}, {any id: canonical id})``.

    Aliases cover the full name ("IT - Information Technology") and its two
    halves ("IT", "Information Technology"), because employee records store
    the department as free text, as a display name or as an id. Departments
    with the same name in several companies collapse into one entry.
    A record that has no ``is_active`` flag is treated as active.
    """
    names: dict[str, str] = {}
    canonical: dict[str, str] = {}
    by_name: dict[str, str] = {}
    aliases: dict[str, set[str]] = {}
    for record in db["departments"].find({"is_active": {"$ne": False}}):
        record_id = str(record["_id"])
        configured_id = str(record.get("department_id") or "").strip()
        name = str(record.get("department_name") or record.get("name") or "").strip()
        if not name:
            continue
        name_key = _department_key(name)
        department_id = by_name.setdefault(name_key, record_id)
        canonical[record_id] = department_id
        if configured_id:
            canonical[configured_id] = department_id
        if department_id != record_id:
            continue
        names[department_id] = name
        keys = {name_key}
        for separator in (" - ", " \u2013 ", " \u2014 "):
            if separator in name:
                head, tail = name.split(separator, 1)
                keys.update({_department_key(head), _department_key(tail)})
        for key in keys:
            if key:
                aliases.setdefault(key, set()).add(department_id)
    return names, aliases, canonical


def _resolve_department_id(
    candidates: list[Any],
    names: dict[str, str],
    aliases: dict[str, set[str]],
    canonical: dict[str, str] | None = None,
) -> str:
    """First candidate (reference ids before display names) that maps to exactly one department."""
    canonical = canonical or {}
    for raw in candidates:
        if isinstance(raw, dict):
            raw = raw.get("id") or raw.get("_id") or raw.get("department_id") or raw.get("department_name") or raw.get("name")
        text = str(raw or "").strip()
        if not text:
            continue
        if text in canonical:
            return canonical[text]
        if text in names:
            return text
        matches = aliases.get(_department_key(text), set())
        if len(matches) == 1:
            return next(iter(matches))
    return ""


def _load_department_employees(include_inactive: bool = False) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Every active employee resolved to a department, plus the full department list.

    Departments come from the Department master (Organization Setup) *and* from
    the department text on employee records. Employee Details accepts free-text
    departments, so departments that were never created in the master would
    otherwise be invisible. Those are listed under a ``name:<text>`` id.
    """
    names, aliases, canonical = _department_directory()
    users = list(users_collection.find({} if include_inactive else {"is_active": {"$ne": False}}))
    employee_codes = [str(user.get("employee_id") or "").strip() for user in users if user.get("employee_id")]
    master_by_code = {
        str(item.get("employee_id") or "").strip().casefold(): item
        for item in db["employees"].find({"employee_id": {"$in": employee_codes}})
    } if employee_codes else {}
    details_by_user = {
        str(item.get("user_id")): item
        for item in db["employee_details"].find({"user_id": {"$in": [str(user["_id"]) for user in users]}})
    } if users else {}

    departments = dict(names)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for user in users:
        user_id = str(user["_id"])
        if user_id in seen:
            continue
        seen.add(user_id)
        code = str(user.get("employee_id") or "").strip()
        master = master_by_code.get(code.casefold(), {})
        details = details_by_user.get(user_id, {})
        resolved_id = _resolve_department_id(
            [
                user.get("department_id"), details.get("department_id"), master.get("department_id"),
                user.get("department"), details.get("department"), master.get("department"),
                user.get("department_name"), details.get("department_name"), master.get("department_name"),
                user.get("department_ref"), details.get("department_ref"), master.get("department_ref"),
            ],
            names, aliases, canonical,
        )
        if not resolved_id:
            raw_text = next(
                (re.sub(r"\s+", " ", value).strip() for value in (user.get("department"), details.get("department"), master.get("department"))
                 if isinstance(value, str) and value.strip() and not ObjectId.is_valid(value.strip())),
                "",
            )
            if raw_text:
                resolved_id = f"name:{_department_key(raw_text)}"
                departments.setdefault(resolved_id, raw_text)
        joining = employee_joining_date({"date_of_joining": user.get("date_of_joining") if user.get("date_of_joining") is not None else details.get("date_of_joining")})
        rows.append({
            "id": user_id,
            "user_id": user_id,
            "employee_id": code,
            "name": str(user.get("name") or master.get("employee_name") or "").strip(),
            "email": user.get("work_email", ""),
            "role": user.get("role", ""),
            "department_id": resolved_id,
            "department_name": departments.get(resolved_id, ""),
            "joining_date": joining.isoformat() if joining else None,
            "relieving_date": next((str(user.get(key) or details.get(key) or "")[:10] for key in ("date_of_leaving", "relieving_date", "resignation_date", "last_working_date") if user.get(key) or details.get(key)), None),
            "is_active": user.get("is_active") is not False,
        })
    return departments, rows


@router.get("/departments")
def list_attendance_departments(
    authorization: str | None = Header(default=None),
):
    """Complete department list for the attendance filters (HR Admin / Administrator).

    ``/api/departments`` only returns the Department master, which can hold a
    single department while employees carry others as free text. This merges
    both so the dropdown shows every department that really exists.
    """
    _require_hr_admin(
        authorization,
        "Only HR Admin or Administrator can view employee attendance",
    )
    departments, rows = _load_department_employees()
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["department_id"]] = counts.get(row["department_id"], 0) + 1
    result = [
        {
            "id": department_id,
            "department_name": name,
            "name": name,
            "source": "master" if not department_id.startswith("name:") else "employee_records",
            "employee_count": counts.get(department_id, 0),
        }
        for department_id, name in departments.items()
    ]
    result.sort(key=lambda item: (item["department_name"].casefold(), item["id"]))
    return result


@router.get("/department-employees")
def list_department_employees(
    department_id: str = Query(default="all"),
    search: str | None = Query(default=None),
    authorization: str | None = Header(default=None),
):
    """Active employees, each resolved to a department id, optionally for one department."""
    _require_hr_admin(
        authorization,
        "Only HR Admin or Administrator can view employee attendance",
    )
    departments, rows = _load_department_employees()
    wanted = (department_id or "all").strip()
    if wanted.lower() != "all" and wanted not in departments:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Department not found.")
    query = _department_key(search)
    result = [
        row for row in rows
        if (wanted.lower() == "all" or row["department_id"] == wanted)
        and (not query or query in _department_key(row["name"]) or query in _department_key(row["employee_id"]))
    ]
    result.sort(key=lambda row: (row["name"].casefold(), row["employee_id"]))
    return result


@router.get("/payroll-locks")
def get_attendance_payroll_locks(
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    authorization: str | None = Header(default=None),
):
    """Return employee-specific payroll statuses for the selected attendance month."""
    _require_hr_admin(authorization, "Only HR Admin or Administrator can view attendance payroll locks.")
    period = f"{year:04d}-{month:02d}"
    return [
        {"employee_id": row.get("employee_id"), "status": row.get("status")}
        for row in db["monthly_payroll_records"].find({"period": period}, {"employee_id": 1, "status": 1})
        if row.get("employee_id")
    ]


# ---------------------------------------------------------------------------
# GET /api/attendance/user/{user_id}  (HR Admin / Administrator)
# ---------------------------------------------------------------------------

@router.get(
    "/user/{user_id}",
    response_model=list[AttendanceResponse],
)
def get_user_attendance(
    user_id: str,
    year: int = Query(...),
    month: int = Query(...),
    authorization: str | None = Header(default=None),
):
    """
    Monthly attendance of one employee, read-only, for HR Admin /
    Administrator. Same data and leave-sync rules as /my, so the view
    matches what the employee sees under Profile -> Attendance.
    """

    _require_hr_admin(
        authorization,
        "Only HR Admin or Administrator can view employee attendance",
    )

    if not ObjectId.is_valid(user_id):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Invalid employee",
        )

    employee = users_collection.find_one(
        {"_id": ObjectId(user_id)}
    )

    if not employee:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Employee not found",
        )

    today = company_today()

    if month < 1 or month > 12:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Invalid month",
        )

    if year < 2000 or (year, month) > (today.year, today.month):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Future months cannot be viewed",
        )

    start_date = f"{year:04d}-{month:02d}-01"

    end_date = (
        f"{year + 1:04d}-01-01"
        if month == 12
        else f"{year:04d}-{month + 1:02d}-01"
    )

    last_day_of_month = (
        date.fromisoformat(end_date) - timedelta(days=1)
    ).isoformat()

    # Lazy-sync approved leave only until today (same as /my).
    _sync_leave_for_user_range(
        user_id,
        start_date,
        last_day_of_month,
    )

    records = attendance_collection.find(
        {
            "user_id": user_id,
            "attendance_date": {
                "$gte": start_date,
                "$lt": end_date,
            },
        }
    ).sort("attendance_date", 1)

    return [_to_response(record, employee) for record in records]


# ---------------------------------------------------------------------------
# GET /api/attendance/day/{user_id}
# Shared daily attendance calculation for Employee, Team Leader and HR Admin
# ---------------------------------------------------------------------------

def _duration_minutes(start: datetime, end: datetime) -> int:
    return max(0, int((end - start).total_seconds() // 60))


def _format_duration(total_minutes: int) -> str:
    total_minutes = max(0, int(total_minutes))
    return f"{total_minutes // 60}h {total_minutes % 60:02d}m"


def _local_punch(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(COMPANY_TZ)


def _calculate_daily_attendance(
    user_id: str,
    selected_date: date,
    user: dict[str, Any],
) -> dict[str, Any]:
    date_iso = selected_date.isoformat()
    record = attendance_collection.find_one({
        "user_id": user_id,
        "attendance_date": date_iso,
    })

    start = datetime.combine(selected_date, time.min, tzinfo=COMPANY_TZ)
    end = start + timedelta(days=1)
    # For an overnight employee schedule, include the following morning's
    # punches in this attendance date. The cutoff is four hours after the
    # scheduled end so a late checkout is retained without absorbing the
    # next day's normal shift.
    scheduled = applicable_schedule(user_id, selected_date)
    schedule_start = _hhmm_minutes(scheduled.get("scheduled_work_start_time"))
    schedule_end = _hhmm_minutes(scheduled.get("scheduled_work_end_time"))
    if schedule_start is not None and schedule_end is not None and schedule_end <= schedule_start:
        end = datetime.combine(
            selected_date + timedelta(days=1),
            time(schedule_end // 60, schedule_end % 60),
            tzinfo=COMPANY_TZ,
        ) + timedelta(hours=4)
    raw_records = list(essl_raw_punches_collection.find(
        {
            "user_id": user_id,
            "punch_at": {
                "$gte": start.astimezone(timezone.utc),
                "$lt": end.astimezone(timezone.utc),
            },
        },
        {"punch_at": 1, "punch_key": 1},
    ).sort("punch_at", 1))

    punch_times: list[datetime] = []
    seen: set[datetime] = set()
    for item in raw_records:
        local_time = _local_punch(item.get("punch_at"))
        if local_time is not None and local_time not in seen:
            seen.add(local_time)
            punch_times.append(local_time)
    punch_times.sort()

    source = (record or {}).get("attendance_source")
    attendance_type = (record or {}).get("attendance_type")
    is_essl = source == "ESSL" or attendance_type == "OFFICE"
    # An HR-approved attendance-request correction replaces the day's raw
    # punches; show the approved check-in/check-out instead.
    if source in {"ATTENDANCE_REQUEST", "MANUAL", "ADMIN"}:
        # Explicit HR/manual corrections supersede imported raw biometric punches.
        punch_times = []

    # ESSL raw punches are the source of truth for office intervals.
    # For legacy/manual and WFH records, preserve their existing check-in/out.
    if not punch_times and record and record.get("attendance_source") != "WFH_FACE_GPS":
        if record.get("first_half_status") or record.get("second_half_status"):
            for side in ("first", "second"):
                if record.get(f"{side}_half_status") != "Present":
                    continue
                half_in = record.get(f"{side}_half_check_in_time")
                half_out = record.get(f"{side}_half_check_out_time")
                if not half_in or not half_out:
                    continue
                try:
                    in_hour, in_minute = map(int, str(half_in).split(":")[:2])
                    out_hour, out_minute = map(int, str(half_out).split(":")[:2])
                    punch_times.extend([
                        datetime.combine(selected_date, time(in_hour, in_minute), tzinfo=COMPANY_TZ),
                        datetime.combine(selected_date, time(out_hour, out_minute), tzinfo=COMPANY_TZ),
                    ])
                except (TypeError, ValueError):
                    continue
        check_in = record.get("check_in_time")
        check_out = record.get("check_out_time")
        if not punch_times and isinstance(check_in, str) and check_in:
            try:
                h, m = map(int, check_in.split(":")[:2])
                punch_times.append(datetime.combine(selected_date, time(h, m), tzinfo=COMPANY_TZ))
            except (ValueError, TypeError):
                pass
        if not punch_times and isinstance(check_out, str) and check_out:
            try:
                h, m = map(int, check_out.split(":")[:2])
                out_time = datetime.combine(selected_date, time(h, m), tzinfo=COMPANY_TZ)
                if punch_times and out_time < punch_times[0]:
                    out_time += timedelta(days=1)
                punch_times.append(out_time)
            except (ValueError, TypeError):
                pass
        punch_times.sort()

    now = company_now()

    # WFH attendance has explicit timestamped break events. Use those events
    # directly instead of treating its single daily check-in/out as ESSL punches.
    if record and record.get("attendance_source") == "WFH_FACE_GPS":
        def _wfh_event_time(value: Any, fallback_clock: Any = None) -> datetime | None:
            parsed = _local_punch(value)
            if parsed is not None:
                return parsed
            if isinstance(fallback_clock, str) and fallback_clock:
                try:
                    hour, minute, *rest = map(int, fallback_clock.split(":"))
                    second = rest[0] if rest else 0
                    return datetime.combine(
                        selected_date, time(hour, minute, second), tzinfo=COMPANY_TZ
                    )
                except (TypeError, ValueError):
                    return None
            return None

        first_in = _wfh_event_time(record.get("check_in_at"), record.get("check_in_time"))
        last_out = _wfh_event_time(record.get("check_out_at"), record.get("check_out_time"))
        is_today = selected_date == company_today()
        is_open = bool(first_in and last_out is None and is_today)
        end_of_day = now if is_open else last_out

        raw_breaks = record.get("wfh_breaks") or []
        normalized_breaks: list[tuple[datetime, datetime | None]] = []
        for item in raw_breaks:
            if not isinstance(item, dict):
                continue
            break_start = _wfh_event_time(item.get("started_at"), item.get("start_time"))
            break_end = _wfh_event_time(item.get("ended_at"), item.get("end_time"))
            if break_start is None or (break_end is not None and break_end <= break_start):
                continue
            if first_in and break_start < first_in:
                continue
            if last_out and break_start >= last_out:
                continue
            normalized_breaks.append((break_start, break_end))
        normalized_breaks.sort(key=lambda pair: pair[0])
        # Merge overlapping WFH break events so a duplicated/overlapping
        # interval is never subtracted from work or counted twice.
        merged_breaks: list[tuple[datetime, datetime | None]] = []
        for break_start, break_end in normalized_breaks:
            if not merged_breaks:
                merged_breaks.append((break_start, break_end))
                continue
            previous_start, previous_end = merged_breaks[-1]
            if previous_end is None or break_start <= previous_end:
                merged_end = None if previous_end is None or break_end is None else max(previous_end, break_end)
                merged_breaks[-1] = (previous_start, merged_end)
            else:
                merged_breaks.append((break_start, break_end))
        normalized_breaks = merged_breaks

        working_intervals: list[dict[str, Any]] = []
        break_intervals: list[dict[str, Any]] = []
        working_minutes = 0
        cursor = first_in
        active_break = False

        def _append_work_interval(interval_start: datetime, interval_end: datetime, status_text: str = "Completed") -> None:
            nonlocal working_minutes
            if interval_end <= interval_start:
                return
            minutes = _duration_minutes(interval_start, interval_end)
            if minutes <= 0:
                return
            working_minutes += minutes
            working_intervals.append({
                "from_time": interval_start.isoformat(),
                "to_time": interval_end.isoformat(),
                "duration_minutes": minutes,
                "duration": _format_duration(minutes),
                "status": status_text,
            })

        if cursor is not None:
            for break_start, break_end in normalized_breaks:
                if end_of_day is not None and break_start >= end_of_day:
                    continue
                if break_start > cursor:
                    _append_work_interval(cursor, break_start)
                actual_break_end = break_end
                if actual_break_end is None:
                    active_break = True
                    current_break_minutes = _duration_minutes(break_start, now) if now > break_start else 0
                    if current_break_minutes > 0:
                        break_intervals.append({
                            "name": str(item.get("name") or item.get("break_name") or item.get("type") or "Break"),
                            "from_time": break_start.isoformat(),
                            "to_time": now.isoformat(),
                            "duration_minutes": current_break_minutes,
                            "duration": _format_duration(current_break_minutes),
                            "status": "In Progress",
                        })
                    cursor = None
                    break
                if end_of_day is not None:
                    actual_break_end = min(actual_break_end, end_of_day)
                break_minutes = _duration_minutes(break_start, actual_break_end)
                if break_minutes > 0:
                    break_intervals.append({
                        "name": str(item.get("name") or item.get("break_name") or item.get("type") or "Break"),
                        "from_time": break_start.isoformat(),
                        "to_time": actual_break_end.isoformat(),
                        "duration_minutes": break_minutes,
                        "duration": _format_duration(break_minutes),
                    })
                cursor = max(cursor, actual_break_end)

            if cursor is not None and end_of_day is not None and end_of_day > cursor:
                _append_work_interval(cursor, end_of_day, "In Progress" if is_open else "Completed")

        elapsed_minutes = (
            _duration_minutes(first_in, end_of_day)
            if first_in is not None and end_of_day is not None and end_of_day > first_in
            else 0
        )
        outside_minutes = max(0, elapsed_minutes - working_minutes)
        visible_break_minutes = sum(item["duration_minutes"] for item in break_intervals)

        return {
            "user_id": user_id,
            "employee_id": user.get("employee_id", ""),
            "name": user.get("name", ""),
            "attendance_date": date_iso,
            "status": record.get("status", "Present"),
            "attendance_source": record.get("attendance_source"),
            "attendance_type": record.get("attendance_type"),
            "first_check_in": first_in.isoformat() if first_in else None,
            "last_check_out": last_out.isoformat() if last_out else None,
            "attendance_state": "In Progress" if is_open else ("Completed" if first_in else "Not Marked"),
            "working_minutes": working_minutes,
            "working_duration": _format_duration(working_minutes),
            "elapsed_minutes": elapsed_minutes,
            "total_duration_minutes": elapsed_minutes,
            "total_duration": _format_duration(elapsed_minutes),
            **_schedule_fields_for_date(user_id, selected_date),
            "outside_minutes": outside_minutes,
            "outside_duration": _format_duration(outside_minutes),
            "visible_break_minutes": max(0, elapsed_minutes - working_minutes),
            "break_minutes": max(0, elapsed_minutes - working_minutes),
            "break_duration": _format_duration(max(0, elapsed_minutes - working_minutes)),
            "working_intervals": working_intervals,
            "break_intervals": break_intervals,
            "event_count": 1 + len(normalized_breaks) * 2 + (1 if last_out else 0),
        }

    # Do not turn an incomplete historical day into a live interval.
    is_today = selected_date == company_today()
    open_interval = bool(is_today and punch_times and len(punch_times) % 2 == 1)
    first_in = punch_times[0] if punch_times else None
    last_out = punch_times[-1] if punch_times and not open_interval else None

    working_intervals: list[dict[str, Any]] = []
    break_intervals: list[dict[str, Any]] = []
    all_gap_minutes = 0
    working_minutes = 0

    for index in range(0, len(punch_times) - 1, 2):
        check_in = punch_times[index]
        check_out = punch_times[index + 1]
        if check_out <= check_in:
            continue
        minutes = _duration_minutes(check_in, check_out)
        working_minutes += minutes
        working_intervals.append({
            "from_time": check_in.isoformat(),
            "to_time": check_out.isoformat(),
            "duration_minutes": minutes,
            "duration": _format_duration(minutes),
            "status": "Completed",
        })

    # Every checkout-to-next-check-in gap is outside time. All gaps are
    # excluded from net work, but only gaps strictly greater than 10 minutes
    # are surfaced as named breaks.
    for index in range(1, len(punch_times) - 1, 2):
        check_out = punch_times[index]
        next_check_in = punch_times[index + 1]
        if next_check_in <= check_out:
            continue
        minutes = _duration_minutes(check_out, next_check_in)
        all_gap_minutes += minutes
        if minutes > 0:
            break_intervals.append({
                "name": f"Break {len(break_intervals) + 1}",
                "from_time": check_out.isoformat(),
                "to_time": next_check_in.isoformat(),
                "duration_minutes": minutes,
                "duration": _format_duration(minutes),
            })

    if open_interval and punch_times:
        current_in = punch_times[-1]
        if now > current_in:
            minutes = _duration_minutes(current_in, now)
            working_minutes += minutes
            working_intervals.append({
                "from_time": current_in.isoformat(),
                "to_time": now.isoformat(),
                "duration_minutes": minutes,
                "duration": _format_duration(minutes),
                "status": "In Progress",
            })

    elapsed_minutes = (
        _duration_minutes(first_in, now if open_interval else last_out)
        if first_in and (open_interval or last_out)
        else 0
    )
    # For a day with a closed final checkout, total outside time is elapsed
    # span minus the actual paired work intervals. For a live day, also
    # account for all known gaps; incomplete gaps are not assumed.
    if first_in and (open_interval or last_out):
        outside_minutes = max(0, elapsed_minutes - working_minutes)
    else:
        outside_minutes = all_gap_minutes

    return {
        "user_id": user_id,
        "employee_id": user.get("employee_id", ""),
        "name": user.get("name", ""),
        "attendance_date": date_iso,
        "status": (record or {}).get("status", "Not Marked"),
        "attendance_source": source,
        "attendance_type": attendance_type,
        "first_check_in": first_in.isoformat() if first_in else None,
        "last_check_out": last_out.isoformat() if last_out else None,
        "attendance_state": "In Progress" if open_interval else ("Completed" if punch_times else "Not Marked"),
        "working_minutes": working_minutes,
        "working_duration": _format_duration(working_minutes),
        "elapsed_minutes": elapsed_minutes,
        "total_duration_minutes": elapsed_minutes,
        "total_duration": _format_duration(elapsed_minutes),
        **_schedule_fields_for_date(user_id, selected_date),
        "outside_minutes": outside_minutes,
        "outside_duration": _format_duration(outside_minutes),
        "visible_break_minutes": max(0, elapsed_minutes - working_minutes),
        "break_minutes": max(0, elapsed_minutes - working_minutes),
        "break_duration": _format_duration(max(0, elapsed_minutes - working_minutes)),
        "working_intervals": working_intervals,
        "break_intervals": break_intervals,
        "event_count": len(punch_times),
    }


def refresh_automatic_attendance_calculation(user_id: str, selected_date: date) -> bool:
    """Persist the current canonical calculation for an unlocked automatic attendance day.

    Only ESSL/WFH-generated records are eligible. Manual/admin-approved records and
    finalized payroll months are intentionally immutable here.
    """
    record = attendance_collection.find_one({"user_id": user_id, "attendance_date": selected_date.isoformat()})
    if not record or record.get("attendance_source") not in {"ESSL", "WFH_FACE_GPS"} or record.get("hr_admin_modified"):
        return False
    employee_id = str(record.get("employee_id") or "")
    if not employee_id:
        user = users_collection.find_one({"_id": ObjectId(user_id)}, {"employee_id": 1}) if ObjectId.is_valid(user_id) else None
        employee_id = str((user or {}).get("employee_id") or "")
    payroll = db["monthly_payroll_records"].find_one(
        {"employee_id": employee_id, "period": selected_date.strftime("%Y-%m")}, {"status": 1}
    ) if employee_id else None
    if (payroll or {}).get("status") in {"Approved", "Processed", "Paid"}:
        return False
    employee = users_collection.find_one({"_id": ObjectId(user_id), "is_active": True}) if ObjectId.is_valid(user_id) else None
    if not employee:
        return False
    details = _calculate_daily_attendance(user_id, selected_date, employee)
    check_in = details.get("first_check_in") or record.get("check_in_time")
    check_out = details.get("last_check_out") or record.get("check_out_time")
    permission_map = _collect_permissions(
        user_id, selected_date.isoformat(), (selected_date + timedelta(days=1)).isoformat(),
        {selected_date.isoformat(): record},
    )
    permission_entries = permission_map.get(selected_date.isoformat(), [])
    permission_extra = permission_minutes_outside_total_duration(
        [entry["_interval"] for entry in permission_entries], check_in, check_out
    )
    effective_minutes = effective_working_minutes(int(details.get("working_minutes") or 0), permission_extra)
    half = classify_half_day(
        total_duration_minutes=int(details.get("elapsed_minutes") or details.get("total_duration_minutes") or 0),
        permission_intervals=[entry["_interval"] for entry in permission_entries],
        recalculated_base=_is_automatic_attendance_record(record),
        half_day_threshold_minutes=int(details.get("half_day_working_duration") or 0),
        scheduled_start=details.get("scheduled_work_start_time"),
        scheduled_end=details.get("scheduled_work_end_time"),
        check_in=check_in, check_out=check_out,
        day_completed=selected_date < company_today() or bool(check_out and details.get("attendance_state") != "In Progress"),
        base_status=record.get("status") or "Not Marked",
        leave_day_part=record.get("leave_day_part"),
        leave_is_full_day=bool(record.get("status") == "Leave" and (record.get("leave_day_part") or "full_day") == "full_day"),
        working_intervals=details.get("working_intervals") or [],
    )
    status_value = record.get("status") or "Not Marked"
    if status_value not in {"Leave", "Medical Leave", "Holiday", "Weekly Off", "Permission", "Not Applicable"}:
        _status_override = half_day_status_override(
            half,
            total_duration_minutes=int(details.get("elapsed_minutes") or details.get("total_duration_minutes") or 0),
            half_day_threshold_minutes=int(details.get("half_day_working_duration") or 0),
            check_in=check_in, check_out=check_out,
            day_completed=selected_date < company_today() or bool(check_out and details.get("attendance_state") != "In Progress"),
            current_status=status_value, recalculated=True,
        )
        if _status_override:
            status_value = _status_override
    now = datetime.now(timezone.utc)
    values = {
        "status": status_value,
        "half_day_present": half.get("half_day_present"),
        "half_day_present_side": half.get("half_day_present_side"),
        "half_day_eligibility": half.get("half_day_eligibility"),
        "first_half_status": half.get("first_half_status"),
        "second_half_status": half.get("second_half_status"),
        "actual_working_minutes": int(details.get("working_minutes") or 0),
        "half_day_working_duration": int(details.get("half_day_working_duration") or 0),
        "attendance_calculation_updated_at": now,
        "attendance_calculation_version": "current",
        "updated_at": now,
    }
    result = attendance_collection.update_one(
        {"_id": record["_id"], "attendance_source": record.get("attendance_source"),
         "hr_admin_modified": {"$ne": True}},
        {"$set": values},
    )
    return bool(result.modified_count)


@router.get("/day/{user_id}")
def get_daily_attendance_breakdown(
    user_id: str,
    attendance_date: str = Query(..., description="Attendance date in YYYY-MM-DD format"),
    authorization: str | None = Header(default=None),
):
    """Return calculated work/break intervals with role-scoped employee access."""
    requester = _current_user(authorization)
    role = str(requester.get("role", "")).strip().casefold()
    selected = _validate_date(attendance_date)
    if selected > company_today():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Future attendance dates cannot be viewed.")

    if user_id == "me":
        target = requester
    else:
        if not ObjectId.is_valid(user_id):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid employee ID.")
        target = users_collection.find_one({"_id": ObjectId(user_id), "is_active": True})
        if not target:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Employee not found.")

    target_id = str(target["_id"])
    requester_id = str(requester["_id"])
    if target_id != requester_id:
        if role in {"hr admin", "administrator"}:
            pass
        elif str(target.get("reporting_manager_id", "")) == requester_id:
            # Match the Reporting Manager module's source of truth: an account
            # with active direct reports is a reporting manager even when its
            # user role label remains "Employee" or another custom role.
            is_assigned_manager = users_collection.find_one(
                {"reporting_manager_id": requester_id, "is_active": True},
                {"_id": 1},
            ) is not None
            if not is_assigned_manager:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "You are not authorized to view this employee's attendance.")
        else:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You can view only your own attendance or authorized team members.")

    daily = _calculate_daily_attendance(target_id, selected, target)
    record = attendance_collection.find_one({"user_id": target_id, "attendance_date": selected.isoformat()}) or {}
    check_in = daily.get("first_check_in") or record.get("check_in_time")
    check_out = daily.get("last_check_out") or record.get("check_out_time")
    total_minutes = int(daily.get("working_minutes") or 0)
    permission_map = _collect_permissions(target_id, selected.isoformat(), (selected + timedelta(days=1)).isoformat(), {selected.isoformat(): record})
    permission_entries = permission_map.get(selected.isoformat(), [])
    permission_minutes = _union_minutes([entry["_interval"] for entry in permission_entries])
    permission_additional_minutes = permission_minutes_outside_total_duration(
        [entry["_interval"] for entry in permission_entries], check_in, check_out
    )
    effective_minutes = effective_working_minutes(total_minutes, permission_additional_minutes)
    day_completed = bool(selected < company_today() or (selected == company_today() and check_out and daily.get("attendance_state") != "In Progress"))
    half_day = classify_half_day(
        total_duration_minutes=int(daily.get("elapsed_minutes") or daily.get("total_duration_minutes") or 0),
        permission_intervals=[entry["_interval"] for entry in permission_entries],
        recalculated_base=_is_automatic_attendance_record(record),
        half_day_threshold_minutes=int(daily.get("half_day_working_duration") or 0),
        scheduled_start=daily.get("scheduled_work_start_time"),
        scheduled_end=daily.get("scheduled_work_end_time"),
        check_in=check_in,
        check_out=check_out,
        day_completed=day_completed,
        base_status=record.get("status") or daily.get("status") or "Not Marked",
        leave_day_part=record.get("leave_day_part"),
        leave_is_full_day=bool(record.get("status") == "Leave" and (record.get("leave_day_part") or "full_day") == "full_day"),
        working_intervals=daily.get("working_intervals") or [],
    )
    if record.get("first_half_status") and record.get("second_half_status") and _preserve_saved_half_day_status(record):
        half_day.update(combine_half_day_statuses(record["first_half_status"], record["second_half_status"]))
        half_day["half_day_eligibility"] = record.get("half_day_eligibility")
        half_day["actual_working_minutes"] = record.get("actual_working_minutes", effective_minutes)
    issue = attendance_issue(check_in, check_out, daily.get("working_intervals") or [])
    scheduled_out = daily.get("scheduled_work_end_time")
    early_going_by, late_going_by = _going_by_minutes(check_out, scheduled_out)
    _resolved_day_status = record.get("status") or daily.get("status") or "Not Marked"
    _status_override = half_day_status_override(
        half_day,
        total_duration_minutes=int(daily.get("elapsed_minutes") or daily.get("total_duration_minutes") or 0),
        half_day_threshold_minutes=int(daily.get("half_day_working_duration") or 0),
        check_in=check_in, check_out=check_out, day_completed=day_completed,
        current_status=_resolved_day_status,
        recalculated=_is_automatic_attendance_record(record),
    )
    if _status_override:
        _resolved_day_status = _status_override
    return {
        **daily,
        **half_day,
        **issue,
        "permission_total_minutes": permission_minutes,
        "permission_additional_minutes": permission_additional_minutes,
        "effective_working_minutes": effective_minutes,
        "early_going_by_minutes": early_going_by,
        "late_going_by_minutes": late_going_by,
        "early_going_by": _format_duration(early_going_by) if early_going_by is not None else "—",
        "late_going_by": _format_duration(late_going_by) if late_going_by is not None else "—",
        "status": _resolved_day_status,
        "actual_elapsed_minutes": total_minutes,
         "total_duration_minutes": int(daily.get("elapsed_minutes") or 0),
        "total_duration": _format_duration(int(daily.get("elapsed_minutes") or 0)),
        "effective_working_minutes": effective_minutes,
        "half_day_threshold_met": bool(daily.get("half_day_working_duration") and int(daily.get("elapsed_minutes") or daily.get("total_duration_minutes") or 0) >= int(daily.get("half_day_working_duration") or 0)),
    }


# ---------------------------------------------------------------------------
# Self-service attendance remarks (today only)
# ---------------------------------------------------------------------------

class AttendanceRemarkPayload(BaseModel):
    remark: str = Field(min_length=1, max_length=1000)


@router.get("/my/remarks")
def get_my_attendance_remark(
    authorization: str | None = Header(default=None),
):
    user = _current_user(authorization)
    today_iso = company_today().isoformat()
    saved = attendance_remarks_collection.find_one({
        "user_id": str(user["_id"]),
        "attendance_date": today_iso,
    })
    return {"attendance_date": today_iso, "remark": (saved or {}).get("remark", "")}


@router.put("/my/remarks")
def save_my_attendance_remark(
    payload: AttendanceRemarkPayload,
    authorization: str | None = Header(default=None),
):
    user = _current_user(authorization)
    remark = payload.remark.strip()
    if not remark:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Please enter a remark.")
    today_iso = company_today().isoformat()
    now = company_now()
    attendance_remarks_collection.update_one(
        {"user_id": str(user["_id"]), "attendance_date": today_iso},
        {"$set": {
            "user_id": str(user["_id"]),
            "attendance_date": today_iso,
            "remark": remark,
            "updated_at": now,
            "updated_by": str(user["_id"]),
        }, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    return {"attendance_date": today_iso, "remark": remark, "message": "Attendance remark saved."}


# ---------------------------------------------------------------------------
# HR Admin monthly attendance report and Excel export
# ---------------------------------------------------------------------------

def _monthly_report_rows(user_id: str, employee: dict[str, Any], year: int, month: int) -> list[dict[str, Any]]:
    """Build monthly rows from canonical attendance and self-service remarks."""
    today = company_today()
    if month < 1 or month > 12:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid month.")
    if year < 2000 or (year, month) > (today.year, today.month):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Future months cannot be viewed.")

    start_date = f"{year:04d}-{month:02d}-01"
    end_date = (date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)).isoformat()
    last_day = (date.fromisoformat(end_date) - timedelta(days=1)).isoformat()
    employee_id = str(employee.get("employee_id") or "")
    payroll = db["monthly_payroll_records"].find_one(
        {"employee_id": employee_id, "period": f"{year:04d}-{month:02d}"}, {"status": 1}
    ) if employee_id else None
    payroll_locked = (payroll or {}).get("status") in {"Approved", "Processed", "Paid"}
    if not payroll_locked:
        _sync_leave_for_user_range(user_id, start_date, last_day)

    records = list(attendance_collection.find({
        "user_id": user_id,
        "attendance_date": {"$gte": start_date, "$lt": end_date},
    }).sort("attendance_date", 1))
    remarks = list(attendance_remarks_collection.find({
        "user_id": user_id,
        "attendance_date": {"$gte": start_date, "$lt": end_date},
    }))
    record_by_date = {str(r.get("attendance_date", ""))[:10]: r for r in records}
    remark_by_date = {str(r.get("attendance_date", ""))[:10]: r.get("remark", "") for r in remarks}
    dates = sorted(set(record_by_date) | set(remark_by_date))
    rows: list[dict[str, Any]] = []
    for date_iso in dates:
        try:
            selected_date = date.fromisoformat(date_iso[:10])
        except (TypeError, ValueError):
            continue
        if selected_date > today:
            continue
        record = record_by_date.get(date_iso, {})
        details = _calculate_daily_attendance(user_id, selected_date, employee) if record else {}
        calculated_status = record.get("status") or details.get("status") or "Not Marked"
        if record and not payroll_locked and record.get("attendance_source") in {"ESSL", "WFH_FACE_GPS"} and not record.get("hr_admin_modified"):
            day_check_in = details.get("first_check_in") or record.get("check_in_time")
            day_check_out = details.get("last_check_out") or record.get("check_out_time")
            permission_map = _collect_permissions(
                user_id, date_iso, (selected_date + timedelta(days=1)).isoformat(), {date_iso: record}
            )
            permission_entries = permission_map.get(date_iso, [])
            permission_extra = permission_minutes_outside_total_duration(
                [entry["_interval"] for entry in permission_entries], day_check_in, day_check_out
            )
            effective_minutes = effective_working_minutes(int(details.get("working_minutes") or 0), permission_extra)
            half = classify_half_day(
                total_duration_minutes=int(details.get("elapsed_minutes") or details.get("total_duration_minutes") or 0),
                permission_intervals=[entry["_interval"] for entry in permission_entries],
                recalculated_base=_is_automatic_attendance_record(record),
                half_day_threshold_minutes=int(details.get("half_day_working_duration") or 0),
                scheduled_start=details.get("scheduled_work_start_time"),
                scheduled_end=details.get("scheduled_work_end_time"),
                check_in=day_check_in, check_out=day_check_out,
                day_completed=selected_date < today or bool(day_check_out and details.get("attendance_state") != "In Progress"),
                base_status=record.get("status") or "Not Marked",
                leave_day_part=record.get("leave_day_part"),
                leave_is_full_day=bool(record.get("status") == "Leave" and (record.get("leave_day_part") or "full_day") == "full_day"),
                working_intervals=details.get("working_intervals") or [],
            )
            _status_override = half_day_status_override(
                half,
                total_duration_minutes=int(details.get("elapsed_minutes") or details.get("total_duration_minutes") or 0),
                half_day_threshold_minutes=int(details.get("half_day_working_duration") or 0),
                check_in=day_check_in, check_out=day_check_out,
                day_completed=selected_date < today or bool(day_check_out and details.get("attendance_state") != "In Progress"),
                current_status=calculated_status, recalculated=True,
            )
            if _status_override:
                calculated_status = _status_override
            recalc_values = {
                "status": calculated_status,
                "half_day_present": half.get("half_day_present"),
                "half_day_present_side": half.get("half_day_present_side"),
                "half_day_eligibility": half.get("half_day_eligibility"),
                "first_half_status": half.get("first_half_status"),
                "second_half_status": half.get("second_half_status"),
                "actual_working_minutes": int(details.get("working_minutes") or 0),
                "half_day_working_duration": int(details.get("half_day_working_duration") or 0),
                "attendance_calculation_updated_at": datetime.now(timezone.utc),
                "attendance_calculation_version": "current",
            }
            attendance_collection.update_one(
                {"_id": record["_id"], "attendance_source": record.get("attendance_source"), "hr_admin_modified": {"$ne": True}},
                {"$set": recalc_values},
            )
            record.update(recalc_values)
        break_intervals = details.get("break_intervals") or []
        break_lines = []
        for item in break_intervals:
            start = item.get("from_time")
            end = item.get("to_time")
            duration = item.get("duration") or _format_duration(item.get("duration_minutes", 0))
            try:
                start_text = _local_punch(datetime.fromisoformat(start)).strftime("%I:%M %p") if start else "—"
            except (TypeError, ValueError):
                start_text = "—"
            try:
                end_text = _local_punch(datetime.fromisoformat(end)).strftime("%I:%M %p") if end else "In progress"
            except (TypeError, ValueError):
                end_text = "In progress"
            break_lines.append(f"B{len(break_lines) + 1}: {start_text} – {end_text} ({duration})")

        rows.append({
            "attendance_date": date_iso[:10],
            "status": calculated_status,
            "first_check_in": details.get("first_check_in") if record else None,
            "last_check_out": details.get("last_check_out") if record else None,
            "working_minutes": int(details.get("working_minutes") or 0),
            "working_duration": details.get("working_duration") or "0h 00m",
            "total_duration_minutes": int(details.get("elapsed_minutes") or ((details.get("working_minutes") or 0) + (details.get("outside_minutes") or 0))),
            "total_duration": _format_duration(int(details.get("elapsed_minutes") or ((details.get("working_minutes") or 0) + (details.get("outside_minutes") or 0)))),
            "working_type": details.get("working_type") or _schedule_fields_for_date(user_id, selected_date).get("working_type") or "",
            "scheduled_work_start_time": (details.get("scheduled_work_start_time") or _schedule_fields_for_date(user_id, selected_date).get("scheduled_work_start_time")),
            "scheduled_work_end_time": (details.get("scheduled_work_end_time") or _schedule_fields_for_date(user_id, selected_date).get("scheduled_work_end_time")),
            "scheduled_work_duration": int(details.get("scheduled_work_duration") or _schedule_fields_for_date(user_id, selected_date).get("scheduled_work_duration") or 0),
            "half_day_working_duration": int(details.get("half_day_working_duration") or _schedule_fields_for_date(user_id, selected_date).get("half_day_working_duration") or 0),
            "break_minutes": int(details.get("break_minutes", details.get("visible_break_minutes", 0)) or 0),
            "break_duration": details.get("break_duration") or "0h 00m",
            "break_intervals": break_intervals,
            "break_details": "\n".join(break_lines) if break_lines else "No break intervals recorded",
            "working_intervals": details.get("working_intervals") or [],
            "attendance_type": details.get("attendance_type") or record.get("attendance_type") or (
                "WFH" if record.get("attendance_source") == "WFH_FACE_GPS" else ("Office" if record else "—")
            ),
            "attendance_source": record.get("attendance_source") or "—",
            "leave_type_code": record.get("leave_type_code") or "",
            "remarks": remark_by_date.get(date_iso) or record.get("remarks") or "",
        })
    return rows


def _report_employee(user_id: str, authorization: str | None) -> dict[str, Any]:
    _require_hr_admin(
        authorization,
        "Only HR Admin or Administrator can view and export employee attendance.",
    )
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid employee ID.")
    employee = users_collection.find_one({"_id": ObjectId(user_id), "is_active": True})
    if not employee:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Active employee not found.")

    # Employee Master / Employee Details are the source of department and designation;
    # these fields are not consistently copied onto the login-user document.
    employee_master = db["employees"].find_one({"employee_id": str(employee.get("employee_id") or "")}) or {}
    employee_details = db["employee_details"].find_one({"user_id": str(employee["_id"])}) or {}
    employee["department"] = employee.get("department") or employee_master.get("department") or employee_details.get("department") or ""
    employee["designation"] = employee.get("designation") or employee_master.get("designation") or employee_details.get("designation") or ""
    return employee


# ---------------------------------------------------------------------------
# Complete monthly report: every calendar date, permissions, leave, requests
# ---------------------------------------------------------------------------

SOURCE_LABELS = {
    "ESSL": "ESSL",
    "MANUAL": "Manual",
    "ATTENDANCE_REQUEST": "Attendance Request",
    "WFH_FACE_GPS": "WFH (Face + GPS)",
    "AUTO_WEEKLY_OFF": "Automatic Weekly Off (Historical)",
    "MANUAL_WEEKLY_OFF": "Manual Weekly Off",
}


def _going_by_minutes(check_out: Any, scheduled_out: Any) -> tuple[int | None, int | None]:
    """Return mutually exclusive early/late checkout minutes; None for missing values."""
    actual = _hhmm_minutes(check_out)
    scheduled = _hhmm_minutes(scheduled_out)
    if actual is None or scheduled is None:
        return None, None
    difference = actual - scheduled
    if difference > 720:
        difference -= 1440
    elif difference < -720:
        difference += 1440
    return (abs(difference), 0) if difference < 0 else (0, difference)


def _hhmm_minutes(value: Any) -> int | None:
    raw = str(value or "").strip()
    match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::\d{2})?", raw)
    if match:
        hours, minutes = int(match.group(1)), int(match.group(2))
        return hours * 60 + minutes if hours < 24 and minutes < 60 else None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo:
            parsed = parsed.astimezone(COMPANY_TZ)
        return parsed.hour * 60 + parsed.minute
    except (TypeError, ValueError):
        return None


def _attendance_clock_hhmm(value: Any) -> str | None:
    """Return a company-local attendance clock as HH:mm, without its date."""
    if value is None or value == "":
        return None
    raw = value.isoformat() if isinstance(value, datetime) else str(value).strip()
    match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::\d{2})?", raw)
    if match:
        hours, minutes = int(match.group(1)), int(match.group(2))
        if hours < 24 and minutes < 60:
            return f"{hours:02d}:{minutes:02d}"
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(COMPANY_TZ).strftime("%H:%M")
    except (TypeError, ValueError):
        return raw


def _clock_text(value: Any) -> str:
    """'14:30' -> '02:30 PM'; ISO timestamps -> company-time clock; blank -> '—'."""
    if not value:
        return "—"
    minutes = _hhmm_minutes(value)
    if minutes is not None:
        return f"{(minutes // 60) % 12 or 12:02d}:{minutes % 60:02d} {'PM' if minutes >= 720 else 'AM'}"
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(COMPANY_TZ).strftime("%I:%M %p")
    except (TypeError, ValueError):
        return str(value)


def _permission_duration_text(total_minutes: int) -> str:
    total_minutes = max(0, int(total_minutes))
    return f"{total_minutes // 60:02d} Hours {total_minutes % 60:02d} Minutes"


def _company_date_text(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(COMPANY_TZ).strftime("%d-%b-%Y")
    return str(value or "")[:10] or "—"


def _union_minutes(intervals: list[tuple[int, int]]) -> int:
    """Total minutes covered by possibly overlapping intervals (nothing counted twice)."""
    total, current_end = 0, None
    for start, end in sorted(intervals):
        if current_end is None or start > current_end:
            total += end - start
            current_end = end
        elif end > current_end:
            total += end - current_end
            current_end = end
    return total


def _permission_entry(source: str, start: Any, end: Any, reason: Any, status_text: str) -> dict[str, Any] | None:
    start_minutes, end_minutes = _hhmm_minutes(start), _hhmm_minutes(end)
    if start_minutes is None or end_minutes is None or end_minutes <= start_minutes:
        return None
    duration = end_minutes - start_minutes
    return {
        "start_time": str(start)[:5],
        "end_time": str(end)[:5],
        "start_text": _clock_text(start),
        "end_text": _clock_text(end),
        "duration_minutes": duration,
        "duration_text": _permission_duration_text(duration),
        "reason": str(reason or "").strip(),
        "status": status_text,
        "source": source,
        "_interval": (start_minutes, end_minutes),
    }


def _collect_permissions(user_id: str, start_iso: str, end_iso: str, records: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Approved permissions per date from every place the project stores them.

    ``permission_requests`` holds the approval workflow; an approval is also
    copied onto the attendance record (only one per date), and attendance
    corrections / HR edits write ``approved_permission`` directly. Requests are
    the primary source; attendance-only permissions are added when they do not
    duplicate one already listed, so nothing is lost or counted twice.
    Pending, rejected and cancelled requests are never listed as permission.
    """
    result: dict[str, list[dict[str, Any]]] = {}
    seen: set[tuple[str, str, str]] = set()
    request_ids: set[str] = set()

    def add(day: str, entry: dict[str, Any] | None) -> None:
        if not entry:
            return
        key = (day, entry["start_time"], entry["end_time"])
        if key in seen:
            return
        seen.add(key)
        result.setdefault(day, []).append(entry)

    for item in permission_requests_collection.find({
        "user_id": user_id, "status": "approved",
        "permission_date": {"$gte": start_iso, "$lt": end_iso},
    }).sort("start_time", 1):
        request_ids.add(str(item["_id"]))
        add(str(item["permission_date"])[:10], _permission_entry(
            "Permission Request", item.get("start_time"), item.get("end_time"), item.get("reason"), "Approved"))
    for day, record in records.items():
        permission = record.get("approved_permission")
        if not isinstance(permission, dict):
            continue
        if str(permission.get("request_id") or "") in request_ids:
            continue
        origin = "Attendance Request" if permission.get("source") == "ATTENDANCE_REQUEST" else "Attendance Correction"
        add(day, _permission_entry(origin, permission.get("start_time"), permission.get("end_time"), permission.get("reason"), "Approved"))
    for entries in result.values():
        entries.sort(key=lambda entry: entry["_interval"])
    return result


def _collect_requests(user_id: str, period: str, start_iso: str, end_iso: str) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    """Pending requests per date, plus each date's latest request status.

    Read-only: requests are displayed, never applied to attendance.
    """
    from app import leave as leave_module

    pending: dict[str, list[dict[str, Any]]] = {}
    latest: dict[str, tuple[Any, str]] = {}
    seen: set[tuple[str, str, str]] = set()

    def note_status(day: str, when: Any, label: str) -> None:
        stamp = when if isinstance(when, datetime) else datetime.min
        if stamp.tzinfo is not None:
            stamp = stamp.astimezone(timezone.utc).replace(tzinfo=None)
        if day not in latest or stamp >= latest[day][0]:
            latest[day] = (stamp, label)

    for request in db["attendance_requests"].find({"user_id": user_id, "periods": period}):
        active = request.get("status") in ("pending", "processing")
        submitted = request.get("submitted_at") or request.get("created_at")
        for item in request.get("dates", []):
            day = str(item.get("attendance_date") or "")[:10]
            if not (start_iso <= day < end_iso):
                continue
            decision = item.get("decision")
            if decision == "approve":
                label = "Approved"
            elif decision == "reject":
                label = "Rejected"
            elif request.get("status") == "cancelled":
                label = "Cancelled"
            elif active:
                label = "Pending" if request.get("status") == "pending" else "Under Review"
            else:
                continue
            note_status(day, submitted, label)
            key = ("attendance", str(request["_id"]), day)
            if not active or decision or key in seen:
                continue
            seen.add(key)
            requested = item.get("requested_status") or ""
            check_in, check_out = item.get("requested_check_in_time"), item.get("requested_check_out_time")
            pending.setdefault(day, []).append({
                "request_type": "Attendance Request",
                "request_number": request.get("request_number") or "",
                "request_date": _company_date_text(submitted),
                "attendance_date": day,
                "requested_status": requested,
                "requested_check_in": _clock_text(check_in) if check_in else "",
                "requested_check_out": _clock_text(check_out) if check_out else "",
                "permission_start": _clock_text(item.get("requested_permission_start_time")) if item.get("requested_permission_start_time") else "",
                "permission_end": _clock_text(item.get("requested_permission_end_time")) if item.get("requested_permission_end_time") else "",
                "reason": (item.get("requested_permission_reason") if requested == "Permission" else None) or request.get("employee_remarks") or "",
                "status": label,
                "approver": "HR Admin",
                "approval_stage": "HR Admin Review",
            })

    approver_ids = set()
    permission_items = list(permission_requests_collection.find({
        "user_id": user_id, "permission_date": {"$gte": start_iso, "$lt": end_iso},
    }))
    for item in permission_items:
        if item.get("status") == "pending" and ObjectId.is_valid(str(item.get("approver_id") or "")):
            approver_ids.add(str(item["approver_id"]))
    approver_names = {
        str(user["_id"]): user.get("name") or ""
        for user in users_collection.find({"_id": {"$in": [ObjectId(value) for value in approver_ids]}}, {"name": 1})
    } if approver_ids else {}
    for item in permission_items:
        day = str(item.get("permission_date") or "")[:10]
        raw = str(item.get("status") or "")
        label = {"pending": "Pending", "approved": "Approved", "rejected": "Rejected", "cancelled": "Cancelled"}.get(raw, raw.title())
        note_status(day, item.get("created_at"), label)
        key = ("permission", str(item["_id"]), day)
        if raw != "pending" or key in seen:
            continue
        seen.add(key)
        stage = item.get("approval_stage") or "reporting_manager"
        stage_label = leave_module.APPROVAL_STAGES.get(stage, (None, None, stage))[2]
        pending.setdefault(day, []).append({
            "request_type": "Permission Request",
            "request_number": "",
            "request_date": _company_date_text(item.get("created_at")),
            "attendance_date": day,
            "requested_status": "Permission",
            "requested_check_in": "",
            "requested_check_out": "",
            "permission_start": _clock_text(item.get("start_time")),
            "permission_end": _clock_text(item.get("end_time")),
            "reason": item.get("reason") or "",
            "status": label,
            "approver": approver_names.get(str(item.get("approver_id") or ""), ""),
            "approval_stage": f"{stage_label} Approval",
        })
    for entries in pending.values():
        entries.sort(key=lambda entry: (entry["request_type"], entry["permission_start"], entry["request_number"]))
    return pending, {day: label for day, (_, label) in latest.items()}


def _pending_request_text(entries: list[dict[str, Any]]) -> str:
    lines = []
    for entry in entries:
        parts = [entry["request_type"] + (f" {entry['request_number']}" if entry["request_number"] else ""), f"Status: {entry['status']}"]
        if entry["requested_status"]:
            parts.append(f"Requested: {entry['requested_status']}")
        if entry["requested_check_in"] or entry["requested_check_out"]:
            parts.append(f"Check-in/out: {entry['requested_check_in'] or '—'} – {entry['requested_check_out'] or '—'}")
        if entry["permission_start"] or entry["permission_end"]:
            parts.append(f"Permission: {entry['permission_start'] or '—'} – {entry['permission_end'] or '—'}")
        if entry["reason"]:
            parts.append(f"Reason: {entry['reason']}")
        parts.append(f"Requested on: {entry['request_date']}")
        if entry["approver"] or entry["approval_stage"]:
            parts.append(f"Awaiting: {entry['approval_stage']}" + (f" ({entry['approver']})" if entry["approver"] else ""))
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def _complete_monthly_report(user_id: str, employee: dict[str, Any], year: int, month: int, legacy: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Every calendar date of the month, resolved with the existing attendance logic.

    ``_monthly_report_rows`` (unchanged) supplies working-time and break data for
    dates that have attendance. The Attendance Request calendar supplies the
    resolved status of *every* date (record, holiday, weekly off, approved
    leave, not marked, ...), so dates without a record are no longer dropped.
    """
    # Imported here: attendance_requests imports this module at load time.
    from app import leave as leave_module
    from app.attendance_requests import _build_calendar, _medical_leave_types

    legacy_rows = {row["attendance_date"]: row for row in (legacy if legacy is not None else _monthly_report_rows(user_id, employee, year, month))}
    calendar = _build_calendar(employee, year, month)
    start_iso = f"{year:04d}-{month:02d}-01"
    end_iso = (date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)).isoformat()
    period = f"{year:04d}-{month:02d}"

    records = {
        str(item.get("attendance_date", ""))[:10]: item
        for item in attendance_collection.find({"user_id": user_id, "attendance_date": {"$gte": start_iso, "$lt": end_iso}})
    }
    permissions = _collect_permissions(user_id, start_iso, end_iso, records)
    pending, request_status = _collect_requests(user_id, period, start_iso, end_iso)

    # Derive HR Admin Edited strictly from the append-only modification audit trail.
    # Older audit rows without an actor role are resolved against the current user record.
    audit_rows = list(db["attendance_modification_history"].find(
        {"user_id": user_id, "attendance_date": {"$gte": start_iso, "$lt": end_iso}},
        {"modified_by": 1, "modified_by_role": 1, "attendance_date": 1},
    ))
    actor_ids = {str(item.get("modified_by")) for item in audit_rows if item.get("modified_by")}
    actor_roles = {
        str(item["_id"]): str(item.get("role") or "")
        for item in users_collection.find(
            {"_id": {"$in": [ObjectId(value) for value in actor_ids if ObjectId.is_valid(value)]}},
            {"role": 1},
        )
    } if actor_ids else {}
    hr_admin_edited_dates = set()
    for audit in audit_rows:
        role_name = str(audit.get("modified_by_role") or actor_roles.get(str(audit.get("modified_by") or ""), "")).strip().casefold()
        if role_name == "hr admin":
            day_value = str(audit.get("attendance_date") or "")[:10]
            if day_value:
                hr_admin_edited_dates.add(day_value)

    # Medical Leave is identified from the configured leave types (code ML / leave type id),
    # never by display name, so Normal Leave is not counted as Medical Leave.
    medical_by_id = _medical_leave_types()
    medical_codes = set(medical_by_id.values()) or {leave_module.MEDICAL_LEAVE_CODE}
    leave_type_docs = list(leave_types_collection.find({}, {"code": 1, "name": 1}))
    type_name_by_code = {str(item.get("code") or "").strip().upper(): item.get("name") or "" for item in leave_type_docs}
    type_by_id = {str(item["_id"]): item for item in leave_type_docs}
    leave_request_ids = [ObjectId(str(r["leave_request_id"])) for r in records.values() if ObjectId.is_valid(str(r.get("leave_request_id") or ""))]
    leave_type_by_request = {
        str(item["_id"]): str(item.get("leave_type_id") or "")
        for item in db["leave_requests"].find({"_id": {"$in": leave_request_ids}}, {"leave_type_id": 1})
    } if leave_request_ids else {}

    def leave_details(day: dict[str, Any], record: dict[str, Any] | None) -> dict[str, Any] | None:
        upcoming = day.get("upcoming_leave") or {}
        if not ((record and (record.get("status") == "Leave" or record.get("leave_request_id"))) or upcoming):
            return None
        code = str((record or {}).get("leave_type_code") or upcoming.get("leave_type_code") or "").strip().upper()
        type_id = leave_type_by_request.get(str((record or {}).get("leave_request_id") or ""), "")
        if type_id in type_by_id and not code:
            code = str(type_by_id[type_id].get("code") or "").strip().upper()
        is_medical = bool((record or {}).get("medical_leave")) or code in medical_codes or type_id in medical_by_id
        name = type_name_by_code.get(code) or upcoming.get("leave_type_name") or ("Medical Leave" if is_medical else "")
        part = str((record or {}).get("leave_day_part") or upcoming.get("day_part") or "full_day")
        part_label = "Full Day" if part == "full_day" else part.replace("_", " ").title()
        label = f"{name} ({code})" if name and code else (name or code or "Leave")
        return {
            "leave_type_code": code,
            "leave_type_name": name,
            "leave_type": label,
            "is_medical": is_medical,
            "day_part": part,
            "day_part_label": part_label,
            "days": 1.0 if part == "full_day" else 0.5,
            "details": f"{label} · {part_label}",
        }

    rows: list[dict[str, Any]] = []
    all_permission_intervals = 0
    permission_count = 0
    medical_days = normal_days = 0.0
    for day in calendar["days"]:
        iso = day["attendance_date"]
        record = records.get(iso)
        legacy = legacy_rows.get(iso)
        selected = date.fromisoformat(iso)
        entries = permissions.get(iso, [])
        day_permission_minutes = _union_minutes([entry["_interval"] for entry in entries])
        all_permission_intervals += day_permission_minutes
        permission_count += len(entries)
        leave_info = leave_details(day, record)
        if leave_info:
            if leave_info["is_medical"]:
                medical_days += leave_info["days"]
            else:
                normal_days += leave_info["days"]
        base = legacy or {
            "attendance_date": iso,
            "first_check_in": None, "last_check_out": None,
            "working_minutes": 0, "working_duration": "0h 00m",
            "break_minutes": 0, "break_duration": "0h 00m", "break_intervals": [],
            "break_details": "No break intervals recorded",
            "attendance_type": (record or {}).get("attendance_type") or "—",
            "attendance_source": (record or {}).get("attendance_source") or "—",
            "leave_type_code": (record or {}).get("leave_type_code") or "",
            "remarks": (record or {}).get("remarks") or "",
        }
        # Use the same daily calculation for every date, including punch-only days
        # and dates without a persisted attendance document.
        day_metrics = _calculate_daily_attendance(user_id, selected, employee)
        check_in = base.get("first_check_in") or day_metrics.get("first_check_in") or day.get("check_in_time")
        check_out = base.get("last_check_out") or day_metrics.get("last_check_out") or day.get("check_out_time")
        source = base.get("attendance_source") or day_metrics.get("attendance_source") or "—"
        status_text = day.get("status_label") or day.get("status") or "Not Marked"
        if status_text == "Not Marked" and day_metrics.get("first_check_in") and day_metrics.get("last_check_out"):
            status_text = "Present"
        schedule = _schedule_fields_for_date(user_id, selected)
        total_duration_minutes = int(
            base.get("total_duration_minutes")
            if base.get("total_duration_minutes") is not None
            else day_metrics.get("elapsed_minutes") or day_metrics.get("total_duration_minutes") or 0
        )
        attendance_state = day_metrics.get("attendance_state") or "Not Marked"
        today_for_completion = company_today()
        day_completed = bool(selected < today_for_completion or (selected == today_for_completion and check_out and attendance_state != "In Progress"))
        effective_permission_minutes = permission_minutes_outside_total_duration(
            [entry["_interval"] for entry in entries], check_in, check_out
        )
        effective_minutes = effective_working_minutes(
            total_duration_minutes, effective_permission_minutes,
            permission_already_included=bool(base.get("permission_included_in_working_duration")),
        )
        half_day = classify_half_day(
            total_duration_minutes=total_duration_minutes,
            half_day_threshold_minutes=int(schedule.get("half_day_working_duration") or 0),
            permission_intervals=[entry["_interval"] for entry in entries],
            recalculated_base=_is_automatic_attendance_record(record),
            scheduled_start=schedule.get("scheduled_work_start_time"),
            scheduled_end=schedule.get("scheduled_work_end_time"),
            check_in=check_in,
            check_out=check_out,
            day_completed=day_completed,
            base_status=status_text,
            leave_day_part=(leave_info or {}).get("day_part"),
            leave_is_full_day=bool(leave_info and (leave_info.get("day_part") or "full_day") == "full_day"),
            working_intervals=(base.get("working_intervals") if legacy is not None else day_metrics.get("working_intervals")) or [],
        )
        if record and record.get("first_half_status") and record.get("second_half_status") and _preserve_saved_half_day_status(record):
            half_day.update(combine_half_day_statuses(record["first_half_status"], record["second_half_status"]))
            half_day["half_day_eligibility"] = record.get("half_day_eligibility")
            half_day["actual_working_minutes"] = record.get("actual_working_minutes", effective_minutes)
        effective_break_intervals = base.get("break_intervals") if legacy is not None else (day_metrics.get("break_intervals") or [])
        effective_break_minutes = int(base.get("break_minutes") or 0) if legacy is not None else int(day_metrics.get("break_minutes", day_metrics.get("visible_break_minutes", 0)) or 0)
        if legacy is not None:
            effective_break_details = base.get("break_details") or "No break intervals recorded"
        else:
            break_lines = []
            for index, interval in enumerate(effective_break_intervals, 1):
                break_lines.append(
                    f"B{index}: {_clock_text(interval.get('from_time'))} – {_clock_text(interval.get('to_time'))} ({interval.get('duration') or _format_duration(interval.get('duration_minutes') or 0)})"
                )
            effective_break_details = "\n".join(break_lines) if break_lines else "No break intervals recorded"
        resolved_status = status_text
        protected_statuses = {"holiday", "weekly off", "leave", "medical leave", "not applicable", "before joining"}
        if not check_in and not check_out and status_text.casefold() == "absent":
            resolved_status = "Not Marked"
            half_day["first_half_status"] = "Not Marked"
            half_day["second_half_status"] = "Not Marked"
        if status_text.casefold() not in protected_statuses and not (not check_in and not check_out and status_text.casefold() == "absent"):
            _status_override = half_day_status_override(
                half_day,
                total_duration_minutes=total_duration_minutes,
                half_day_threshold_minutes=int(schedule.get("half_day_working_duration") or 0),
                check_in=check_in, check_out=check_out, day_completed=day_completed,
                current_status=status_text,
                recalculated=_is_automatic_attendance_record(record),
            )
            if _status_override:
                resolved_status = _status_override
            # No punches means Not Marked, not Absent. Absent is assigned only
            # when a completed attendance interval falls below the half-day threshold
            # or an explicit HR/admin attendance record already says Absent.
        rows.append({
            **{key: value for key, value in base.items() if key not in {"status"}},
            **schedule,
            "working_type": schedule.get("working_type") or base.get("working_type") or "",
            "working_minutes": int(base.get("working_minutes") if legacy is not None else day_metrics.get("working_minutes") or 0),
            "working_duration": base.get("working_duration") if legacy is not None else (day_metrics.get("working_duration") or "0h 00m"),
            "break_minutes": effective_break_minutes,
            "break_duration": base.get("break_duration") if legacy is not None else (day_metrics.get("break_duration") or "0h 00m"),
            "break_intervals": effective_break_intervals,
            "break_details": effective_break_details,
             "actual_elapsed_minutes": total_duration_minutes,
            "total_duration_minutes": total_duration_minutes,
            "total_duration": _format_duration(total_duration_minutes),
            "effective_working_minutes": effective_minutes,
            "half_day_threshold_met": bool(schedule.get("half_day_working_duration") and total_duration_minutes >= int(schedule.get("half_day_working_duration") or 0)),
            "working_intervals": (base.get("working_intervals") if legacy is not None else day_metrics.get("working_intervals")) or [],
            **attendance_issue(check_in, check_out, (base.get("working_intervals") if legacy is not None else day_metrics.get("working_intervals")) or []),
            "early_going_by_minutes": _going_by_minutes(check_out, schedule.get("scheduled_work_end_time"))[0],
            "late_going_by_minutes": _going_by_minutes(check_out, schedule.get("scheduled_work_end_time"))[1],
            "early_going_by": _format_duration(_going_by_minutes(check_out, schedule.get("scheduled_work_end_time"))[0]) if _going_by_minutes(check_out, schedule.get("scheduled_work_end_time"))[0] is not None else "—",
            "late_going_by": _format_duration(_going_by_minutes(check_out, schedule.get("scheduled_work_end_time"))[1]) if _going_by_minutes(check_out, schedule.get("scheduled_work_end_time"))[1] is not None else "—",
            **half_day,
            "attendance_state": attendance_state,
            "day": selected.strftime("%A"),
            "status": resolved_status,
            "check_in_text": _clock_text(check_in),
            "check_out_text": _clock_text(check_out),
            "attendance_category": day.get("attendance_category") or "—",
            "attendance_source_label": SOURCE_LABELS.get(str(source), str(source)),
            "holiday_name": day.get("holiday_name") or "",
            "permissions": [{k: v for k, v in entry.items() if not k.startswith("_")} for entry in entries],
            "permission_total_minutes": day_permission_minutes,
            "permission_total_text": _permission_duration_text(day_permission_minutes) if entries else "",
            "leave": leave_info,
            "pending_requests": pending.get(iso, []),
            "request_status": request_status.get(iso, ""),
            "hr_admin_edited": iso in hr_admin_edited_dates,
            # Dates that existed in the original report; its metrics only cover these.
            "in_legacy": legacy is not None,
        })

    first_half_present_days = sum(1 for item in rows if str(item.get("half_day_present") or "").endswith("First Half"))
    second_half_present_days = sum(1 for item in rows if str(item.get("half_day_present") or "").endswith("Second Half"))
    half_day_present_days = sum(1 for item in rows if str(item.get("half_day_present") or "No").startswith("Yes"))
    half_status_counts = {
        "Present": 0.0, "Absent": 0.0, "Leave": 0.0,
        "Medical Leave": 0.0, "Holiday": 0.0, "Weekly Off": 0.0,
    }
    for item in rows:
        for key in ("first_half_status", "second_half_status"):
            half_status = str(item.get(key) or "").strip().casefold()
            if half_status == "leave" and item.get("leave", {}).get("is_medical"):
                half_status_counts["Medical Leave"] += 0.5
                continue
            for label in half_status_counts:
                if half_status == label.casefold():
                    half_status_counts[label] += 0.5
                    break
    return {
        "rows": rows,
        "summary": {
            "total_days": len(rows),
            "present_days": half_status_counts["Present"],
            "absent_days": half_status_counts["Absent"],
            "leave_days": half_status_counts["Leave"],
            "medical_leave_days": half_status_counts["Medical Leave"],
            "holiday_days": half_status_counts["Holiday"],
            "weekly_off_days": half_status_counts["Weekly Off"],
            "half_day_present_days": half_day_present_days,
            "first_half_present_days": first_half_present_days,
            "second_half_present_days": second_half_present_days,
            "total_working_minutes": sum(int(item.get("working_minutes") or 0) for item in rows),
            "total_working_duration": _format_duration(sum(int(item.get("working_minutes") or 0) for item in rows)),
            "total_break_minutes": sum(int(item.get("break_minutes") or 0) for item in rows),
            "total_break_duration": _format_duration(sum(int(item.get("break_minutes") or 0) for item in rows)),
            "total_elapsed_minutes": sum(int(item.get("total_duration_minutes") or 0) for item in rows),
            "total_elapsed_duration": _format_duration(sum(int(item.get("total_duration_minutes") or 0) for item in rows)),
            "permission_count": permission_count,
            "permission_total_minutes": all_permission_intervals,
            "permission_total_text": _permission_duration_text(all_permission_intervals),
            "medical_leave_days": medical_days,
            "normal_leave_days": normal_days,
            "pending_request_count": sum(len(items) for items in pending.values()),
            "hr_admin_edited_count": len(hr_admin_edited_dates),
            "hr_admin_edited_dates": sorted(hr_admin_edited_dates),
        },
    }


@router.get("/user/{user_id}/monthly-details")
def get_user_monthly_attendance_details(
    user_id: str,
    year: int = Query(...),
    month: int = Query(...),
    authorization: str | None = Header(default=None),
):
    """Monthly table data including shared working-time and break calculations."""
    employee = _report_employee(user_id, authorization)
    rows = _monthly_report_rows(user_id, employee, year, month)
    complete = _complete_monthly_report(user_id, employee, year, month, rows)
    return {
        "employee": {
            "user_id": str(employee["_id"]),
            "employee_id": employee.get("employee_id", ""),
            "name": employee.get("name", ""),
            "department": employee.get("department", ""),
            "designation": employee.get("designation", ""),
        },
        "year": year,
        "month": month,
        # Recorded dates only (unchanged, kept for existing consumers).
        "records": rows,
        # Every calendar date of the month, plus permission / leave / request details.
        "days": complete["rows"],
        "summary": complete["summary"],
    }


@router.get("/user/{user_id}/export.xlsx")
def export_user_monthly_attendance(
    user_id: str,
    year: int = Query(...),
    month: int = Query(...),
    authorization: str | None = Header(default=None),
):
    """Create a professionally formatted two-worksheet monthly attendance workbook."""
    employee = _report_employee(user_id, authorization)
    legacy_rows = _monthly_report_rows(user_id, employee, year, month)
    complete = _complete_monthly_report(user_id, employee, year, month, legacy_rows)
    # Daily sheet: every calendar date. Attendance Metrics: the original recorded rows only.
    all_rows = complete["rows"]
    rows = [row for row in all_rows if row.get("in_legacy")]
    report_summary = complete["summary"]

    month_name = date(year, month, 1).strftime("%B")
    employee_id = str(employee.get("employee_id") or user_id)
    employee_name = str(employee.get("name") or "Employee")

    workbook = Workbook()
    summary = workbook.active
    summary.title = "Monthly Summary"
    detail = workbook.create_sheet("Daily Attendance")

    navy = "17365D"
    blue = "D9EAF7"
    light_blue = "EAF2F8"
    green = "E2F0D9"
    orange = "FCE4D6"
    white = "FFFFFF"
    thin_grey = Side(style="thin", color="D9E1F2")

    summary.merge_cells("A1:E1")
    summary["A1"] = "EMPLOYEE MONTHLY ATTENDANCE REPORT"
    summary["A1"].font = Font(name="Aptos Display", size=16, bold=True, color=white)
    summary["A1"].fill = PatternFill("solid", fgColor=navy)
    summary["A1"].alignment = Alignment(horizontal="center", vertical="center")
    summary.row_dimensions[1].height = 30

    meta = [
        ("Employee Name", employee_name, "Employee ID", employee_id),
        ("Department", employee.get("department") or "—", "Designation", employee.get("designation") or "—"),
        ("Reporting Month", f"{month_name} {year}", "Generated On", company_now().strftime("%d-%b-%Y %I:%M %p")),
    ]
    for r, values in enumerate(meta, start=3):
        for c, value in enumerate(values, start=1):
            cell = summary.cell(r, c, value)
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            if c in (1, 3):
                cell.font = Font(bold=True, color=navy)
                cell.fill = PatternFill("solid", fgColor=blue)
            else:
                cell.fill = PatternFill("solid", fgColor="F7F9FB")
        summary.row_dimensions[r].height = 24

    employee_schedule = _schedule_fields_for_date(user_id, date(year, month, 1))
    schedule_text = f"{employee_schedule.get('scheduled_work_start_time') or '—'} - {employee_schedule.get('scheduled_work_end_time') or '—'}"
    working_meta = [
        (1, "Half Day Time"), (2, format_hours_minutes(employee_schedule.get("half_day_working_duration") or 0)),
        (3, "Scheduled In / Out"), (4, schedule_text), (5, "Working Type"),
        (6, employee_schedule.get("working_type") or "—"),
    ]
    for column, value in working_meta:
        cell = summary.cell(6, column, value)
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        if column in (1, 3, 5):
            cell.font = Font(bold=True, color=navy)
            cell.fill = PatternFill("solid", fgColor=blue)
        else:
            cell.fill = PatternFill("solid", fgColor="F7F9FB")
    summary.row_dimensions[6].height = 24

    counts: dict[str, int] = {}
    for row in rows:
        status_name = str(row.get("status") or "Not Marked")
        counts[status_name] = counts.get(status_name, 0) + 1
    total_work_minutes = sum(int(row.get("working_minutes") or 0) for row in rows)
    total_break_minutes = sum(int(row.get("break_minutes") or 0) for row in rows)
    summary["A7"] = "Attendance Metric"
    summary["B7"] = "Value"
    for cell in summary[7][:2]:
        cell.font = Font(bold=True, color=white)
        cell.fill = PatternFill("solid", fgColor=navy)
        cell.alignment = Alignment(horizontal="center")
    metrics = [
        ("Total Days in Month", calendar.monthrange(year, month)[1]),
        ("Recorded Attendance Days", len(rows)),
        ("Present", sum(v for k, v in counts.items() if k.casefold() == "present")),
        ("Absent", sum(v for k, v in counts.items() if k.casefold() == "absent")),
        ("Leave", sum(v for k, v in counts.items() if k.casefold() == "leave")),
        ("Medical Leave", sum(v for k, v in counts.items() if k.casefold() == "medical leave")),
        ("Holiday", sum(v for k, v in counts.items() if k.casefold() == "holiday")),
        ("Weekly Off", sum(v for k, v in counts.items() if k.casefold() == "weekly off")),
        ("Not Applicable (LOP)", sum(v for k, v in counts.items() if k.casefold() == "not applicable")),
        ("Not Marked", sum(v for k, v in counts.items() if k.casefold() == "not marked")),
        ("Total Net Working Time", _format_duration(total_work_minutes)),
        ("Total Qualifying Break Time", _format_duration(total_break_minutes)),
        ("Pending Attendance / Permission Requests", report_summary["pending_request_count"]),
    ]
    for r, (label, value) in enumerate(metrics, start=8):
        summary.cell(r, 1, label)
        summary.cell(r, 2, value)
        summary.cell(r, 1).font = Font(bold=True, color=navy)
        summary.cell(r, 1).fill = PatternFill("solid", fgColor=light_blue)
        summary.cell(r, 2).fill = PatternFill("solid", fgColor="F7F9FB")
        for cell in summary[r][:2]:
            cell.border = Border(bottom=thin_grey)
            cell.alignment = Alignment(vertical="center")
    # Four clear monthly-summary groups to the right of the attendance metrics.
    summary.merge_cells("D7:K7")
    summary["D7"] = "MONTHLY ATTENDANCE SUMMARY"
    summary["D7"].font = Font(bold=True, color=white)
    summary["D7"].fill = PatternFill("solid", fgColor=navy)
    summary["D7"].alignment = Alignment(horizontal="center", vertical="center")
    summary_groups = [
        [("Net Working Time", report_summary["total_working_duration"]),
         ("Break Time", report_summary["total_break_duration"]),
         ("Total Duration", report_summary["total_elapsed_duration"])],
        [("Present Days", report_summary["present_days"]),
         ("Absent Days", report_summary["absent_days"]),
         ("Leave Days", report_summary["leave_days"]),
         ("Holidays", report_summary["holiday_days"])],
        [("Weekly Off", report_summary["weekly_off_days"]),
         ("Medical Leave", report_summary["medical_leave_days"]),
         ("Permission Count", report_summary["permission_count"]),
         ("Permission Duration", report_summary["permission_total_text"])],
        [("Half-Day Present", report_summary["half_day_present_days"]),
         ("First Half Present", report_summary["first_half_present_days"]),
         ("Second Half Present", report_summary["second_half_present_days"])],
    ]
    for group_index, group in enumerate(summary_groups):
        group_col = 4 + group_index * 2
        for offset, (label, value) in enumerate(group, start=1):
            row_number = 7 + offset
            label_cell = summary.cell(row_number, group_col, label)
            value_cell = summary.cell(row_number, group_col + 1, value)
            label_cell.font = Font(bold=True, color=navy)
            label_cell.fill = PatternFill("solid", fgColor=light_blue)
            value_cell.fill = PatternFill("solid", fgColor="F7F9FB")
            for cell in (label_cell, value_cell):
                cell.border = Border(bottom=thin_grey)
                cell.alignment = Alignment(vertical="center", horizontal="left" if cell is label_cell else "center", wrap_text=True)
    summary.row_dimensions[8].height = 26
    summary.row_dimensions[9].height = 26
    summary.row_dimensions[10].height = 26
    summary.row_dimensions[11].height = 26
    edited_dates = report_summary.get("hr_admin_edited_dates", [])
    for col, text in ((1, "HR Admin Edited Attendance Summary"), (2, "Value")):
        cell = summary.cell(22, col, text)
        cell.font = Font(bold=True, color=white)
        cell.fill = PatternFill("solid", fgColor=navy)
        cell.alignment = Alignment(horizontal="center")
    edited_summary_rows = [
        ("Total HR Admin Edited Attendance", len(edited_dates)),
        ("HR Admin Edited Dates", ", ".join(date_text[8:10] for date_text in edited_dates) if edited_dates else "No HR Admin edited attendance dates"),
    ]
    for row_number, (label, value) in enumerate(edited_summary_rows, start=23):
        summary.cell(row_number, 1, label)
        summary.cell(row_number, 2, value)
        summary.cell(row_number, 1).font = Font(bold=True, color=navy)
        summary.cell(row_number, 1).fill = PatternFill("solid", fgColor=light_blue)
        summary.cell(row_number, 2).fill = PatternFill("solid", fgColor="F7F9FB")
        for cell in summary[row_number][:2]:
            cell.border = Border(bottom=thin_grey)
            cell.alignment = Alignment(vertical="center", wrap_text=True)
    if not rows:
        summary["A16"] = "No attendance records were found for this employee and month."
        summary["A16"].font = Font(italic=True, color="666666")

    summary.column_dimensions["A"].width = 36
    summary.column_dimensions["B"].width = 28
    summary.column_dimensions["C"].width = 20
    summary.column_dimensions["D"].width = 36
    summary.column_dimensions["E"].width = 30
    for col_letter in ("F", "H", "J"):
        summary.column_dimensions[col_letter].width = 24
    for col_letter in ("G", "I", "K"):
        summary.column_dimensions[col_letter].width = 16
    summary.freeze_panes = "A7"
    summary.sheet_view.showGridLines = False
    summary.page_setup.orientation = "portrait"
    summary.page_setup.fitToWidth = 1
    summary.sheet_properties.pageSetUpPr.fitToPage = True
    summary.print_area = f"A1:K{max(24, 7 + len(metrics))}"

    headers = [
        "Attendance Date", "Day", "Status", "First Check-In", "Last Check-Out",
        "Break Duration", "Working Intervals", "Break Details", "Net Working Duration",
        "Early Going By", "Late Going By", "Attendance Type", "Source", "Leave Type", "Remarks",
        "Permission Start Time", "Permission End Time", "Permission Duration", "Permission Reason",
        "Leave Details", "Request Status", "Pending Request Details", "HR Admin Edited",
    ]
    detail.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(headers))
    title_cell = detail.cell(1, 1, f"{employee_name} — Daily Attendance — {month_name} {year}")
    title_cell.font = Font(size=14, bold=True, color=white)
    title_cell.fill = PatternFill("solid", fgColor=navy)
    title_cell.alignment = Alignment(horizontal="center", vertical="center")
    detail.row_dimensions[1].height = 28
    for col, header in enumerate(headers, start=1):
        cell = detail.cell(3, col, header)
        cell.font = Font(bold=True, color=white)
        cell.fill = PatternFill("solid", fgColor=navy)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = Border(bottom=thin_grey)
    detail.row_dimensions[3].height = 34

    def local_clock(value: Any) -> str:
        if not value:
            return "—"
        try:
            parsed = datetime.fromisoformat(str(value))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(COMPANY_TZ).strftime("%I:%M %p")
        except (TypeError, ValueError):
            return str(value)

    for idx, row in enumerate(all_rows, start=4):
        attendance_date = date.fromisoformat(row["attendance_date"])
        permission_items = row.get("permissions", [])
        permission_duration_text = "\n".join(entry["duration_text"] for entry in permission_items) or "—"
        if permission_items:
            permission_duration_text += f"\nPermission Count: {len(permission_items)}"
        working_interval_text = "\\n".join(
            f"WI{interval_index}: {local_clock(item.get('from_time'))} - {local_clock(item.get('to_time'))} "
            f"({item.get('duration') or _format_duration(item.get('duration_minutes') or 0)})"
            for interval_index, item in enumerate((row.get("working_intervals") or []), 1)
            if item.get("from_time") or item.get("to_time")
        ) or "—"
        date_value = attendance_date.strftime("%d-%b-%Y") + (" ⚠" if row.get("has_attendance_issue") else "")
        break_details_text = "\n".join(
            line for line in str(row.get("break_details") or "No qualifying breaks recorded").splitlines()
            if "total break duration:" not in line.casefold()
        ) or "No qualifying breaks recorded"
        values = [
            date_value,
            attendance_date.strftime("%A"),
            row.get("status") or "Not Marked",
            row.get("check_in_text") or "—",
            row.get("check_out_text") or "—",
            row.get("break_duration") or "0h 00m",
            working_interval_text,
            break_details_text,
            row.get("working_duration") or "0h 00m",
            row.get("early_going_by") or "—",
            row.get("late_going_by") or "—",
            row.get("attendance_category") if row.get("attendance_category") not in (None, "—") else (
                {"WFH": "Work From Home", "OFFICE": "Office", "Office": "Office"}.get(str(row.get("attendance_type")), row.get("attendance_type") or "—")
            ),
            row.get("attendance_source_label") or row.get("attendance_source") or "—",
            (row.get("leave") or {}).get("leave_type") or "—",
            row.get("remarks") or "—",
            "\n".join(entry["start_text"] for entry in permission_items) or "—",
            "\n".join(entry["end_text"] for entry in permission_items) or "—",
            permission_duration_text,
            "\n".join(entry["reason"] or "—" for entry in permission_items) or "—",
            (row.get("leave") or {}).get("details") or "—",
            row.get("request_status") or "—",
            _pending_request_text(row.get("pending_requests", [])) or "—",
            "Yes" if row.get("hr_admin_edited") else "No",
        ]
        for col, value in enumerate(values, start=1):
            cell = detail.cell(idx, col, value)
            cell.alignment = Alignment(
                vertical="top",
                wrap_text=col in (7, 8, 15, 18, 19, 20, 22),
                horizontal="left" if col in (7, 8, 15, 19, 20, 22) else "center",
            )
            cell.border = Border(bottom=thin_grey)
            if idx % 2 == 0:
                cell.fill = PatternFill("solid", fgColor="F7F9FB")
            if col == 1:
                cell.number_format = "DD-MMM-YYYY"
            if col == 3:
                normalized = str(value).casefold()
                if normalized == "half-day present":
                    cell.fill = PatternFill("solid", fgColor="DDEBF7")
                elif normalized == "present":
                    cell.fill = PatternFill("solid", fgColor=green)
                elif normalized == "absent":
                    cell.fill = PatternFill("solid", fgColor="F4CCCC")
                elif normalized in {"leave", "medical leave"}:
                    cell.fill = PatternFill("solid", fgColor=orange)
                elif normalized == "holiday":
                    cell.fill = PatternFill("solid", fgColor=blue)
                elif normalized == "weekly off":
                    cell.fill = PatternFill("solid", fgColor="EDEDED")
                elif normalized == "not marked":
                    cell.fill = PatternFill("solid", fgColor="FFF2CC")
            if col == 21 and str(value).casefold() in {"pending", "under review"}:
                cell.fill = PatternFill("solid", fgColor="FFF2CC")
        # Size each row for BOTH the break list and the complete remark.
        # Excel does not always auto-fit row heights in generated workbooks,
        # so estimate wrapped lines using the configured column widths.
        break_text = str(row.get("break_details") or "No qualifying breaks recorded")
        remark_text = str(row.get("remarks") or "—")

        def _wrapped_line_count(value: str, width: int) -> int:
            # Count explicit newlines and estimate lines caused by wrapping.
            return sum(
                max(1, math.ceil(max(1, len(line)) / width))
                for line in value.splitlines() or [""]
            )

        estimated_lines = max(
            _wrapped_line_count(str(values[6]), 38),
            _wrapped_line_count(break_text, 22),
            _wrapped_line_count(remark_text, 43),
            _wrapped_line_count(str(values[21]), 60),
            _wrapped_line_count(str(values[17]), 26),
            len(row.get("permissions", [])),
        )
        # Keep ordinary rows compact while allowing long remarks to expand.
        detail.row_dimensions[idx].height = min(300, max(24, 15 * estimated_lines + 6))

    if all_rows:
        table = Table(displayName="MonthlyAttendance", ref=f"A3:W{3 + len(all_rows)}")
        table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showFirstColumn=False, showLastColumn=False, showRowStripes=True, showColumnStripes=False)
        detail.add_table(table)
    else:
        detail.cell(4, 1, "No attendance records were found for this employee and month.")
        detail.merge_cells("A4:W4")
        detail["A4"].font = Font(italic=True, color="666666")

    # A wider Remarks column plus wrapped text and dynamic row heights keeps
    # long employee remarks readable without truncating the exported value.
    widths = [17, 15, 18, 18, 18, 18, 38, 22, 32, 17, 17, 22, 20, 22, 45, 20, 20, 26, 30, 30, 16, 60, 18]
    for idx, width in enumerate(widths, start=1):
        detail.column_dimensions[get_column_letter(idx)].width = width
    detail.freeze_panes = "A4"
    detail.auto_filter.ref = f"A3:W{max(3, 3 + len(all_rows))}"
    detail.sheet_view.showGridLines = False
    detail.page_setup.orientation = "landscape"
    detail.page_setup.fitToWidth = 1
    detail.page_setup.fitToHeight = 0
    detail.sheet_properties.pageSetUpPr.fitToPage = True
    detail.print_title_rows = "1:3"
    detail.sheet_properties.pageSetUpPr.autoPageBreaks = False
    detail.page_margins.left = 0.25
    detail.page_margins.right = 0.25
    detail.page_margins.top = 0.5
    detail.page_margins.bottom = 0.5
    detail.print_area = f"A1:W{max(4, 3 + len(all_rows))}"

    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    safe_id = re.sub(r"[^A-Za-z0-9_-]+", "_", employee_id).strip("_") or "employee"
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", employee_name).strip("_") or "employee"
    filename = f"Attendance_{safe_name}_{safe_id}_{month_name}_{year}.xlsx"
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Multi-employee monthly attendance export (HR Admin / Administrator)
# ---------------------------------------------------------------------------

def _export_employee_directory() -> list[dict[str, Any]]:
    _, rows = _load_department_employees(include_inactive=True)
    by_id = {str(row["user_id"]): row for row in rows}
    users = list(users_collection.find({}))
    result = []
    for user in users:
        user_id = str(user.get("_id") or "")
        row = by_id.get(user_id)
        if not row:
            continue
        details = db["employee_details"].find_one({"user_id": user_id}) or {}
        master = db["employees"].find_one({"employee_id": str(user.get("employee_id") or "")}) or {}
        row = {
            **row,
            "name": str(user.get("name") or master.get("employee_name") or "").strip(),
            "employee_id": str(user.get("employee_id") or ""),
            "employee_code": str(user.get("essl_employee_id") or ""),
            "department_id": row.get("department_id") or "",
            "department_name": row.get("department_name") or str(user.get("department") or details.get("department") or ""),
            "joining_date": row.get("joining_date") or str(details.get("date_of_joining") or user.get("date_of_joining") or "")[:10] or None,
            "relieving_date": row.get("relieving_date") or next((str(details.get(key) or user.get(key) or "")[:10] for key in ("date_of_leaving", "relieving_date", "resignation_date", "last_working_date") if details.get(key) or user.get(key)), None),
            "is_active": user.get("is_active") is not False,
        }
        result.append(row)
    result.sort(key=lambda item: (str(item.get("name") or "").casefold(), str(item.get("employee_id") or "")))
    return result


def _export_month_window(year: int, month: int) -> tuple[date, date]:
    if month < 1 or month > 12 or year < 2000 or year > 2100:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Select a valid attendance month and year.")
    today = company_today()
    if (year, month) > (today.year, today.month):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Future attendance months cannot be exported.")
    start = date(year, month, 1)
    end = date(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1)
    return start, end


def _employee_month_eligibility(employee: dict[str, Any], start: date, end: date) -> tuple[bool, str]:
    joining = date.fromisoformat(str(employee["joining_date"])[:10]) if employee.get("joining_date") else None
    leaving = date.fromisoformat(str(employee["relieving_date"])[:10]) if employee.get("relieving_date") else None
    if joining and joining > end:
        return False, "Joined after the selected month."
    if leaving and leaving < start:
        return False, "Employment ended before the selected month."
    if not joining:
        # Legacy records with no joining date remain selectable; attendance is still
        # constrained by actual attendance data and existing project rules.
        return True, "Joining date not recorded."
    return True, ""


@router.get("/export-options")
def get_attendance_export_options(
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    department_id: str = Query(default="all"),
    authorization: str | None = Header(default=None),
):
    _require_hr_admin(authorization, "Only HR Admin or Administrator can export employee attendance.")
    start, end = _export_month_window(year, month)
    _, all_departments = _load_department_employees(include_inactive=True)
    employees = _export_employee_directory()
    if department_id and department_id != "all":
        employees = [item for item in employees if str(item.get("department_id") or "") == department_id]
    result = []
    for employee in employees:
        eligible, reason = _employee_month_eligibility(employee, start, end)
        result.append({
            "user_id": employee["user_id"],
            "employee_id": employee.get("employee_id") or "",
            "employee_code": employee.get("employee_code") or "",
            "name": employee.get("name") or "",
            "department_id": employee.get("department_id") or "",
            "department_name": employee.get("department_name") or "",
            "joining_date": employee.get("joining_date"),
            "relieving_date": employee.get("relieving_date"),
            "is_active": bool(employee.get("is_active")),
            "eligible": eligible,
            "eligibility_message": reason,
        })
    return {
        "year": year,
        "month": month,
        "employees": result,
        "selected_count": sum(1 for item in result if item["eligible"]),
    }




@router.post("/export.xlsx")
def export_multiple_employee_attendance(
    payload: dict[str, Any],
    authorization: str | None = Header(default=None),
):
    _require_hr_admin(authorization, "Only HR Admin or Administrator can export employee attendance.")
    try:
        year = int(payload.get("year"))
        month = int(payload.get("month"))
    except (TypeError, ValueError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Select a valid attendance month and year.")
    start, end = _export_month_window(year, month)
    employee_ids = list(dict.fromkeys(str(value).strip() for value in (payload.get("employee_ids") or []) if str(value).strip()))
    department_id = str(payload.get("department_id") or "all")
    if not employee_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Select at least one eligible employee.")
    if len(employee_ids) > 500:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Select no more than 500 employees per export.")
    if any(not ObjectId.is_valid(value) for value in employee_ids):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "One or more selected employee IDs are invalid.")

    directory = {item["user_id"]: item for item in _export_employee_directory()}
    _, dept_rows = _load_department_employees(include_inactive=True)
    dept_by_user = {str(item["user_id"]): item.get("department_id") or "" for item in dept_rows}
    selected = []
    for user_id in employee_ids:
        employee_info = directory.get(user_id)
        if not employee_info:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Employee {user_id} was not found.")
        eligible, reason = _employee_month_eligibility(employee_info, start, end)
        if not eligible:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{employee_info.get('name') or user_id}: {reason}")
        if department_id != "all" and dept_by_user.get(user_id) != department_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{employee_info.get('name') or user_id} does not belong to the selected department.")
        user = users_collection.find_one({"_id": ObjectId(user_id)})
        if not user:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Employee {user_id} was not found.")
        master = db["employees"].find_one({"employee_id": str(user.get("employee_id") or "")}) or {}
        details = db["employee_details"].find_one({"user_id": user_id}) or {}
        employee = {
            **user,
            "department": employee_info.get("department_name") or details.get("department") or master.get("department") or "",
            "designation": details.get("designation") or master.get("designation") or user.get("designation") or "",
            "date_of_joining": employee_info.get("joining_date"),
            "date_of_leaving": employee_info.get("relieving_date"),
        }
        selected.append((employee_info, employee, details))

    month_name = date(year, month, 1).strftime("%B")
    company = db["companies"].find_one({"is_active": {"$ne": False}}, {"company_name": 1, "name": 1}) or {}
    company_name = company.get("company_name") or company.get("name") or "HRMS"
    generated_on = company_now().strftime("%d-%b-%Y %I:%M %p")

    employee_reports = []
    for info, employee, working_details in selected:
        legacy_rows = _monthly_report_rows(str(employee["_id"]), employee, year, month)
        complete = _complete_monthly_report(str(employee["_id"]), employee, year, month, legacy_rows)
        rows = complete["rows"]
        joining = date.fromisoformat(str(info["joining_date"])[:10]) if info.get("joining_date") else None
        leaving = date.fromisoformat(str(info["relieving_date"])[:10]) if info.get("relieving_date") else None
        rows = [
            item for item in rows
            if (not joining or date.fromisoformat(item["attendance_date"]) >= joining)
            and (not leaving or date.fromisoformat(item["attendance_date"]) <= leaving)
        ]
        employee_reports.append({"info": info, "employee": employee, "working_details": working_details, "rows": rows})

    output = build_multi_employee_attendance_workbook(
        employee_reports,
        company_name=company_name,
        month=month,
        year=year,
        generated_on=generated_on,
    )
    filename = f"Daily_Attendance_Report_{month_name}_{year}.xlsx"
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
