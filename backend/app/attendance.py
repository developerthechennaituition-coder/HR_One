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
from dotenv import load_dotenv
from pymongo import ReturnDocument
from pydantic import BaseModel, Field, field_validator
from pymongo.errors import DuplicateKeyError
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.table import Table, TableStyleInfo

from app.db.mongodb import db
from app.session import active_sessions

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
    outside_minutes: int | None = None
    outside_duration: str | None = None
    visible_break_minutes: int | None = None
    break_duration: str | None = None
    working_intervals: list[dict[str, Any]] | None = None
    break_intervals: list[dict[str, Any]] | None = None
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
        },
    ) or {}

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
        "check_in_time": record.get(
            "check_in_time"
        ),
        "check_out_time": record.get(
            "check_out_time"
        ),
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
        "attendance_source": record.get("attendance_source"),
        "attendance_type": record.get("attendance_type"),
        "first_punch": record.get("first_punch"),
        "last_punch": record.get("last_punch"),
        "last_essl_sync_at": record.get("last_essl_sync_at"),
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


def _require_wfh_employee(authorization: str | None) -> dict[str, Any]:
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
    if user.get("is_wfh_enabled") is not True:
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
    user = _require_wfh_employee(authorization)
    if purpose is not None:
        _assert_wfh_email_scope(authorization, purpose)
        _assert_wfh_action_allowed(user, purpose)
    else:
        _assert_wfh_email_scope(authorization)
    user_id = str(user["_id"])
    record = _today_wfh_attendance(user_id)
    return {
        "is_wfh_enabled": user.get("is_wfh_enabled") is True,
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

    if selected not in {
        today,
        today - timedelta(days=1),
    }:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Attendance can only be marked for today or yesterday",
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

    user_id = str(
        user["_id"]
    )

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

        return _to_response(
            record,
            user,
        )

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
        "attendance_type": "OFFICE",
        "updated_at": now,
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

    today = company_today()

    # Current year only.
    if year != today.year:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Only the current year can be viewed",
        )

    if month < 1 or month > 12:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Invalid month",
        )

    if month > today.month:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Future months cannot be viewed",
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

    return [
        _to_response(
            record,
            user,
        )
        for record in records
    ]

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

    # ESSL raw punches are the source of truth for office intervals.
    # For legacy/manual and WFH records, preserve their existing check-in/out.
    if not punch_times and record and record.get("attendance_source") != "WFH_FACE_GPS":
        check_in = record.get("check_in_time")
        check_out = record.get("check_out_time")
        if isinstance(check_in, str) and check_in:
            try:
                h, m = map(int, check_in.split(":")[:2])
                punch_times.append(datetime.combine(selected_date, time(h, m), tzinfo=COMPANY_TZ))
            except (ValueError, TypeError):
                pass
        if isinstance(check_out, str) and check_out:
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
                    if current_break_minutes > 10:
                        break_intervals.append({
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
                # Keep the ESSL report's existing rule: only breaks strictly
                # greater than 10 minutes appear in the named break list.
                if break_minutes > 10:
                    break_intervals.append({
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
            "outside_minutes": outside_minutes,
            "outside_duration": _format_duration(outside_minutes),
            "visible_break_minutes": visible_break_minutes,
            "break_duration": _format_duration(visible_break_minutes),
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
        if minutes > 10:
            break_intervals.append({
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
        "outside_minutes": outside_minutes,
        "outside_duration": _format_duration(outside_minutes),
        "visible_break_minutes": sum(item["duration_minutes"] for item in break_intervals),
        "break_duration": _format_duration(sum(item["duration_minutes"] for item in break_intervals)),
        "working_intervals": working_intervals,
        "break_intervals": break_intervals,
        "event_count": len(punch_times),
    }


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
            # A reporting manager may see only their direct active team members.
            if str(requester.get("role", "")).strip().casefold() not in {
                "manager", "team leader", "reporting manager", "hr manager"
            }:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "You are not authorized to view this employee's attendance.")
        else:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You can view only your own attendance or authorized team members.")

    return _calculate_daily_attendance(target_id, selected, target)


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
            break_lines.append(f"{start_text} – {end_text} ({duration})")

        rows.append({
            "attendance_date": date_iso[:10],
            "status": record.get("status") or details.get("status") or "Not Marked",
            "first_check_in": details.get("first_check_in") if record else None,
            "last_check_out": details.get("last_check_out") if record else None,
            "working_minutes": int(details.get("working_minutes") or 0),
            "working_duration": details.get("working_duration") or "0h 00m",
            "break_minutes": int(details.get("visible_break_minutes") or 0),
            "break_duration": details.get("break_duration") or "0h 00m",
            "break_intervals": break_intervals,
            "break_details": "\n".join(break_lines) if break_lines else "No qualifying breaks recorded",
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
        "records": rows,
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
    rows = _monthly_report_rows(user_id, employee, year, month)

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

    summary.merge_cells("A1:D1")
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
        ("Recorded Attendance Days", len(rows)),
        ("Present", sum(v for k, v in counts.items() if k.casefold() == "present")),
        ("Absent", sum(v for k, v in counts.items() if k.casefold() == "absent")),
        ("Leave", sum(v for k, v in counts.items() if k.casefold() == "leave")),
        ("Holiday", sum(v for k, v in counts.items() if k.casefold() == "holiday")),
        ("Total Net Working Time", _format_duration(total_work_minutes)),
        ("Total Qualifying Break Time", _format_duration(total_break_minutes)),
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
    if not rows:
        summary["A16"] = "No attendance records were found for this employee and month."
        summary["A16"].font = Font(italic=True, color="666666")

    summary.column_dimensions["A"].width = 31
    summary.column_dimensions["B"].width = 28
    summary.column_dimensions["C"].width = 20
    summary.column_dimensions["D"].width = 32
    summary.freeze_panes = "A7"
    summary.sheet_view.showGridLines = False
    summary.page_setup.orientation = "portrait"
    summary.page_setup.fitToWidth = 1
    summary.sheet_properties.pageSetUpPr.fitToPage = True
    summary.print_area = f"A1:D{max(15, 7 + len(metrics))}"

    headers = [
        "Attendance Date", "Day", "Status", "First Check-In", "Last Check-Out",
        "Break Duration", "Break Time List", "Net Working Duration",
        "Attendance Type", "Source", "Leave Type", "Remarks",
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

    for idx, row in enumerate(rows, start=4):
        attendance_date = date.fromisoformat(row["attendance_date"])
        values = [
            attendance_date,
            attendance_date.strftime("%A"),
            row.get("status") or "Not Marked",
            local_clock(row.get("first_check_in")),
            local_clock(row.get("last_check_out")),
            row.get("break_duration") or "0h 00m",
            row.get("break_details") or "No qualifying breaks recorded",
            row.get("working_duration") or "0h 00m",
            row.get("attendance_type") or "—",
            row.get("attendance_source") or "—",
            row.get("leave_type_code") or "—",
            row.get("remarks") or "—",
        ]
        for col, value in enumerate(values, start=1):
            cell = detail.cell(idx, col, value)
            cell.alignment = Alignment(
                vertical="top",
                wrap_text=col in (7, 12),
                horizontal="left" if col in (7, 12) else "center",
            )
            cell.border = Border(bottom=thin_grey)
            if idx % 2 == 0:
                cell.fill = PatternFill("solid", fgColor="F7F9FB")
            if col == 1:
                cell.number_format = "DD-MMM-YYYY"
            if col == 3:
                normalized = str(value).casefold()
                if normalized == "present":
                    cell.fill = PatternFill("solid", fgColor=green)
                elif normalized == "absent":
                    cell.fill = PatternFill("solid", fgColor="F4CCCC")
                elif normalized in {"leave", "holiday"}:
                    cell.fill = PatternFill("solid", fgColor=orange if normalized == "leave" else blue)
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
            _wrapped_line_count(break_text, 40),
            _wrapped_line_count(remark_text, 43),
        )
        # Keep ordinary rows compact while allowing long remarks to expand.
        detail.row_dimensions[idx].height = min(300, max(24, 15 * estimated_lines + 6))

    if rows:
        table = Table(displayName="MonthlyAttendance", ref=f"A3:L{3 + len(rows)}")
        table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showFirstColumn=False, showLastColumn=False, showRowStripes=True, showColumnStripes=False)
        detail.add_table(table)
    else:
        detail.cell(4, 1, "No attendance records were found for this employee and month.")
        detail.merge_cells("A4:L4")
        detail["A4"].font = Font(italic=True, color="666666")

    # A wider Remarks column plus wrapped text and dynamic row heights keeps
    # long employee remarks readable without truncating the exported value.
    widths = [17, 15, 16, 18, 18, 18, 42, 22, 22, 20, 16, 45]
    for idx, width in enumerate(widths, start=1):
        detail.column_dimensions[chr(64 + idx)].width = width
    detail.freeze_panes = "A4"
    detail.auto_filter.ref = f"A3:L{max(3, 3 + len(rows))}"
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
    detail.print_area = f"A1:L{max(4, 3 + len(rows))}"

    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    safe_id = re.sub(r"[^A-Za-z0-9_-]+", "_", employee_id).strip("_") or "employee"
    filename = f"Employee_Attendance_{safe_id}_{month_name}_{year}.xlsx"
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
