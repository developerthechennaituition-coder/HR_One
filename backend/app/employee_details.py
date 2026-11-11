from datetime import date, datetime, timezone
import re

from bson import ObjectId
from fastapi import APIRouter, Header, HTTPException, Query, status
from pydantic import BaseModel, field_validator, model_validator

from app.db.mongodb import db


router = APIRouter(
    prefix="/api/employee-details",
    tags=["Employee Master"],
)

users_collection = db["users"]
employee_details_collection = db["employee_details"]


# ============================================================
# SERIALIZATION HELPERS
# ============================================================

def serialize_date(value):
    if value is None:
        return None

    if isinstance(value, datetime):
        return value.date().isoformat()

    if isinstance(value, date):
        return value.isoformat()

    return str(value)


def serialize_datetime(value):
    if value is None:
        return None

    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()

    return str(value)


def serialize_user(user: dict) -> dict:
    """User Management data. Employee Master never edits these fields."""
    return {
        "id": str(user["_id"]),
        "employee_id": user.get("employee_id", ""),
        "name": user.get("name", ""),
        "work_email": user.get("work_email", ""),
        "country": user.get("country", ""),
        "country_iso": user.get("country_iso"),
        "country_code": user.get("country_code"),
        "mobile": user.get("mobile", ""),
        "role": user.get("role", ""),
        "is_active": bool(user.get("is_active", False)),
        "created_at": serialize_datetime(user.get("created_at")),
        "last_login": serialize_datetime(user.get("last_login")),
        "login_count": int(user.get("login_count", 0) or 0),
    }


def serialize_details(record: dict | None) -> dict:
    if not record:
        return {
            "date_of_birth": None,
            "blood_group": "",
            "date_of_joining": None,
            "work_stage": "Employee",
            "work_field": "",
            "nationality": "",
            "address": "",
            "gender": "",
            "marital_status": "",
            "religion": "",
            "department": "",
            "designation": "",
            "city": "",
            "state": "",
            "country": "",
            "working_type": "",
            "scheduled_work_start_time": "",
            "scheduled_work_end_time": "",
            "scheduled_work_duration": 0,
            "half_day_working_duration": 0,
            "schedule_effective_from": "",
            "working_schedule_starting_date": "",
            "working_schedule_history": [],
        }

    return {
        "date_of_birth": serialize_date(record.get("date_of_birth")),
        "blood_group": record.get("blood_group") or "",
        "date_of_joining": serialize_date(record.get("date_of_joining")),
        "work_stage": record.get("work_stage") if record.get("work_stage") in {"Probation", "Employee"} else "Employee",
        "work_field": record.get("work_field") or "",
        "nationality": record.get("nationality") or "",
        "address": record.get("address") or "",
        "gender": record.get("gender") or "",
        "marital_status": record.get("marital_status") or "",
        "religion": record.get("religion") or "",
        "department": record.get("department") or "",
        "designation": record.get("designation") or "",
        "city": record.get("city") or "",
        "state": record.get("state") or "",
        "country": record.get("country") or "",
        "working_type": record.get("working_type") or "",
        "scheduled_work_start_time": record.get("scheduled_work_start_time") or "",
        "scheduled_work_end_time": record.get("scheduled_work_end_time") or "",
        "scheduled_work_duration": int(record.get("scheduled_work_duration") or 0),
        "half_day_working_duration": int(record.get("half_day_working_duration") or 0),
        "schedule_effective_from": serialize_date(record.get("schedule_effective_from")) or "",
        "working_schedule_starting_date": serialize_date(record.get("schedule_effective_from")) or "",
        "working_schedule_history": record.get("working_schedule_history") or [],
    }


def serialize_record(user: dict) -> dict:
    user_id = str(user["_id"])
    details = employee_details_collection.find_one({"user_id": user_id})

    return {
        "user": serialize_user(user),
        "details": serialize_details(details),
    }


# ============================================================
# AUTHENTICATION / PERMISSION
# ============================================================

def get_authenticated_user(
    authorization: str | None = None,
    x_user_id: str | None = None,
) -> dict:
    """Use the same active_sessions dictionary as main.py."""

    # Import at request time to avoid a module-import circular dependency.
    from app.main import active_sessions

    token = None

    if isinstance(authorization, str):
        authorization = authorization.strip()
        if authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()

    session_user_id = active_sessions.get(token) if token else None

    # Keep compatibility with the existing HRMS x_user_id fallback.
    if not session_user_id and x_user_id:
        session_user_id = x_user_id

    if not session_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    return get_user_by_id(str(session_user_id))


def is_hr_admin(user: dict) -> bool:
    return (
        str(user.get("role", ""))
        .strip()
        .lower()
        == "hr admin"
    )


# ============================================================
# USER LOOKUP
# ============================================================

def get_user_by_id(user_id: str) -> dict:
    if not ObjectId.is_valid(user_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid user ID",
        )

    user = users_collection.find_one({"_id": ObjectId(user_id)})

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return user


# ============================================================
# REQUEST MODEL
# ============================================================

class EmployeeExtraDetailsPayload(BaseModel):
    date_of_birth: date | None = None
    blood_group: str = ""
    date_of_joining: date | None = None
    working_type: str = ""
    scheduled_work_start_time: str = ""
    scheduled_work_end_time: str = ""
    working_schedule_starting_date: date | None = None
    working_details_update: bool | None = None
    work_stage: str = "Employee"
    work_field: str = ""
    nationality: str = ""
    address: str = ""
    gender: str = ""
    marital_status: str = ""
    religion: str = ""
    department: str = ""
    designation: str = ""
    city: str = ""
    state: str = ""
    country: str = ""

    @model_validator(mode="after")
    def validate_work_schedule(self):
        self.working_type = (self.working_type or "").strip()
        if self.working_type and self.working_type not in {"Full Time", "Part Time"}:
            raise ValueError("Working Type must be Full Time or Part Time.")
        start = (self.scheduled_work_start_time or "").strip()
        end = (self.scheduled_work_end_time or "").strip()
        if bool(start) != bool(end):
            raise ValueError("Scheduled Work Start Time and End Time must both be provided.")
        if self.working_type and not (start and end):
            raise ValueError("Scheduled Work Start Time and End Time are required when Working Type is selected.")
        if (start or end) and not self.working_type:
            raise ValueError("Select a Working Type before entering scheduled work times.")
        if start and end:
            from app.working_details import clock_minutes, schedule_duration
            if clock_minutes(start) is None or clock_minutes(end) is None:
                raise ValueError("Scheduled work times must use HH:MM format.")
            if schedule_duration(start, end) <= 0:
                raise ValueError("Scheduled Work End Time must differ from Start Time.")
        self.scheduled_work_start_time = start
        self.scheduled_work_end_time = end
        return self

    @field_validator("work_stage")
    @classmethod
    def validate_work_stage(cls, value: str) -> str:
        value = (value or "Employee").strip().title()
        if value not in {"Probation", "Employee"}:
            raise ValueError("Work Stage must be Probation or Employee.")
        return value

    @field_validator("blood_group")
    @classmethod
    def validate_blood_group(cls, value: str) -> str:
        value = (value or "").strip()

        if not value:
            return ""

        # Frontend stores the exact required format: A+ve, AB-ve, etc.
        if not re.fullmatch(r"(?:A|B|AB|O)[+-]ve", value):
            raise ValueError(
                "Blood group must be A+ve, A-ve, B+ve, B-ve, AB+ve, AB-ve, O+ve or O-ve."
            )

        return value

    @field_validator(
        "work_field",
        "nationality",
        "address",
        "gender",
        "marital_status",
        "religion",
        "department",
        "designation",
        "city",
        "state",
        "country",
    )
    @classmethod
    def clean_text(cls, value: str) -> str:
        return (value or "").strip()


# ============================================================
# INDEX
# ============================================================

@router.on_event("startup")
def create_employee_details_indexes():
    employee_details_collection.create_index(
        [("user_id", 1)],
        unique=True,
    )


# ============================================================
# EMPLOYEE MASTER LIST
# ============================================================

@router.get("")
def list_employee_details(
    search: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    role: str | None = Query(default=None),
    authorization: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
):
    """Return every User Management user for Employee Master."""

    get_authenticated_user(authorization, x_user_id)

    query: dict = {}

    if search and search.strip():
        regex = {
            "$regex": search.strip(),
            "$options": "i",
        }

        query["$or"] = [
            {"name": regex},
            {"work_email": regex},
            {"employee_id": regex},
            {"mobile": regex},
        ]

    if status_filter:
        normalized = status_filter.strip().lower()

        if normalized not in {"all", "active", "inactive"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Status must be active, inactive, or all",
            )

        if normalized != "all":
            query["is_active"] = normalized == "active"

    if role and role.strip() and role.strip().lower() != "all":
        query["role"] = role.strip()

    users = users_collection.find(query).sort("created_at", -1)

    return [serialize_record(user) for user in users]


# ============================================================
# GET ONE EMPLOYEE
# ============================================================

@router.get("/{user_id}")
def get_employee_details(
    user_id: str,
    authorization: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
):
    """Return User Management data + Employee Master extra data."""

    get_authenticated_user(authorization, x_user_id)

    user = get_user_by_id(user_id)
    return serialize_record(user)


# ============================================================
# CREATE / UPDATE EXTRA DETAILS
# ============================================================

@router.put("/{user_id}")
def save_employee_details(
    user_id: str,
    payload: EmployeeExtraDetailsPayload,
    authorization: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
):
    """Only HR Admin can create/update Employee Master extra details."""

    current_user = get_authenticated_user(
        authorization,
        x_user_id,
    )

    if not is_hr_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only HR Admin can create or edit Employee Master details.",
        )

    target_user = get_user_by_id(user_id)
    now = datetime.now(timezone.utc)

    values = payload.model_dump(mode="json")
    working_details_update = values.pop("working_details_update", None)
    values.pop("working_schedule_starting_date", None)
    from app.working_details import record_schedule_change
    existing_details = employee_details_collection.find_one({"user_id": str(target_user["_id"])}) or {}
    from app.attendance import company_today

    has_working_schedule = bool(
        payload.working_type or payload.scheduled_work_start_time or payload.scheduled_work_end_time
    )
    schedule_effective_date = payload.working_schedule_starting_date
    if has_working_schedule and schedule_effective_date is None:
        # Backward compatibility for older API clients: retain the current
        # effective date when editing an existing schedule; otherwise use today.
        schedule_effective_date = (
            date.fromisoformat(str(existing_details.get("schedule_effective_from"))[:10])
            if existing_details.get("schedule_effective_from")
            else company_today()
        )
    old_schedule_values = (
        existing_details.get("working_type") or "",
        existing_details.get("scheduled_work_start_time") or "",
        existing_details.get("scheduled_work_end_time") or "",
    )
    new_schedule_values = (
        values.get("working_type") or "",
        values.get("scheduled_work_start_time") or "",
        values.get("scheduled_work_end_time") or "",
    )
    should_update_schedule = (
        working_details_update is True
        or (working_details_update is None and old_schedule_values != new_schedule_values)
    )
    payroll_schedule_notice = None
    if schedule_effective_date is not None and should_update_schedule:
        joining_date_value = (
            payload.date_of_joining
            or target_user.get("joining_date")
            or target_user.get("date_of_joining")
            or existing_details.get("date_of_joining")
            or existing_details.get("joining_date")
        )
        from app.working_details import _as_date
        joining_date = _as_date(joining_date_value)
        if joining_date and schedule_effective_date < joining_date:
            raise HTTPException(
                status_code=422,
                detail="Working Details Starting Date cannot be earlier than the Date of Joining.",
            )

        # A schedule effective-date change must not rewrite any month already
        # approved, processed, or paid in payroll. Check the full date span
        # whose effective schedule would change, including the selected month.
        employee_id = str(target_user.get("employee_id") or "").strip()
        if not employee_id:
            employee_master = db["employees"].find_one(
                {"user_id": str(target_user["_id"])}, {"employee_id": 1}
            ) or {}
            employee_id = str(employee_master.get("employee_id") or "").strip()
        old_effective_date = _as_date(existing_details.get("schedule_effective_from"))
        affected_start = min(old_effective_date, schedule_effective_date) if old_effective_date else schedule_effective_date
        # A schedule applies forward until the next effective-dated schedule.
        # Include that full interval so a change cannot alter an approved future
        # payroll month indirectly.
        later_schedule_dates = [
            parsed for item in (existing_details.get("working_schedule_history") or [])
            if isinstance(item, dict)
            for parsed in [_as_date(item.get("effective_from"))]
            if parsed and parsed > schedule_effective_date
        ]
        affected_end = min(later_schedule_dates) - __import__("datetime").timedelta(days=1) if later_schedule_dates else date.max
        if employee_id:
            locked_payrolls = db["monthly_payroll_records"].find({
                "employee_id": employee_id,
                "status": {"$regex": "^(approved|processed|paid)$", "$options": "i"},
            }, {"period": 1, "month": 1, "year": 1, "status": 1})
            for payroll_row in locked_payrolls:
                period = str(payroll_row.get("period") or "").strip()
                try:
                    period_year, period_month = (int(part) for part in period[:7].split("-"))
                    period_start = date(period_year, period_month, 1)
                    from calendar import monthrange
                    period_end = date(period_year, period_month, monthrange(period_year, period_month)[1])
                except (ValueError, TypeError):
                    continue
                if period_start <= affected_end and period_end >= affected_start:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Working Details cannot be changed because the affected date range includes {period_start.strftime('%B %Y')} payroll, which is {payroll_row.get('status')}.",
                    )

        try:
            schedule_fields = record_schedule_change(
                existing_details,
                values,
                schedule_effective_date,
                joining_date=(
                    joining_date_value
                ),
                force_effective_date=(
                    payload.working_schedule_starting_date is not None
                    and str(payload.working_schedule_starting_date)
                    != str(existing_details.get("schedule_effective_from") or "")[:10]
                ),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        values.update(schedule_fields)
    else:
        # Do not alter a previously configured schedule when this save did not
        # intentionally edit Working Details.
        for key in (
            "working_type", "scheduled_work_start_time", "scheduled_work_end_time",
            "scheduled_work_duration", "half_day_working_duration",
            "schedule_effective_from", "working_schedule_history",
        ):
            values.pop(key, None)
    values["updated_at"] = now

    employee_details_collection.update_one(
        {"user_id": str(target_user["_id"])},
        {
            "$set": values,
            "$setOnInsert": {
                "user_id": str(target_user["_id"]),
                "created_at": now,
            },
        },
        upsert=True,
    )

    saved = employee_details_collection.find_one(
        {"user_id": str(target_user["_id"])}
    )

    return {
        "message": "Employee details saved successfully.",
        "user": serialize_user(target_user),
        "details": serialize_details(saved),
        "schedule_effective_date_notice": payroll_schedule_notice,
    }
