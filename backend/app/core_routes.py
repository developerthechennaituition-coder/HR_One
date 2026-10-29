"""Core organization, authentication, user, employee and holiday API routes.

This module contains the existing route handlers and their supporting schemas.
The public FastAPI application is created in ``app.main`` and includes this router.
"""

import secrets
import smtplib
import os
import logging
import threading
import hashlib
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from bson import ObjectId
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import AliasChoices, BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator
from pymongo.errors import DuplicateKeyError
from app.security import hash_password, verify_password
from app.activation import build_activation_url, hash_activation_token, send_activation_email
from app.password_reset import router as password_reset_router
from app.employee_details import router as employee_details_router
from app.reporting_manager import router as reporting_manager_router
from app.leave import router as leave_router, reroute_pending_leave_requests
from app.payroll import router as payroll_router, ensure_payroll_collections
from app.session import active_sessions
from app.religion_holidays import (
    employee_religion,
    employee_religions,
    flag_draft_payroll_for_holiday_change,
    published_religion_holidays,
    calendar_applies_to,
    calendar_user_ids,
    religion_key,
)

#Attendance
from app.attendance import router as attendance_router, ensure_attendance_indexes, employee_joining_date
from app.essl import router as essl_attendance_router, ensure_essl_indexes

# Notification
from app.notifications import (
    create_system_notification,
    router as notifications_router,
    create_notification_indexes,
)

from app.db.mongodb import (
    MONGODB_DATABASE,
    client,
    db,
    ensure_business_units_collection,
    ensure_companies_collection,
    ensure_departments_collection,
    ensure_designations_collection,
    ensure_employees_collection,
    ensure_users_collection,
    ensure_roles_collection,
    ensure_permissions_collection,
    ensure_audit_logs_collection,
    ensure_employee_holiday_calendars_collection,
    ensure_holiday_calendars_collection,
    ensure_holidays_collection,
    ensure_teams_collection,
)



router = APIRouter()
logger = logging.getLogger(__name__)
ORGANIZATION_TIMEZONE = ZoneInfo(os.getenv("ORG_TIMEZONE", "Asia/Kolkata"))

def log_event(message: str):
    """Write important HRMS events with a local timestamp for operational tracing."""
    timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{timestamp}, {message}", flush=True)

companies_collection = db["companies"]
employees_collection = db["employees"]
users_collection = db["users"]
roles_collection = db["roles"]
permissions_collection = db["permissions"]
audit_logs_collection = db["audit_logs"]
business_units_collection = db["business_units"]
departments_collection = db["departments"]
teams_collection = db["teams"]
designations_collection = db["designations"]
holiday_calendars_collection = db["holiday_calendars"]
holidays_collection = db["holidays"]
employee_holiday_calendars_collection = db["employee_holiday_calendars"]
holiday_calendar_reminders_collection = db["holiday_calendar_reminders"]
organization_audit_collection = db["organization_audit_history"]
organization_scheduler_stop = threading.Event()

# Prevent duplicate attendance for the same user on the same date
@router.on_event("startup")
def create_indexes():
    ensure_attendance_indexes()
    ensure_essl_indexes()
    create_notification_indexes()
    companies_collection.create_index(
        "organization_singleton_key",
        unique=True,
        sparse=True,
        name="companies_singleton_unique",
    )
    # One active calendar per religion and year ("" = legacy company-wide calendar).
    holiday_calendars_collection.update_many(
        {"religion_key": {"$exists": False}}, {"$set": {"religion_key": "", "religion": None}}
    )
    if "holiday_calendar_active_year_unique" in holiday_calendars_collection.index_information():
        holiday_calendars_collection.drop_index("holiday_calendar_active_year_unique")
    holiday_calendars_collection.create_index(
        [("calendar_year", 1), ("religion_key", 1)],
        unique=True,
        partialFilterExpression={"is_active": True},
        name="holiday_calendar_active_year_religion_unique",
    )
    holidays_collection.create_index(
        [("calendar_id", 1), ("holiday_date", 1), ("holiday_name", 1)],
        unique=True,
        partialFilterExpression={"is_active": True},
        name="holiday_active_calendar_date_name_unique",
    )
    try:
        # One live (non-cancelled) holiday per calendar and date.
        holidays_collection.create_index(
            [("calendar_id", 1), ("holiday_date", 1)],
            unique=True,
            partialFilterExpression={"is_active": True, "status": {"$in": ["Draft", "Published"]}},
            name="holiday_live_calendar_date_unique",
        )
    except Exception:
        logger.exception("Existing duplicate holiday dates prevent the one-holiday-per-date index.")
    employee_holiday_calendars_collection.create_index(
        [("employee_id", 1), ("calendar_id", 1)],
        unique=True,
        partialFilterExpression={"is_active": True},
        name="employee_active_calendar_assignment_unique",
    )
    holiday_calendar_reminders_collection.create_index(
        [("target_year", 1), ("period", 1)],
        unique=True,
        name="holiday_reminder_target_period_unique",
    )

@router.on_event("startup")
def initialize_database():
    ensure_payroll_collections()
    ensure_companies_collection()
    ensure_employees_collection()
    ensure_users_collection()
    ensure_roles_collection()
    ensure_permissions_collection()
    ensure_audit_logs_collection()
    ensure_business_units_collection()
    ensure_departments_collection()
    ensure_teams_collection()
    ensure_designations_collection()
    ensure_holiday_calendars_collection()
    ensure_holidays_collection()
    ensure_employee_holiday_calendars_collection()
    legacy_migration_time = datetime.now(timezone.utc)
    holiday_calendars_collection.update_many(
        {"status": {"$exists": False}},
        {
            "$set": {
                "status": "Published",
                "published_at": legacy_migration_time,
                "legacy_migrated": True,
                "country": "Not specified",
                "state": "Not specified",
            }
        },
    )
    holidays_collection.update_many(
        {"status": {"$exists": False}},
        {
            "$set": {
                "status": "Published",
                "applicability": "All Employees",
                "applicable_religions": [],
                "is_company_holiday": False,
                "legacy_migrated": True,
            }
        },
    )
    ensure_demo_user()
    _assign_legacy_company_singleton()
    _start_holiday_reminder_scheduler()


def _assign_legacy_company_singleton():
    companies = list(companies_collection.find({}).sort("created_at", 1))
    if len(companies) == 1 and not companies[0].get("organization_singleton_key"):
        companies_collection.update_one(
            {"_id": companies[0]["_id"]},
            {"$set": {"organization_singleton_key": "primary"}},
        )


def _emit_holiday_calendar_reminders(now: datetime | None = None):
    current = now or datetime.now(ORGANIZATION_TIMEZONE)
    today = current.astimezone(ORGANIZATION_TIMEZONE).date()
    if today.month == 12:
        target_year = today.year + 1
        period = min((today.day - 1) // 15, 2)
    elif today.year and today.month == 1 and today.day <= 15:
        target_year = today.year
        period = 2 if today.day < 15 else 3
    else:
        return
    if today > date(target_year, 1, 15):
        return
    if holiday_calendars_collection.find_one({"calendar_year": target_year, "is_active": True}):
        return
    period = min(period, 3)
    event_key = f"holiday-calendar-reminder:{target_year}:{period}"
    if holiday_calendar_reminders_collection.find_one(
        {"target_year": target_year, "period": period}
    ):
        return
    notification_id = create_system_notification(
        title=f"Create the {target_year} holiday calendar",
        message=f"Please create and publish the holiday calendar for {target_year}.",
        validity=timedelta(days=15),
        event_key=event_key,
        roles={"HR Admin", "hr admin"},
    )
    if notification_id is None:
        return
    now_value = now or datetime.now(timezone.utc)
    try:
        holiday_calendar_reminders_collection.insert_one(
            {
                "target_year": target_year,
                "period": period,
                "created_at": now_value,
                "expires_at": now_value + timedelta(days=15),
                "notification_id": notification_id,
                "status": "sent",
            }
        )
    except DuplicateKeyError:
        return


def _start_holiday_reminder_scheduler():
    def run_scheduler():
        while not organization_scheduler_stop.is_set():
            try:
                _emit_holiday_calendar_reminders()
            except Exception:
                logger.exception("Holiday calendar reminder job failed")
            if organization_scheduler_stop.wait(3600):
                break

    if not any(
        thread.name == "holiday-calendar-reminder-scheduler" and thread.is_alive()
        for thread in threading.enumerate()
    ):
        organization_scheduler_stop.clear()
        threading.Thread(
            target=run_scheduler,
            name="holiday-calendar-reminder-scheduler",
            daemon=True,
        ).start()


@router.on_event("shutdown")
def stop_holiday_reminder_scheduler():
    organization_scheduler_stop.set()


def _organization_user(
    authorization: str | None = Header(default=None),
) -> dict:
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    token = authorization.removeprefix("Bearer ").strip()
    user_id = active_sessions.get(token)
    if not user_id or not ObjectId.is_valid(user_id):
        raise HTTPException(status_code=401, detail="Session expired. Please login again.")
    user = users_collection.find_one({"_id": ObjectId(user_id), "is_active": True})
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def _organization_reader(user: dict = Depends(_organization_user)) -> dict:
    role = str(user.get("role", "")).strip().casefold()
    if role not in {"hr admin", "administrator", "manager", "employee"}:
        raise HTTPException(status_code=403, detail="You are not authorized to view organization information.")
    return user


def _profile_organization_reader(user: dict = Depends(_organization_user)) -> dict:
    """Profile organization details are readable by every signed-in HRMS role."""
    role = str(user.get("role", "")).strip().casefold()
    if role not in {"hr admin", "administrator", "manager", "hr manager", "employee", "super admin"}:
        raise HTTPException(status_code=403, detail="You are not authorized to view profile organization information.")
    return user


def _organization_admin(user: dict = Depends(_organization_user)) -> dict:
    role = str(user.get("role", "")).strip().casefold()
    if role not in {"hr admin", "administrator"}:
        raise HTTPException(status_code=403, detail="Only HR Admin or Administrator can manage organization setup.")
    return user


def ensure_demo_user():
    if os.getenv("HRMS_ENV", "development").lower() != "development":
        return

    email = os.getenv("DEMO_USER_EMAIL", "").strip().lower()
    password = os.getenv("DEMO_USER_PASSWORD", "")
    if not email or not password:
        return

    now = datetime.now(timezone.utc)
    users_collection.update_one(
        {"work_email": email},
        {
            "$set": {
                "name": os.getenv("DEMO_USER_NAME", "HR Admin").strip(),
                "mobile": os.getenv("DEMO_USER_MOBILE", "9000000000").strip(),
                "role": os.getenv("DEMO_USER_ROLE", "HR Admin").strip(),
                "role_id": os.getenv("DEMO_USER_ROLE", "HR Admin").strip().lower().replace(" ", "_"),
                "work_email": email,
                "password_hash": hash_password(password),
                "is_active": True,
                "failed_attempts": 0,
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
            "$unset": {"activation_token_hash": "", "activation_expires_at": "", "locked_until": ""},
        },
        upsert=True,
    ) 

class CompanyPayload(BaseModel):
    company: str = Field(min_length=1)
    legal_entity: str = Field(
        min_length=1,
        validation_alias=AliasChoices("legal_entity", "legalEntity"),
    )
    branch: str = Field(min_length=1)
    location: str = Field(min_length=1)
    branch_code: str = Field(default="", max_length=40)
    employee_ids: list[str] = Field(default_factory=list, max_length=1000)

    @field_validator("company", "legal_entity", "branch", "location", "branch_code")
    @classmethod
    def strip_and_validate(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

class CompanyResponse(CompanyPayload):
    model_config = ConfigDict(from_attributes=True)

    id: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class EmployeePayload(BaseModel):
    employee_id: str = Field(min_length=1)
    employee_name: str = Field(
        min_length=1,
        validation_alias=AliasChoices("employee_name", "employeeName"),
    )
    email: EmailStr
    phone: str = Field(min_length=1)
    department: str = Field(min_length=1)
    designation: str = Field(min_length=1)
    status: str = Field(default="Active")

    @field_validator(
        "employee_id",
        "employee_name",
        "phone",
        "department",
        "designation",
        "status",
    )
    @classmethod
    def strip_and_validate(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class EmployeeResponse(EmployeePayload):
    model_config = ConfigDict(from_attributes=True)

    id: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class UserPayload(BaseModel):
    name: str = Field(min_length=1)
    work_email: EmailStr
    mobile: str = Field(min_length=1)
    role: str = Field(min_length=1)
    country: str = ""
    country_iso: str | None = None
    country_code: str = ""

    @field_validator("name", "mobile", "role")
    @classmethod
    def strip_and_validate(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: str) -> str:
        # "HOD" is the display name of the stored "HR Manager" role; keep one
        # stored identifier so permissions and role_id never split in two.
        if value.casefold() == "hod":
            value = "HR Manager"
        allowed_roles = {"HR Admin", "Administrator", "HR Manager", "Employee"}
        if value not in allowed_roles:
            raise ValueError("role must be one of HR Admin, Administrator, HOD, or Employee")
        return value

    @field_validator("country", "country_code")
    @classmethod
    def strip_country_values(cls, value: str) -> str:
        return value.strip()

    @field_validator("country_iso")
    @classmethod
    def normalize_country_iso(cls, value: str | None) -> str | None:
        return value.strip().upper() if value else None


class UserCreatePayload(UserPayload):
    employee_id: str | None = Field(default=None, min_length=1)
    password: str | None = Field(default=None, min_length=8)

    @field_validator("employee_id")
    @classmethod
    def strip_employee_id(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


def validate_unique_user_fields(
    payload: UserPayload,
    user_id: ObjectId | None = None,
    employee_id: str | None = None,
):
    query = {
        "$or": [
            {"work_email": str(payload.work_email)},
            {"mobile": payload.mobile},
        ]
    }
    if employee_id:
        query["$or"].append({"employee_id": employee_id})
    if user_id is not None:
        query["_id"] = {"$ne": user_id}

    existing_user = users_collection.find_one(query)
    if existing_user is None:
        return
    if existing_user.get("work_email", "").lower() == str(payload.work_email).lower():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Work email already exists",
        )
    if employee_id and existing_user.get("employee_id") == employee_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Employee ID already exists",
        )
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Mobile number already exists",
    )


class UserResponse(UserPayload):
    model_config = ConfigDict(from_attributes=True)

    id: str
    employee_id: str | None = None
    is_active: bool
    is_wfh_enabled: bool = False
    created_at: datetime
    updated_at: datetime
    last_login: datetime | None = None
    login_count: int = 0
    active_login_count: int = 0
    # Authoritative joining date; returned by /api/auth/me for the attendance calendar.
    joining_date: date | None = None


class UserStatusPayload(BaseModel):
    is_active: bool


class UserActivationPayload(UserStatusPayload):
    password: str | None = Field(default=None, min_length=8)


class UserLoginPayload(BaseModel):
    identifier: str = Field(
        min_length=1,
        validation_alias=AliasChoices("identifier", "work_email", "employee_id"),
    )
    password: str = Field(min_length=1)


class ChangePasswordPayload(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8)


class AuthMessageResponse(BaseModel):
    message: str


class AuthResponse(BaseModel):
    token: str
    user: UserResponse


class UserActivationResponse(BaseModel):
    user: UserResponse
    delivery: str
    activation_url: str | None = None


class ActivationDetailsResponse(BaseModel):
    name: str
    work_email: EmailStr
    expires_at: datetime


class ActivationPayload(BaseModel):
    password: str = Field(min_length=8)


class UserListResponse(BaseModel):
    users: list[UserResponse]
    total: int


class BusinessUnitPayload(BaseModel):
    company_id: str = Field(min_length=1)
    business_unit_name: str = Field(min_length=1)
    manager_employee_id: str | None = None

    @field_validator("company_id", "business_unit_name")
    @classmethod
    def strip_and_validate(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class BusinessUnitResponse(BusinessUnitPayload):
    id: str
    is_active: bool | None = None


class DepartmentPayload(BaseModel):
    company_id: str = Field(min_length=1)
    department_name: str = Field(min_length=1)
    parent_department_id: str | None = None
    manager_employee_id: str | None = None

    @field_validator("company_id", "department_name")
    @classmethod
    def strip_and_validate(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class DepartmentResponse(DepartmentPayload):
    id: str
    is_active: bool | None = None


class TeamPayload(BaseModel):
    company_id: str = Field(min_length=1)
    department_id: str = Field(min_length=1)
    team_name: str = Field(min_length=1)
    manager_employee_id: str | None = None

    @field_validator("company_id", "department_id", "team_name")
    @classmethod
    def strip_and_validate(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class TeamResponse(TeamPayload):
    id: str
    is_active: bool | None = None


class DesignationPayload(BaseModel):
    company_id: str = Field(min_length=1)
    designation_name: str = Field(min_length=1)
    description: str | None = None

    @field_validator("company_id", "designation_name")
    @classmethod
    def strip_and_validate(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class DesignationResponse(DesignationPayload):
    id: str
    is_active: bool | None = None


class HolidayCalendarAssignment(BaseModel):
    """One organizational assignment: a branch record (company/legal entity + branch) and its location."""
    company_id: str = Field(min_length=1)
    location: str = Field(min_length=1)


class HolidayCalendarPayload(BaseModel):
    company_id: str = Field(min_length=1)
    location: str = Field(min_length=1)
    calendar_name: str = Field(min_length=1)
    calendar_year: int = Field(ge=2000, le=2100)
    country: str = Field(default="India", min_length=2, max_length=80)
    state: str | None = Field(default=None, max_length=80)
    description: str | None = None
    # Required for new calendars; legacy company-wide calendars have none.
    religion: str | None = Field(default=None, max_length=80)
    # Company/legal entity -> branch -> location assignments (validated on the server).
    assignments: list[HolidayCalendarAssignment] = Field(default_factory=list, max_length=200)

    @field_validator("company_id", "location", "calendar_name")
    @classmethod
    def strip_and_validate(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("religion")
    @classmethod
    def clean_religion(cls, value: str | None) -> str | None:
        value = " ".join(str(value or "").split())
        return value or None


class HolidayCalendarResponse(HolidayCalendarPayload):
    id: str
    is_active: bool
    status: str = "Draft"
    published_at: datetime | None = None
    religion_key: str = ""
    updated_at: datetime | None = None
    assignments: list[dict] = Field(default_factory=list)
    holiday_count: int | None = None

    @field_validator("published_at", "updated_at")
    @classmethod
    def mark_naive_as_utc(cls, value: datetime | None) -> datetime | None:
        # MongoDB returns naive UTC datetimes; tag them so the API emits an explicit offset.
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class HolidayPayload(BaseModel):
    calendar_id: str = Field(min_length=1)
    holiday_date: date
    holiday_name: str = Field(min_length=1)
    holiday_type: str = Field(default="Public")
    applicable_religion: str | None = None
    applicable_religions: list[str] = Field(default_factory=list)
    applicable_employee_groups: list[str] = Field(default_factory=list)
    applicability: str = "All Employees"
    is_company_holiday: bool = False
    description: str | None = None
    is_optional: bool = False
    status: str = "Draft"

    @field_validator("calendar_id", "holiday_name", "holiday_type")
    @classmethod
    def strip_and_validate(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("applicable_religion")
    @classmethod
    def clean_applicable_religion(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        return value or None

    @model_validator(mode="after")
    def require_religion_for_religious_holiday(self):
        if self.applicability.strip().lower() == "selected religions":
            if not (self.applicable_religions or self.applicable_religion):
                raise ValueError("Select at least one applicable religion.")
        if self.applicability.strip().lower() == "selected employee groups" and not self.applicable_employee_groups:
            raise ValueError("Select at least one applicable employee group.")
        if self.holiday_type.strip().lower() == "religion" and not (
            self.applicable_religions or self.applicable_religion
        ):
            raise ValueError("Enter the religion this holiday applies to.")
        return self


class HolidayResponse(HolidayPayload):
    id: str
    is_active: bool


class EmployeeHolidayCalendarPayload(BaseModel):
    employee_id: str = Field(min_length=1)
    calendar_id: str = Field(min_length=1)

    @field_validator("employee_id", "calendar_id")
    @classmethod
    def strip_and_validate(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class EmployeeHolidayCalendarResponse(EmployeeHolidayCalendarPayload):
    id: str
    assigned_at: datetime
    is_active: bool


def serialize_company(company: dict) -> dict:
    company["id"] = str(company.pop("_id"))
    return company


def serialize_employee(employee: dict) -> dict:
    employee["id"] = str(employee.pop("_id"))
    return employee


def serialize_user(user: dict) -> dict:
    safe_user = {
        key: value
        for key, value in user.items()
        if key not in {"_id", "password", "password_hash"}
    }
    for field in ("created_at", "updated_at", "last_login"):
        value = safe_user.get(field)
        if isinstance(value, datetime) and value.tzinfo is None:
            safe_user[field] = value.replace(tzinfo=timezone.utc)
    safe_user["id"] = str(user["_id"])
    return safe_user


def serialize_business_unit(record: dict) -> dict:
    record["id"] = str(record.pop("_id"))
    return record


def serialize_department(record: dict) -> dict:
    record["id"] = str(record.pop("_id"))
    return record


def serialize_team(record: dict) -> dict:
    record["id"] = str(record.pop("_id"))
    return record


def serialize_designation(record: dict) -> dict:
    record["id"] = str(record.pop("_id"))
    return record


def serialize_holiday_calendar(record: dict) -> dict:
    record["id"] = str(record.pop("_id"))
    return record


def serialize_holiday(record: dict) -> dict:
    record["id"] = str(record.pop("_id"))
    if isinstance(record.get("holiday_date"), datetime):
        record["holiday_date"] = record["holiday_date"].date()
    return record


def serialize_employee_holiday_calendar(record: dict) -> dict:
    record["id"] = str(record.pop("_id"))
    return record


def parse_company_id(company_id: str) -> ObjectId:
    if not ObjectId.is_valid(company_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid company id",
        )
    return ObjectId(company_id)


def parse_employee_id(employee_id: str) -> ObjectId:
    if not ObjectId.is_valid(employee_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid employee id",
        )
    return ObjectId(employee_id)


def parse_object_id(raw_id: str, entity_name: str) -> ObjectId:
    try:
        object_id = ObjectId(raw_id)
    except Exception as exc:  # pragma: no cover - defensive guard
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {entity_name} id",
        ) from exc

    if not ObjectId.is_valid(str(object_id)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {entity_name} id",
        )
    return object_id


def get_active_company(company_id: str) -> dict:
    company = companies_collection.find_one(
        {"_id": parse_company_id(company_id), "is_active": True}
    )
    if company is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found",
        )
    return serialize_company(company)


def get_active_employee(employee_id: str) -> dict:
    employee = employees_collection.find_one(
        {"_id": parse_employee_id(employee_id), "is_active": True}
    )
    if employee is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Employee not found",
        )
    return serialize_employee(employee)


def get_company_reference(company_id: str):
    company = companies_collection.find_one(
        {"_id": parse_company_id(company_id), "is_active": True}
    )
    if company is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found",
        )
    return company


def get_business_unit_reference(business_unit_id: str):
    try:
        record = business_units_collection.find_one({
            "_id": parse_object_id(business_unit_id, "business unit"),
            "is_active": True,
        })
    except HTTPException:
        raise
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Business unit not found",
        )
    return record


def get_department_reference(department_id: str):
    try:
        record = departments_collection.find_one({
            "_id": parse_object_id(department_id, "department"),
            "is_active": True,
        })
    except HTTPException:
        raise
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Department not found",
        )
    return record


def validate_company_scope(company_id: str, entity_name: str):
    get_company_reference(company_id)
    if not ObjectId.is_valid(company_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {entity_name} company id",
        )


def validate_department_parent(company_id: str, parent_department_id: str | None):
    if parent_department_id is None:
        return
    parent_id = parse_object_id(parent_department_id, "parent department")
    parent = departments_collection.find_one({
        "_id": parent_id,
        "company_id": company_id,
        "is_active": True,
    })
    if parent is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Parent department does not exist in the same company context",
        )


def validate_department_parent_for_record(
    department_id: str,
    company_id: str,
    parent_department_id: str | None,
):
    if parent_department_id is not None and department_id == parent_department_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A department cannot report to itself",
        )
    validate_department_parent(company_id, parent_department_id)


def validate_team_parent(company_id: str, department_id: str):
    dept_id = parse_object_id(department_id, "department")
    department = departments_collection.find_one({
        "_id": dept_id,
        "company_id": company_id,
        "is_active": True,
    })
    if department is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Department does not exist in the same company context",
        )


def validate_manager_employee(manager_employee_id: str | None):
    if manager_employee_id is None:
        return
    if not ObjectId.is_valid(manager_employee_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid manager employee id",
        )
    employee = employees_collection.find_one({
        "_id": ObjectId(manager_employee_id),
        "is_active": True,
    })
    if employee is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Manager employee not found",
        )


def get_holiday_calendar_reference(calendar_id: str) -> dict:
    record = holiday_calendars_collection.find_one(
        {"_id": parse_object_id(calendar_id, "holiday calendar"), "is_active": True}
    )
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Holiday calendar not found",
        )
    return record


def get_holiday_reference(holiday_id: str) -> dict:
    record = holidays_collection.find_one(
        {"_id": parse_object_id(holiday_id, "holiday"), "is_active": True}
    )
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Holiday not found",
        )
    return record


def validate_calendar_location(company_id: str, location: str):
    company = get_company_reference(company_id)
    if company.get("location") != location:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Location does not belong to the selected company",
        )


def validate_holiday_date(calendar: dict, holiday_date: date):
    if holiday_date.year != calendar["calendar_year"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Holiday date must belong to the calendar year",
        )
    if holiday_date < datetime.now(ORGANIZATION_TIMEZONE).date():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New or changed holidays must be today or in the future.",
        )


def validate_employee_reference(employee_id: str):
    employee = employees_collection.find_one(
        {"_id": parse_object_id(employee_id, "employee"), "is_active": True}
    )
    if employee is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Employee not found",
        )
    return employee


def validate_company_employee_ids(employee_ids: list[str]) -> list[str]:
    """Accept stable Mongo IDs from either Employee Master user records or legacy employees."""
    normalized = list(dict.fromkeys(str(value).strip() for value in employee_ids if str(value).strip()))
    for employee_id in normalized:
        if not ObjectId.is_valid(employee_id):
            raise HTTPException(status_code=422, detail=f"Invalid employee selection: {employee_id}")
        object_id = ObjectId(employee_id)
        employee_record = employees_collection.find_one({"_id": object_id, "is_active": True})
        user_record = users_collection.find_one({"_id": object_id, "is_active": True})
        if employee_record is None and user_record is None:
            raise HTTPException(
                status_code=422,
                detail=f"Selected employee {employee_id} does not exist or is inactive.",
            )
    return normalized


def validate_employee_assignment_availability(company_id: str | None, employee_ids: list[str]) -> list[str]:
    """Reject assignments when an employee already belongs to another active branch."""
    normalized = validate_company_employee_ids(employee_ids)
    if not normalized:
        return normalized

    query: dict = {"is_active": True, "employee_ids": {"$in": normalized}}
    if company_id is not None:
        query["_id"] = {"$ne": ObjectId(company_id)}
    conflict = companies_collection.find_one(query, {"company": 1, "branch": 1, "employee_ids": 1})
    if conflict:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="One or more selected employees are already assigned to another active branch. Refresh the employee list and try again.",
        )
    return normalized


@router.post(
    "/api/companies",
    response_model=CompanyResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_organization_admin)],
)
def create_company(payload: CompanyPayload):
    validate_employee_assignment_availability(None, payload.employee_ids)
    now = datetime.now(timezone.utc)
    values = payload.model_dump()
    # Preserve the legacy primary-company marker for the first record only.
    # Additional branches are independent records and must not reuse the unique key.
    company = {
        **values,
        **({"organization_singleton_key": "primary"} if companies_collection.count_documents({}) == 0 else {}),
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    }
    try:
        result = companies_collection.insert_one(company)
    except DuplicateKeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A company/legal entity already exists.",
        ) from exc
    company["_id"] = result.inserted_id
    return get_active_company(str(result.inserted_id))


@router.get(
    "/api/companies",
    response_model=list[CompanyResponse],
    dependencies=[Depends(_organization_reader)],
)
def list_companies():
    companies = companies_collection.find({"is_active": True}).sort(
        "created_at", -1
    )
    return [serialize_company(company) for company in companies]


@router.get(
    "/api/companies/availability",
    dependencies=[Depends(_organization_reader)],
)
def get_company_creation_availability():
    return {"can_create": True}


@router.get(
    "/api/companies/{company_id}",
    response_model=CompanyResponse,
    dependencies=[Depends(_organization_reader)],
)
def get_company(company_id: str):
    return get_active_company(company_id)


@router.put(
    "/api/companies/{company_id}",
    response_model=CompanyResponse,
    dependencies=[Depends(_organization_admin)],
)
def update_company(company_id: str, payload: CompanyPayload):
    object_id = parse_company_id(company_id)
    validate_employee_assignment_availability(company_id, payload.employee_ids)
    now = datetime.now(timezone.utc)
    result = companies_collection.update_one(
        {"_id": object_id, "is_active": True},
        {"$set": {**payload.model_dump(), "updated_at": now}},
    )
    if result.matched_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found",
        )
    return get_active_company(company_id)


@router.delete(
    "/api/companies/{company_id}",
    dependencies=[Depends(_organization_admin)],
)
def deactivate_company(company_id: str):
    object_id = parse_company_id(company_id)
    existing = companies_collection.find_one({"_id": object_id, "is_active": True})
    if not existing:
        raise HTTPException(status_code=404, detail="Company not found")
    if existing.get("employee_ids"):
        raise HTTPException(status_code=409, detail="Reassign employees to another branch before deactivating this branch.")
    result = companies_collection.update_one(
        {"_id": object_id, "is_active": True},
        {
            "$set": {
                "is_active": False,
                "updated_at": datetime.now(timezone.utc),
            },
        },
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Company not found")
    return {"id": company_id, "message": "Company deactivated; historical HR data was retained."}


@router.get("/api/organization/hierarchy")
def get_organization_hierarchy(
    search: str | None = Query(default=None, max_length=120),
    department: str | None = Query(default=None, max_length=120),
    designation: str | None = Query(default=None, max_length=120),
):
    all_employees = list(employees_collection.find({"is_active": True}))
    all_users = list(users_collection.find({"is_active": True}))
    users_by_employee_id = {
        str(record.get("employee_id")): record
        for record in all_users
        if record.get("employee_id")
    }
    details_collection = db["employee_details"]
    nodes: dict[str, dict] = {}
    aliases: dict[str, str] = {}
    for record in all_employees:
        employee_id = str(record.get("employee_id") or record["_id"])
        linked_user = users_by_employee_id.get(employee_id, {})
        employee_record_id = str(record["_id"])
        detail = details_collection.find_one(
            {"$or": [{"user_id": str(linked_user.get("_id", ""))}, {"employee_id": employee_id}]}
        ) or {}
        node = {
            "id": employee_record_id,
            "employee_id": employee_id,
            "name": record.get("employee_name") or linked_user.get("name", ""),
            "department": detail.get("department") or record.get("department"),
            "designation": detail.get("designation") or record.get("designation"),
            "work_location": linked_user.get("work_location") or detail.get("work_location"),
            "reporting_manager_id": str(
                linked_user.get("reporting_manager_id") or record.get("reporting_manager_id")
            )
            if linked_user.get("reporting_manager_id") or record.get("reporting_manager_id")
            else None,
            "children": [],
        }
        searchable = " ".join(
            str(node.get(field) or "")
            for field in ("name", "employee_id", "department", "designation")
        ).casefold()
        if search and search.casefold() not in searchable:
            continue
        if department and str(node["department"] or "").casefold() != department.casefold():
            continue
        if designation and str(node["designation"] or "").casefold() != designation.casefold():
            continue
        nodes[employee_record_id] = node
        aliases[employee_record_id] = employee_record_id
        aliases[employee_id] = employee_record_id
        if linked_user:
            aliases[str(linked_user["_id"])] = employee_record_id

    cycles: set[str] = set()
    for node in nodes.values():
        manager_reference = node["reporting_manager_id"]
        manager_id = aliases.get(manager_reference) if manager_reference else None
        node["manager_id"] = manager_id
        node["reporting_manager_missing"] = bool(manager_reference and not manager_id)
    for start in nodes:
        path: list[str] = []
        positions: dict[str, int] = {}
        current = start
        while current and current in nodes:
            if current in positions:
                cycles.update(path[positions[current] :])
                break
            positions[current] = len(path)
            path.append(current)
            current = nodes[current]["manager_id"]
    roots = []
    for node in nodes.values():
        manager_id = node["manager_id"]
        if manager_id and manager_id in nodes and node["id"] not in cycles and manager_id not in cycles:
            nodes[manager_id]["children"].append(node)
        else:
            if node["id"] in cycles:
                node["reporting_relationship_invalid"] = "circular"
            roots.append(node)
    return {
        "employees": roots,
        "employee_count": len(nodes),
        "circular_reporting_employee_ids": sorted(cycles),
        "filtered": bool(search or department or designation),
    }


@router.get("/api/organization/me")
def get_my_organization_information(user: dict = Depends(_profile_organization_reader)):
    user_id = str(user["_id"])
    employee_id = str(user.get("employee_id") or user_id)
    details = db["employee_details"].find_one(
        {"$or": [{"user_id": user_id}, {"employee_id": employee_id}]}
    ) or {}
    employee = employees_collection.find_one({"employee_id": employee_id})
    if employee is None and ObjectId.is_valid(employee_id):
        employee = employees_collection.find_one({"_id": ObjectId(employee_id)})
    employee = employee or {}

    # Resolve the logged-in person's actual branch assignment first. Legacy records
    # may reference either the user id, the Employee Master id, or the employee code.
    employee_aliases = {user_id, employee_id}
    if employee.get("_id"):
        employee_aliases.add(str(employee["_id"]))
    company = companies_collection.find_one({
        "is_active": True,
        "employee_ids": {"$in": list(employee_aliases)},
    })
    if company is None:
        work_location = str(
            user.get("work_location") or details.get("work_location") or employee.get("work_location") or ""
        ).strip()
        if work_location:
            company = companies_collection.find_one({
                "is_active": True,
                "$or": [
                    {"branch": {"$regex": f"^{re.escape(work_location)}$", "$options": "i"}},
                    {"location": {"$regex": f"^{re.escape(work_location)}$", "$options": "i"}},
                    {"branch_code": work_location},
                ],
            })
    if company is None:
        # Keep legacy single-branch deployments working, but do not display a random
        # branch when the organization has multiple branches and this person is unassigned.
        active_companies = list(companies_collection.find({"is_active": True}).limit(2))
        if len(active_companies) == 1:
            company = active_companies[0]
        else:
            company = {}

    assignment_employee_id = str(employee.get("_id") or employee_id)
    current_year = datetime.now(ORGANIZATION_TIMEZONE).year
    calendar_filter: dict = {"status": "Published", "is_active": True}
    assignment = None
    calendar = None
    for candidate in employee_holiday_calendars_collection.find(
        {"employee_id": assignment_employee_id, "is_active": True}
    ):
        assigned_calendar_id = candidate.get("calendar_id")
        assigned_calendar = holiday_calendars_collection.find_one(
            {
                "_id": parse_object_id(assigned_calendar_id, "holiday calendar"),
                **calendar_filter,
            }
        )
        if assigned_calendar and assigned_calendar.get("calendar_year") == current_year:
            assignment = candidate
            calendar = assigned_calendar
            break
    if calendar is None:
        # Company-wide calendar only; religion calendars are resolved by religion.
        calendar = holiday_calendars_collection.find_one(
            {**calendar_filter, "calendar_year": current_year, "religion_key": {"$in": ["", None]}}
        )
    calendar_year = calendar.get("calendar_year", current_year) if calendar else current_year
    holidays = []
    if calendar:
        religion = str(details.get("religion") or "").casefold()
        for holiday in holidays_collection.find(
            {
                "calendar_id": str(calendar["_id"]),
                "is_active": True,
                "status": {"$ne": "Cancelled"},
            }
        ).sort("holiday_date", 1):
            holiday["status"] = "Published"
            applicable = [str(value).casefold() for value in holiday.get("applicable_religions", [])]
            if holiday.get("applicability") == "Selected Religions" and religion not in applicable:
                continue
            if holiday.get("applicability") == "Selected Employee Groups":
                employee_groups = {
                    str(value).casefold()
                    for value in (
                        details.get("department"),
                        details.get("designation"),
                        details.get("team"),
                        employee.get("department"),
                        employee.get("team"),
                    )
                    if value
                }
                required_groups = {
                    str(value).casefold()
                    for value in holiday.get("applicable_employee_groups", [])
                }
                if not employee_groups.intersection(required_groups):
                    continue
            holidays.append(serialize_holiday(holiday))
    manager_id = user.get("reporting_manager_id")
    manager = None
    if manager_id:
        manager = users_collection.find_one({"_id": ObjectId(manager_id)}) if ObjectId.is_valid(str(manager_id)) else None
        if manager is None:
            manager = users_collection.find_one({"employee_id": str(manager_id)})
    return {
        "company": {
            "company_name": company.get("company"),
            "legal_entity_name": company.get("legal_entity"),
            "address": company.get("branch") or company.get("location"),
            "branch": company.get("branch"),
            "branch_code": company.get("branch_code"),
            "work_location": company.get("location") or employee.get("work_location") or user.get("work_location"),
        },
        "employee": {
            "department": details.get("department") or employee.get("department") or user.get("department"),
            "designation": details.get("designation") or employee.get("designation") or user.get("designation"),
            "reporting_manager": manager.get("name") if manager else None,
            "reporting_manager_id": manager.get("employee_id") if manager else None,
            "employment_type": employee.get("employment_type") or user.get("employment_type"),
            "employment_status": employee.get("status") or user.get("status"),
            "employee_id": employee_id,
        },
        "holiday_calendar": {
            "year": calendar_year,
            "calendar": serialize_holiday_calendar(calendar) if calendar else None,
            "holidays": holidays,
        },
    }


@router.post(
    "/api/users",
    response_model=UserActivationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_user(payload: UserCreatePayload):
    employee_id_value = getattr(payload, "employee_id", None)
    employee_id = employee_id_value.strip() if employee_id_value else None
    validate_unique_user_fields(payload, employee_id=employee_id)
    now = datetime.now(timezone.utc)
    user_fields = payload.model_dump(exclude_none=True, exclude={"password"})
    user = {
        **user_fields,
        "work_email": str(payload.work_email).strip().lower(),
        "role_id": payload.role.lower().replace(" ", "_"),
        "is_active": bool(getattr(payload, "password", None)),
        "login_count": 0,
        "created_at": now,
        "updated_at": now,
    }
    password = getattr(payload, "password", None)
    if password:
        user["password_hash"] = hash_password(password)
    else:
        raw_token = secrets.token_urlsafe(32)
        user["activation_token_hash"] = hash_activation_token(raw_token)
        user["activation_expires_at"] = now + timedelta(hours=24)
    try:
        result = users_collection.insert_one(user)
    except DuplicateKeyError as exc:
        error_text = str(exc).lower()
        field = (
            "Employee ID" if "employee_id" in error_text
            else "Mobile number" if "mobile" in error_text
            else "Work email"
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{field} already exists",
        ) from exc
    user["_id"] = result.inserted_id
    if password:
        log_event(f"User created and activated: employee_id={employee_id}, work_email={user['work_email']}")
        return {"user": serialize_user(user), "delivery": "direct", "activation_url": None}

    activation_url = build_activation_url(raw_token)
    try:
        delivery = send_activation_email(user["work_email"], user["name"], activation_url)
    except (OSError, smtplib.SMTPException):
        raise HTTPException(status_code=503, detail="Activation email could not be sent")
    return {
        "user": serialize_user(user),
        "delivery": delivery,
        "activation_url": activation_url if delivery == "development_console" else None,
    }


@router.get("/api/auth/activate", response_model=ActivationDetailsResponse)
def get_activation_details(token: str = Query(min_length=1)):
    user = users_collection.find_one({"activation_token_hash": hash_activation_token(token)})
    if user is None or user.get("activation_expires_at") is None or user["activation_expires_at"] <= datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Activation link is invalid or expired")
    return {
        "name": user["name"],
        "work_email": user["work_email"],
        "expires_at": user["activation_expires_at"],
    }


@router.post("/api/auth/activate", response_model=UserResponse)
def activate_account(token: str = Query(min_length=1), payload: ActivationPayload = ...):
    now = datetime.now(timezone.utc)
    token_hash = hash_activation_token(token)
    user = users_collection.find_one({"activation_token_hash": token_hash})
    if user is None or user.get("activation_expires_at") is None or user["activation_expires_at"] <= now:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Activation link is invalid or expired")
    result = users_collection.update_one(
        {"_id": user["_id"], "activation_token_hash": token_hash},
        {"$set": {"password_hash": hash_password(payload.password), "is_active": True, "updated_at": now}, "$unset": {"activation_token_hash": "", "activation_expires_at": ""}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Activation link is invalid or expired")
    user.update({"password_hash": "", "is_active": True, "updated_at": now})
    log_event(f"Account activated: work_email={user['work_email']}")
    return serialize_user(user)


@router.get("/api/users", response_model=list[UserResponse])
def list_users(
    search: str | None = Query(default=None, min_length=1),
    status_filter: str | None = Query(default=None, alias="status"),
):
    query = {}
    if search:
        query["$or"] = [
            {"name": {"$regex": search, "$options": "i"}},
            {"work_email": {"$regex": search, "$options": "i"}},
            {"employee_id": {"$regex": search, "$options": "i"}},
        ]
    if status_filter:
        if status_filter.lower() not in {"active", "inactive", "all"}:
            raise HTTPException(status_code=400, detail="Status must be active, inactive, or all")
        if status_filter.lower() != "all":
            query["is_active"] = status_filter.lower() == "active"
    users = list(users_collection.find(query).sort("created_at", -1))
    # Joining dates for the manual-attendance screen: user record first, then
    # employee details (same precedence as the attendance module), in one query.
    missing = [str(user["_id"]) for user in users if user.get("date_of_joining") is None]
    detail_dates = {
        item["user_id"]: item.get("date_of_joining")
        for item in db["employee_details"].find({"user_id": {"$in": missing}}, {"user_id": 1, "date_of_joining": 1})
    } if missing else {}
    return [
        {
            **serialize_user(user),
            "joining_date": employee_joining_date({
                "date_of_joining": user.get("date_of_joining")
                if user.get("date_of_joining") is not None
                else detail_dates.get(str(user["_id"])),
            }),
        }
        for user in users
    ]


@router.get("/api/users/{user_id}", response_model=UserResponse)
def get_user(user_id: str):
    object_id = parse_object_id(user_id, "user")
    user = users_collection.find_one({"_id": object_id})
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return serialize_user(user)


@router.put("/api/users/{user_id}", response_model=UserResponse)
def update_user(user_id: str, payload: UserPayload):
    object_id = parse_object_id(user_id, "user")
    validate_unique_user_fields(payload, object_id)
    now = datetime.now(timezone.utc)
    try:
        result = users_collection.update_one(
            {"_id": object_id},
            {"$set": {
                **payload.model_dump(),
                "work_email": str(payload.work_email).strip().lower(),
                "role_id": payload.role.lower().replace(" ", "_"),
                "updated_at": now,
            }},
        )
    except DuplicateKeyError as exc:
        field = "Mobile number" if "mobile" in str(exc).lower() else "Work email"
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{field} already exists",
        ) from exc
    if result.matched_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    # A role change can change who approves this user's (or their reports') leave.
    reroute_pending_leave_requests({user_id})
    user = users_collection.find_one({"_id": object_id})
    return serialize_user(user)


@router.patch("/api/users/{user_id}", response_model=UserResponse)
def patch_user(user_id: str, payload: UserPayload):
    return update_user(user_id, payload)


@router.patch("/api/users/{user_id}/status", response_model=UserResponse)
def update_user_status(user_id: str, payload: UserActivationPayload):
    object_id = parse_object_id(user_id, "user")
    now = datetime.now(timezone.utc)
    update_fields = {"is_active": payload.is_active, "updated_at": now}
    if payload.is_active:
        update = {"$set": update_fields, "$unset": {"deactivated_at": ""}}
        if getattr(payload, "password", None):
            update["$set"]["password_hash"] = hash_password(payload.password)
    else:
        update_fields["deactivated_at"] = now
        update = {"$set": update_fields}

    result = users_collection.update_one({"_id": object_id}, update)
    if result.matched_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    # Pending leave assigned to a deactivated approver must move to a valid one.
    reroute_pending_leave_requests({user_id})
    user = users_collection.find_one({"_id": object_id})
    log_event(f"User status changed: user_id={user_id}, is_active={payload.is_active}")
    return serialize_user(user)


@router.post("/api/auth/login", response_model=AuthResponse)
def login_user(payload: UserLoginPayload):
    identifier = payload.identifier.strip()
    normalized_email = identifier.lower()
    user = users_collection.find_one({
        "$or": [
            {"work_email": normalized_email},
            {"employee_id": identifier},
        ]
    })
    if user is None:
        log_event(f"Login failed: identifier={identifier}")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if not user.get("is_active", False):
        log_event(f"Login denied for inactive user: identifier={identifier}")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    now = datetime.now(timezone.utc)
    locked_until = user.get("locked_until")
    if locked_until and locked_until > now:
        log_event(f"Login denied for locked user: identifier={identifier}")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if not verify_password(payload.password, user.get("password_hash")):
        failed_attempts = user.get("failed_attempts", 0) + 1
        update_fields = {"failed_attempts": failed_attempts}
        if failed_attempts >= 5:
            update_fields["locked_until"] = now + timedelta(minutes=15)
        users_collection.update_one({"_id": user["_id"]}, {"$set": update_fields})
        log_event(f"Login failed: identifier={identifier}, failed_attempts={failed_attempts}")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    token = secrets.token_urlsafe(32)
    users_collection.update_one(
        {"_id": user["_id"]},
        {
            "$set": {"last_login": now, "failed_attempts": 0},
            "$inc": {"login_count": 1},
            "$unset": {"locked_until": ""},
        },
    )
    user["last_login"] = now
    user["login_count"] = int(user.get("login_count", 0) or 0) + 1
    active_sessions[token] = str(user["_id"])
    user["active_login_count"] = sum(
        session_user_id == str(user["_id"])
        for session_user_id in active_sessions.values()
    )
    log_event(f"Login successful: user_id={user['_id']}, identifier={identifier}")
    return {"token": token, "user": serialize_user(user)}


@router.get("/api/auth/me", response_model=UserResponse)
def get_current_user(authorization: str | None = Header(default=None), x_user_id: str | None = Header(default=None)):
    if not isinstance(authorization, str):
        authorization = None
    token = authorization.removeprefix("Bearer ") if authorization else None
    session_user_id = active_sessions.get(token) if token else x_user_id
    if not session_user_id or not isinstance(session_user_id, str):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    user = get_user(session_user_id)
    user["active_login_count"] = sum(
        active_session_user_id == session_user_id
        for active_session_user_id in active_sessions.values()
    )
    user["joining_date"] = employee_joining_date(
        {"_id": ObjectId(session_user_id), "date_of_joining": user.get("date_of_joining")}
    )
    return user

# Change Password
@router.post("/api/auth/change-password", response_model=AuthMessageResponse)
def change_password(
    payload: ChangePasswordPayload,
    authorization: str | None = Header(default=None),
):
    if not isinstance(authorization, str):
        authorization = None

    token = authorization.removeprefix("Bearer ") if authorization else None
    session_user_id = active_sessions.get(token) if token else None

    if not session_user_id or not isinstance(session_user_id, str):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    user = users_collection.find_one({"_id": parse_object_id(session_user_id, "user")})
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    if not user.get("is_active", False):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account is inactive",
        )

    if not verify_password(payload.current_password, user.get("password_hash")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    if payload.current_password == payload.new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be different from the current password",
        )

    now = datetime.now(timezone.utc)
    result = users_collection.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "password_hash": hash_password(payload.new_password),
                "updated_at": now,
                "failed_attempts": 0,
            },
            "$unset": {"locked_until": ""},
        },
    )

    if result.matched_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    log_event(f"Password changed: user_id={user['_id']}")
    return {"message": "Password changed successfully"}

@router.post("/api/auth/logout", response_model=AuthMessageResponse)
def logout_user(authorization: str | None = Header(default=None)):
    if isinstance(authorization, str) and authorization:
        active_sessions.pop(authorization.removeprefix("Bearer "), None)
    return {"message": "Logged out successfully"}


@router.delete("/api/users/{user_id}")
def deactivate_user(user_id: str):
    object_id = parse_object_id(user_id, "user")
    #now = datetime.now(timezone.utc)
    """result = users_collection.update_one(
        {"_id": object_id, "is_active": True},
        {"$set": {"is_active": False, "updated_at": now, "deactivated_at": now}},
    )"""
    result=users_collection.delete_one(
        {"_id":object_id}
    )
    if result.deleted_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Remove Employee Master extra information belonging to the deleted user.
    db["employee_details"].delete_one({"user_id": str(object_id)})

    # Invalidate any active login session for this deleted account.
    for token, session_user_id in list(active_sessions.items()):
        if str(session_user_id) == str(object_id):
            active_sessions.pop(token, None)

    return {"id": user_id, "message": "User deleted successfully"}


@router.post("/api/users/{user_id}/activate", response_model=UserResponse)
def activate_user(user_id: str):
    object_id = parse_object_id(user_id, "user")
    now = datetime.now(timezone.utc)
    result = users_collection.update_one(
        {"_id": object_id, "is_active": False},
        {"$set": {"is_active": True, "updated_at": now}, "$unset": {"deactivated_at": ""}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inactive user not found")
    user = users_collection.find_one({"_id": object_id})
    return serialize_user(user)


@router.post(
    "/api/business-units",
    response_model=BusinessUnitResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_business_unit(payload: BusinessUnitPayload):
    validate_company_scope(payload.company_id, "business unit")
    validate_manager_employee(payload.manager_employee_id)
    business_unit = {**payload.model_dump(), "is_active": True}
    result = business_units_collection.insert_one(business_unit)
    business_unit["_id"] = result.inserted_id
    return serialize_business_unit(business_unit)


@router.get("/api/business-units", response_model=list[BusinessUnitResponse])
def list_business_units():
    records = business_units_collection.find({"is_active": True}).sort("_id", -1)
    return [serialize_business_unit(record) for record in records]


@router.get("/api/business-units/{business_unit_id}", response_model=BusinessUnitResponse)
def get_business_unit(business_unit_id: str):
    record = business_units_collection.find_one(
        {"_id": parse_object_id(business_unit_id, "business unit"), "is_active": True}
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Business unit not found")
    return serialize_business_unit(record)


@router.put("/api/business-units/{business_unit_id}", response_model=BusinessUnitResponse)
def update_business_unit(business_unit_id: str, payload: BusinessUnitPayload):
    get_company_reference(payload.company_id)
    validate_manager_employee(payload.manager_employee_id)
    object_id = parse_object_id(business_unit_id, "business unit")
    result = business_units_collection.update_one(
        {"_id": object_id, "is_active": True},
        {"$set": payload.model_dump()},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Business unit not found")
    return get_business_unit(business_unit_id)


@router.delete("/api/business-units/{business_unit_id}")
def deactivate_business_unit(business_unit_id: str):
    object_id = parse_object_id(business_unit_id, "business unit")
    result = business_units_collection.update_one(
        {"_id": object_id, "is_active": True},
        {"$set": {"is_active": False}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Business unit not found")
    return {"id": business_unit_id, "message": "Business unit deactivated successfully"}


@router.post(
    "/api/departments",
    response_model=DepartmentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_department(payload: DepartmentPayload):
    get_company_reference(payload.company_id)
    validate_department_parent(payload.company_id, payload.parent_department_id)
    validate_manager_employee(payload.manager_employee_id)
    department = {**payload.model_dump(), "is_active": True}
    result = departments_collection.insert_one(department)
    department["_id"] = result.inserted_id
    return serialize_department(department)


@router.get("/api/departments", response_model=list[DepartmentResponse])
def list_departments():
    records = departments_collection.find({"is_active": True}).sort("_id", -1)
    return [serialize_department(record) for record in records]


@router.get("/api/departments/{department_id}", response_model=DepartmentResponse)
def get_department(department_id: str):
    record = departments_collection.find_one(
        {"_id": parse_object_id(department_id, "department"), "is_active": True}
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Department not found")
    return serialize_department(record)


@router.put("/api/departments/{department_id}", response_model=DepartmentResponse)
def update_department(department_id: str, payload: DepartmentPayload):
    get_company_reference(payload.company_id)
    validate_department_parent_for_record(
        department_id, payload.company_id, payload.parent_department_id
    )
    validate_manager_employee(payload.manager_employee_id)
    object_id = parse_object_id(department_id, "department")
    result = departments_collection.update_one(
        {"_id": object_id, "is_active": True},
        {"$set": payload.model_dump()},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Department not found")
    return get_department(department_id)


@router.delete("/api/departments/{department_id}")
def deactivate_department(department_id: str):
    object_id = parse_object_id(department_id, "department")
    result = departments_collection.update_one(
        {"_id": object_id, "is_active": True},
        {"$set": {"is_active": False}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Department not found")
    return {"id": department_id, "message": "Department deactivated successfully"}


@router.post(
    "/api/teams",
    response_model=TeamResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_team(payload: TeamPayload):
    get_company_reference(payload.company_id)
    validate_team_parent(payload.company_id, payload.department_id)
    validate_manager_employee(payload.manager_employee_id)
    team = {**payload.model_dump(), "is_active": True}
    result = teams_collection.insert_one(team)
    team["_id"] = result.inserted_id
    return serialize_team(team)


@router.get("/api/teams", response_model=list[TeamResponse])
def list_teams():
    records = teams_collection.find({"is_active": True}).sort("_id", -1)
    return [serialize_team(record) for record in records]


@router.get("/api/teams/{team_id}", response_model=TeamResponse)
def get_team(team_id: str):
    record = teams_collection.find_one(
        {"_id": parse_object_id(team_id, "team"), "is_active": True}
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Team not found")
    return serialize_team(record)


@router.put("/api/teams/{team_id}", response_model=TeamResponse)
def update_team(team_id: str, payload: TeamPayload):
    get_company_reference(payload.company_id)
    validate_team_parent(payload.company_id, payload.department_id)
    validate_manager_employee(payload.manager_employee_id)
    object_id = parse_object_id(team_id, "team")
    result = teams_collection.update_one(
        {"_id": object_id, "is_active": True},
        {"$set": payload.model_dump()},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Team not found")
    return get_team(team_id)


@router.delete("/api/teams/{team_id}")
def deactivate_team(team_id: str):
    object_id = parse_object_id(team_id, "team")
    result = teams_collection.update_one(
        {"_id": object_id, "is_active": True},
        {"$set": {"is_active": False}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Team not found")
    return {"id": team_id, "message": "Team deactivated successfully"}


@router.post(
    "/api/designations",
    response_model=DesignationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_designation(payload: DesignationPayload):
    get_company_reference(payload.company_id)
    designation = {**payload.model_dump(), "is_active": True}
    result = designations_collection.insert_one(designation)
    designation["_id"] = result.inserted_id
    return serialize_designation(designation)


@router.get("/api/designations", response_model=list[DesignationResponse])
def list_designations():
    records = designations_collection.find({"is_active": True}).sort("_id", -1)
    return [serialize_designation(record) for record in records]


@router.get("/api/designations/{designation_id}", response_model=DesignationResponse)
def get_designation(designation_id: str):
    record = designations_collection.find_one(
        {"_id": parse_object_id(designation_id, "designation"), "is_active": True}
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Designation not found")
    return serialize_designation(record)


@router.put("/api/designations/{designation_id}", response_model=DesignationResponse)
def update_designation(designation_id: str, payload: DesignationPayload):
    get_company_reference(payload.company_id)
    object_id = parse_object_id(designation_id, "designation")
    result = designations_collection.update_one(
        {"_id": object_id, "is_active": True},
        {"$set": payload.model_dump()},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Designation not found")
    return get_designation(designation_id)


@router.delete("/api/designations/{designation_id}")
def deactivate_designation(designation_id: str):
    object_id = parse_object_id(designation_id, "designation")
    result = designations_collection.update_one(
        {"_id": object_id, "is_active": True},
        {"$set": {"is_active": False}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Designation not found")
    return {"id": designation_id, "message": "Designation deactivated successfully"}


@router.post(
    "/api/holiday-calendars",
    response_model=HolidayCalendarResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_holiday_calendar(payload: HolidayCalendarPayload, user: dict = Depends(_organization_admin)):
    if not payload.religion:
        raise HTTPException(status_code=422, detail="Select the religion this holiday calendar belongs to.")
    assignments = _validate_assignments(payload.assignments, required=True)
    religion = _resolve_employee_religion(payload.religion)
    _validate_calendar_year(payload.calendar_year)
    key = religion_key(religion)
    if _active_calendar_for(payload.calendar_year, key):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A {religion} holiday calendar already exists for {payload.calendar_year}. Open it to make changes.",
        )
    now = datetime.now(timezone.utc)
    calendar = {
        **payload.model_dump(exclude={"assignments"}),
        **_assignment_fields(assignments),
        "religion": religion,
        "religion_key": key,
        "status": "Draft",
        "is_active": True,
        "created_at": now,
        "created_by": str(user["_id"]),
    }
    try:
        result = holiday_calendars_collection.insert_one(calendar)
    except DuplicateKeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A {religion} holiday calendar already exists for {payload.calendar_year}. Open it to make changes.",
        ) from exc
    calendar["_id"] = result.inserted_id
    _record_organization_audit(
        "holiday_calendar_created", str(result.inserted_id), calendar["calendar_year"],
        {"calendar_id": str(result.inserted_id), "religion": religion, "assignments": assignments, "actor_id": str(user["_id"])},
    )
    return serialize_holiday_calendar(calendar)


def _validate_assignments(raw: list, required: bool) -> list[dict]:
    """Validate company/legal entity -> branch -> location assignments against branch records.

    Each assignment names a branch record in ``companies`` (which carries the
    company and legal entity) and the location that belongs to that branch.
    """
    if required and not raw:
        raise HTTPException(status_code=422, detail="Select at least one company, branch and location for this calendar.")
    normalized: dict[str, dict] = {}
    problems = []
    for item in raw:
        if not ObjectId.is_valid(item.company_id):
            problems.append(f"Invalid branch selection '{item.company_id}'.")
            continue
        branch = companies_collection.find_one({"_id": ObjectId(item.company_id), "is_active": {"$ne": False}})
        if branch is None:
            problems.append("A selected branch no longer exists.")
            continue
        if " ".join(item.location.split()).casefold() != " ".join(str(branch.get("location") or "").split()).casefold():
            problems.append(f"Location '{item.location}' does not belong to branch {branch.get('branch')} of {branch.get('legal_entity') or branch.get('company')}.")
            continue
        normalized[str(branch["_id"])] = {
            "company_id": str(branch["_id"]),
            "company": branch.get("company"),
            "legal_entity": branch.get("legal_entity"),
            "branch": branch.get("branch"),
            "branch_code": branch.get("branch_code") or "",
            "location": branch.get("location"),
        }
    if problems:
        raise HTTPException(status_code=422, detail=" ".join(problems))
    return list(normalized.values())


def _assignment_fields(assignments: list[dict]) -> dict:
    """Stored assignments plus the legacy single company/location (first assignment)."""
    fields: dict = {"assignments": assignments}
    if assignments:
        fields["company_id"] = assignments[0]["company_id"]
        fields["location"] = assignments[0]["location"]
    return fields


def _allowed_calendar_years() -> list[int]:
    current_year = datetime.now(ORGANIZATION_TIMEZONE).year
    return [current_year, current_year + 1]


def _validate_calendar_year(year: int) -> None:
    allowed = _allowed_calendar_years()
    if year not in allowed:
        raise HTTPException(
            status_code=422,
            detail=f"Holiday calendars can be created only for {allowed[0]} (current year) or {allowed[1]} (next year).",
        )


def _resolve_employee_religion(value: str) -> str:
    """Canonical religion name as stored on employee records."""
    for religion in employee_religions():
        if religion_key(religion) == religion_key(value):
            return religion
    raise HTTPException(status_code=422, detail=f"No active employee is assigned the religion '{value}'.")


def _active_calendar_for(year: int, key: str, exclude_id: ObjectId | None = None) -> dict | None:
    query: dict = {"calendar_year": year, "is_active": True, "religion_key": key or {"$in": ["", None]}}
    if exclude_id is not None:
        query["_id"] = {"$ne": exclude_id}
    return holiday_calendars_collection.find_one(query)


def _is_hr_admin_role(user: dict | None) -> bool:
    return isinstance(user, dict) and str(user.get("role", "")).strip().casefold() in {"hr admin", "administrator"}


def _can_view_calendar(user: dict | None, calendar: dict) -> bool:
    """HR sees everything; others only published calendars that apply to them."""
    if _is_hr_admin_role(user):
        return True
    if calendar.get("status") != "Published":
        return False
    if not calendar.get("religion_key"):
        return True
    return isinstance(user, dict) and calendar_applies_to(calendar, user)


def _holiday_day(value) -> date:
    return value.date() if isinstance(value, datetime) else value


def _ist_stamp() -> str:
    return datetime.now(ORGANIZATION_TIMEZONE).strftime("%d %b %Y, %I:%M %p IST")


def _notify_calendar_employees(calendar: dict, title: str, message: str, event_key: str, days: int = 14, user_ids: list[str] | None = None) -> None:
    """Notify the employees a calendar applies to; never fails the saved change."""
    key = calendar.get("religion_key") or ""
    if user_ids is None and key:
        # Religion calendars reach only that religion within the assigned branches.
        user_ids = calendar_user_ids(calendar)
    try:
        create_system_notification(
            title=title,
            message=message + " View it in My Profile → Holiday Calendar.",
            validity=timedelta(days=days),
            event_key=event_key,
            # Legacy company-wide calendars keep notifying all users (user_ids None).
            user_ids=user_ids,
        )
    except Exception:
        logger.exception("Holiday calendar notification failed for %s", event_key)


def _reconcile_payroll(calendar: dict, holiday_dates: list[date], reason: str, user_ids: list[str] | None = None) -> None:
    if calendar.get("religion_key") and calendar.get("status") == "Published":
        try:
            flag_draft_payroll_for_holiday_change(
                calendar_user_ids(calendar) if user_ids is None else user_ids, holiday_dates, reason
            )
        except Exception:
            logger.exception("Unable to flag draft payroll for holiday change on calendar %s", calendar.get("_id"))


def _live_holiday_dates(calendar_id: str) -> list[date]:
    return [
        _holiday_day(item["holiday_date"])
        for item in holidays_collection.find(
            {"calendar_id": calendar_id, "is_active": True, "status": {"$ne": "Cancelled"}}, {"holiday_date": 1}
        )
    ]


def _calendar_label(calendar: dict) -> str:
    religion = calendar.get("religion")
    return f"{religion} holiday calendar {calendar['calendar_year']}" if religion else f"{calendar['calendar_year']} holiday calendar"


@router.get("/api/holiday-calendars/options")
def holiday_calendar_options(_user: dict = Depends(_organization_admin)):
    """Religions assigned to active employees and the years HR may manage."""
    allowed_years = _allowed_calendar_years()
    existing = [
        {"id": str(item["_id"]), "religion": item.get("religion"), "calendar_year": item.get("calendar_year"), "status": item.get("status")}
        for item in holiday_calendars_collection.find(
            {"is_active": True, "religion_key": {"$nin": ["", None]}, "calendar_year": {"$in": allowed_years}},
            {"religion": 1, "calendar_year": 1, "status": 1},
        )
    ]
    # Branch records carry company, legal entity, branch and location: the
    # Company/Legal Entity -> Branch -> Location hierarchy used for assignments.
    organization = [
        {
            "id": str(item["_id"]),
            "company": item.get("company"),
            "legal_entity": item.get("legal_entity"),
            "branch": item.get("branch"),
            "branch_code": item.get("branch_code") or "",
            "location": item.get("location"),
        }
        for item in companies_collection.find({"is_active": {"$ne": False}}).sort([("legal_entity", 1), ("branch", 1)])
    ]
    return {
        "religions": employee_religions(),
        "allowed_years": allowed_years,
        "current_year": allowed_years[0],
        "existing_calendars": existing,
        "organization": organization,
    }


@router.get("/api/my-holidays")
def get_my_religion_holidays(year: int | None = None, user: dict = Depends(_profile_organization_reader)):
    """The signed-in employee's published religion calendar for an allowed year.

    The religion comes from the employee's own record; it is never a parameter.
    """
    allowed_years = _allowed_calendar_years()
    target_year = year or allowed_years[0]
    # Past published calendars stay readable for attendance history.
    if target_year < 2000 or target_year > allowed_years[-1]:
        raise HTTPException(status_code=400, detail=f"Holiday calendars are available up to {allowed_years[-1]}.")
    religion = employee_religion(user)
    key = religion_key(religion)
    calendar = next(
        (
            item for item in holiday_calendars_collection.find(
                {"calendar_year": target_year, "religion_key": key, "status": "Published", "is_active": True}
            )
            if calendar_applies_to(item, user)
        ),
        None,
    ) if key else None
    holidays = []
    if calendar:
        for holiday_day, holiday in published_religion_holidays(user, date(target_year, 1, 1), date(target_year, 12, 31)).items():
            holidays.append({
                "id": str(holiday["_id"]),
                "holiday_date": holiday_day.isoformat(),
                "holiday_name": holiday.get("holiday_name"),
                "holiday_type": holiday.get("holiday_type"),
                "description": holiday.get("description"),
                "is_optional": bool(holiday.get("is_optional")),
            })
    return {
        "religion": religion or None,
        "year": target_year,
        "allowed_years": allowed_years,
        "calendar": {
            "id": str(calendar["_id"]),
            "calendar_name": calendar.get("calendar_name"),
            "calendar_year": calendar.get("calendar_year"),
            "religion": calendar.get("religion"),
            "published_at": calendar.get("published_at"),
            "updated_at": calendar.get("updated_at"),
            "assignments": [
                {key_name: item.get(key_name) for key_name in ("legal_entity", "company", "branch", "location")}
                for item in calendar.get("assignments") or []
            ],
        } if calendar else None,
        "holidays": holidays,
    }


@router.get("/api/holiday-calendars", response_model=list[HolidayCalendarResponse])
def list_holiday_calendars(user: dict = Depends(_organization_reader)):
    query = {"is_active": True}
    if str(user.get("role", "")).casefold() not in {"hr admin", "administrator"}:
        query["status"] = "Published"
    records = [record for record in holiday_calendars_collection.find(query).sort("_id", -1) if _can_view_calendar(user, record)]
    counts = {
        item["_id"]: item["count"]
        for item in holidays_collection.aggregate([
            {"$match": {"calendar_id": {"$in": [str(record["_id"]) for record in records]}, "is_active": True, "status": {"$ne": "Cancelled"}}},
            {"$group": {"_id": "$calendar_id", "count": {"$sum": 1}}},
        ])
    }
    return [serialize_holiday_calendar({**record, "holiday_count": counts.get(str(record["_id"]), 0)}) for record in records]


@router.get("/api/holiday-calendars/{calendar_id}", response_model=HolidayCalendarResponse)
def get_holiday_calendar(
    calendar_id: str,
    user: dict = Depends(_organization_reader),
):
    calendar = get_holiday_calendar_reference(calendar_id)
    if not _can_view_calendar(user, calendar):
        raise HTTPException(status_code=404, detail="Holiday calendar not found")
    return serialize_holiday_calendar(calendar)


@router.put(
    "/api/holiday-calendars/{calendar_id}",
    response_model=HolidayCalendarResponse,
)
def update_holiday_calendar(calendar_id: str, payload: HolidayCalendarPayload, user: dict = Depends(_organization_admin)):
    object_id = parse_object_id(calendar_id, "holiday calendar")
    existing = get_holiday_calendar_reference(calendar_id)
    old_key = existing.get("religion_key") or ""
    # Religion calendars must keep at least one valid organizational assignment.
    assignments = _validate_assignments(payload.assignments, required=bool(old_key or payload.religion))
    if not assignments:
        validate_calendar_location(payload.company_id, payload.location)
    if old_key or payload.religion:
        religion = _resolve_employee_religion(payload.religion) if payload.religion else None
        if not religion:
            raise HTTPException(status_code=422, detail="Select the religion this holiday calendar belongs to.")
    else:
        religion = None
    new_key = religion_key(religion)
    if existing.get("status") == "Published" and (
        new_key != old_key or payload.calendar_year != existing.get("calendar_year")
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The religion and year of a published calendar cannot change. Edit its name or details, or its holidays.",
        )
    if payload.calendar_year != existing.get("calendar_year"):
        _validate_calendar_year(payload.calendar_year)
    if _active_calendar_for(payload.calendar_year, new_key, exclude_id=object_id):
        raise HTTPException(status_code=409, detail="A calendar already exists for this religion and year.")
    changes = {
        **payload.model_dump(exclude={"assignments"}),
        **(_assignment_fields(assignments) if assignments else {}),
        "religion": religion,
        "religion_key": new_key,
        "updated_at": datetime.now(timezone.utc),
    }
    was_published_religion = existing.get("status") == "Published" and bool(old_key)
    before_users = set(calendar_user_ids(existing)) if was_published_religion else set()
    try:
        result = holiday_calendars_collection.update_one({"_id": object_id, "is_active": True}, {"$set": changes})
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="A calendar already exists for this religion and year.") from exc
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Holiday calendar not found")
    _record_organization_audit(
        "holiday_calendar_updated", calendar_id, payload.calendar_year,
        {"calendar_id": calendar_id, "before": existing, "after": changes, "actor_id": str(user["_id"])},
    )
    updated = get_holiday_calendar_reference(calendar_id)
    if was_published_religion:
        # Assignment changes alter who the calendar applies to: tell only the
        # employees who gained or lost it, and flag their draft payroll.
        after_users = set(calendar_user_ids(updated))
        gained, lost = sorted(after_users - before_users), sorted(before_users - after_users)
        stamp = updated.get("updated_at") or datetime.now(timezone.utc)
        if gained:
            _notify_calendar_employees(
                updated, f"{_calendar_label(updated)} now applies to you",
                f"The {_calendar_label(updated)} now applies to your branch. Updated {_ist_stamp()}.",
                f"religion-calendar-gained:{calendar_id}:{stamp.isoformat()}", user_ids=gained,
            )
        if lost:
            _notify_calendar_employees(
                updated, f"{_calendar_label(updated)} no longer applies to you",
                f"The {_calendar_label(updated)} no longer applies to your branch; its holidays are removed from your calendar. Updated {_ist_stamp()}.",
                f"religion-calendar-lost:{calendar_id}:{stamp.isoformat()}", user_ids=lost,
            )
        if gained or lost:
            _reconcile_payroll(updated, _live_holiday_dates(calendar_id), f"{_calendar_label(updated)} assignments changed.", user_ids=gained + lost)
    return serialize_holiday_calendar(updated)


@router.delete("/api/holiday-calendars/{calendar_id}")
def deactivate_holiday_calendar(
    calendar_id: str,
    confirm: str | None = Query(default=None, max_length=64),
    user: dict = Depends(_organization_admin),
):
    """Delete a calendar with its holiday entries and employee assignments.

    Records are deactivated (kept for audit), never erased. A published
    calendar requires ``confirm`` to equal its id (the second confirmation step)
    and keeps counting for attendance dates before today, so past attendance
    and payroll are unchanged; it disappears from every holiday-calendar list.
    """
    calendar = get_holiday_calendar_reference(calendar_id)
    was_published = calendar.get("status") == "Published"
    if was_published and confirm != calendar_id:
        raise HTTPException(status_code=422, detail="Confirm the deletion of this published calendar to continue.")
    affected_users = calendar_user_ids(calendar) if was_published and calendar.get("religion_key") else []
    today = datetime.now(ORGANIZATION_TIMEZONE).date()
    future_dates = [day for day in _live_holiday_dates(calendar_id) if day >= today]
    now = datetime.now(timezone.utc)
    # Atomic claim: only one request can delete the calendar.
    claimed = holiday_calendars_collection.update_one(
        {"_id": calendar["_id"], "is_active": True, "status": calendar.get("status")},
        {"$set": {
            "is_active": False,
            "status": "Deleted",
            "previous_status": calendar.get("status"),
            "was_published": was_published,
            "deleted_at": now,
            "deleted_on": today.isoformat(),
            "deleted_by": str(user["_id"]),
        }},
    )
    if claimed.matched_count == 0:
        raise HTTPException(status_code=409, detail="This calendar was changed or already deleted. Refresh the list.")
    try:
        holidays_result = holidays_collection.update_many(
            {"calendar_id": calendar_id, "is_active": True, "status": {"$in": ["Draft", "Published"]}},
            {"$set": {"status": "Deleted", "is_active": False, "deleted_at": now}},
        )
        assignments_result = employee_holiday_calendars_collection.update_many(
            {"calendar_id": calendar_id, "is_active": True},
            {"$set": {"is_active": False, "unassigned_at": now, "unassigned_reason": "calendar deleted"}},
        )
    except Exception:
        # The calendar is already inactive, so its entries are unreachable; log for follow-up.
        logger.exception("Holiday calendar %s deleted but related records were not fully deactivated", calendar_id)
        holidays_result = assignments_result = None
    _record_organization_audit(
        "holiday_calendar_deleted", calendar_id, calendar["calendar_year"],
        {
            "calendar_id": calendar_id, "calendar": calendar, "actor_id": str(user["_id"]),
            "holidays_deactivated": getattr(holidays_result, "modified_count", None),
            "assignments_deactivated": getattr(assignments_result, "modified_count", None),
        },
    )
    if affected_users:
        _notify_calendar_employees(
            {**calendar, "status": "Published"},
            f"{_calendar_label(calendar)} removed",
            f"The {_calendar_label(calendar)} was removed by HR Admin. Its upcoming holidays no longer apply; past attendance is unchanged. Removed {_ist_stamp()}.",
            f"religion-calendar-deleted:{calendar_id}",
            user_ids=affected_users,
        )
        _reconcile_payroll({**calendar, "status": "Published"}, future_dates, f"{_calendar_label(calendar)} was deleted.", user_ids=affected_users)
    return {
        "id": calendar_id,
        "message": f"{calendar.get('calendar_name')} was deleted.",
        "holidays_deactivated": getattr(holidays_result, "modified_count", 0),
        "assignments_deactivated": getattr(assignments_result, "modified_count", 0),
    }


def _record_organization_audit(action: str, target_id: str, year: int | None, details: dict | None):
    organization_audit_collection.insert_one(
        {
            "action": action,
            "target_id": target_id,
            "calendar_year": year,
            "details": details or {},
            "created_at": datetime.now(timezone.utc),
        }
    )


def _serialize_audit_value(value):
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _serialize_audit_value(item) for key, item in value.items() if key != "_id"}
    if isinstance(value, list):
        return [_serialize_audit_value(item) for item in value]
    return value


@router.get("/api/holiday-calendars/{calendar_id}/audit")
def get_holiday_calendar_audit(
    calendar_id: str,
    _user: dict = Depends(_organization_admin),
):
    if not isinstance(_user, dict):
        raise HTTPException(status_code=401, detail="Authentication required")
    calendar = get_holiday_calendar_reference(calendar_id)
    audit_query: dict = {"calendar_year": calendar["calendar_year"]}
    if calendar.get("religion_key"):
        audit_query = {"$or": [{"target_id": calendar_id}, {"details.calendar_id": calendar_id}]}
    events = organization_audit_collection.find(audit_query).sort("created_at", -1)
    return [
        {
            "action": event.get("action"),
            "target_id": event.get("target_id"),
            "created_at": event.get("created_at"),
            "details": _serialize_audit_value(event.get("details", {})),
        }
        for event in events
    ]


@router.post("/api/holiday-calendars/{calendar_id}/publish")
def publish_holiday_calendar(calendar_id: str, user: dict = Depends(_organization_admin)):
    calendar = get_holiday_calendar_reference(calendar_id)
    if calendar.get("status") == "Published":
        return serialize_holiday_calendar(calendar)
    live_holidays = list(holidays_collection.find(
        {"calendar_id": calendar_id, "is_active": True, "status": {"$ne": "Cancelled"}}
    ))
    if calendar.get("religion_key"):
        if not live_holidays:
            raise HTTPException(status_code=422, detail="Add at least one holiday before publishing this calendar.")
        outside = [item["holiday_name"] for item in live_holidays if _holiday_day(item["holiday_date"]).year != calendar["calendar_year"]]
        if outside:
            raise HTTPException(status_code=422, detail=f"These holidays are outside {calendar['calendar_year']}: {', '.join(outside)}.")
    now = datetime.now(timezone.utc)
    result = holiday_calendars_collection.update_one(
        {"_id": calendar["_id"], "is_active": True, "status": {"$ne": "Published"}},
        {
            "$set": {
                "status": "Published",
                "published_at": now,
                "published_by": str(user["_id"]),
            },
            "$push": {
                "audit_history": {
                    "action": "holiday_calendar_published",
                    "created_at": now,
                    "actor_id": str(user["_id"]),
                }
            },
        },
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=409, detail="Calendar was changed by another request.")
    holidays_collection.update_many(
        {"calendar_id": calendar_id, "is_active": True, "status": "Draft"}, {"$set": {"status": "Published"}}
    )
    _record_organization_audit(
        "holiday_calendar_published", calendar_id, calendar["calendar_year"],
        {"calendar_id": calendar_id, "holiday_count": len(live_holidays), "actor_id": str(user["_id"])},
    )
    published = get_holiday_calendar_reference(calendar_id)
    if published.get("religion_key"):
        _notify_calendar_employees(
            published,
            f"{_calendar_label(published)} published",
            f"The {_calendar_label(published)} is published with {len(live_holidays)} holiday(s). Published {_ist_stamp()}.",
            f"religion-calendar-published:{calendar_id}",
            days=30,
        )
        _reconcile_payroll(published, [_holiday_day(item["holiday_date"]) for item in live_holidays], f"{_calendar_label(published)} was published.")
    return serialize_holiday_calendar(published)


@router.post(
    "/api/holiday-calendars/{calendar_id}/holidays",
    response_model=HolidayResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_holiday(calendar_id: str, payload: HolidayPayload, user: dict = Depends(_organization_admin)):
    calendar = get_holiday_calendar_reference(calendar_id)
    is_religion_calendar = bool(calendar.get("religion_key"))
    if calendar.get("status") == "Published" and not is_religion_calendar:
        raise HTTPException(status_code=409, detail="Published calendars require an amendment workflow.")
    if payload.calendar_id != calendar_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Holiday calendar id does not match the route",
        )
    validate_holiday_date(calendar, payload.holiday_date)
    _reject_duplicate_holiday_date(calendar_id, payload.holiday_date)
    is_published = calendar.get("status") == "Published"
    holiday = {
        **payload.model_dump(),
        **_religion_calendar_scope(calendar),
        "calendar_id": calendar_id,
        "holiday_date": datetime.combine(
            payload.holiday_date, datetime.min.time(), tzinfo=timezone.utc
        ),
        "is_active": True,
        # Additions to a published religion calendar are official immediately.
        "status": "Published" if is_published else "Draft",
        "created_at": datetime.now(timezone.utc),
        "created_by": str(user["_id"]),
    }
    try:
        result = holidays_collection.insert_one(holiday)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="A holiday already exists on this date.") from exc
    holiday["_id"] = result.inserted_id
    _record_organization_audit(
        "holiday_added", str(result.inserted_id), calendar["calendar_year"],
        {**holiday, "calendar_id": calendar_id, "actor_id": str(user["_id"])},
    )
    if is_published:
        holiday_text = payload.holiday_date.strftime("%d %b %Y")
        _notify_calendar_employees(
            calendar,
            f"Holiday added: {payload.holiday_name}",
            f"{payload.holiday_name} on {holiday_text} was added to the {_calendar_label(calendar)}. Added {_ist_stamp()}.",
            f"holiday-added:{result.inserted_id}",
        )
        _reconcile_payroll(calendar, [payload.holiday_date], f"Holiday {payload.holiday_name} was added on {holiday_text}.")
    return serialize_holiday(holiday)


def _religion_calendar_scope(calendar: dict) -> dict:
    """Holidays in a religion calendar apply to that whole religion group."""
    if not calendar.get("religion_key"):
        return {}
    return {
        "applicability": "All Employees",
        "applicable_religions": [calendar.get("religion")],
        "applicable_religion": calendar.get("religion"),
        "applicable_employee_groups": [],
    }


def _reject_duplicate_holiday_date(calendar_id: str, holiday_date: date, exclude_id: ObjectId | None = None) -> None:
    query: dict = {
        "calendar_id": calendar_id,
        "is_active": True,
        "status": {"$ne": "Cancelled"},
        "holiday_date": datetime.combine(holiday_date, datetime.min.time(), tzinfo=timezone.utc),
    }
    if exclude_id is not None:
        query["_id"] = {"$ne": exclude_id}
    existing = holidays_collection.find_one(query, {"holiday_name": 1})
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"{existing.get('holiday_name', 'A holiday')} is already on {holiday_date.strftime('%d %b %Y')} in this calendar.",
        )


@router.get(
    "/api/holiday-calendars/{calendar_id}/holidays",
    response_model=list[HolidayResponse],
)
def list_calendar_holidays(
    calendar_id: str,
    user: dict = Depends(_organization_reader),
):
    calendar = get_holiday_calendar_reference(calendar_id)
    if isinstance(user, dict) and not _can_view_calendar(user, calendar):
        raise HTTPException(status_code=404, detail="Holiday calendar not found")
    records = holidays_collection.find(
        {"calendar_id": calendar_id, "is_active": True}
    ).sort("holiday_date", 1)
    serialized = []
    for record in records:
        if calendar.get("status") == "Published" and record.get("status") != "Cancelled":
            record["status"] = "Published"
        serialized.append(serialize_holiday(record))
    return serialized


@router.put("/api/holidays/{holiday_id}", response_model=HolidayResponse)
def update_holiday(
    holiday_id: str,
    payload: HolidayPayload,
    reason: str | None = Query(default=None, min_length=3, max_length=500),
):
    calendar = get_holiday_calendar_reference(payload.calendar_id)
    validate_holiday_date(calendar, payload.holiday_date)
    object_id = parse_object_id(holiday_id, "holiday")
    previous = get_holiday_reference(holiday_id)
    if previous.get("calendar_id") != payload.calendar_id:
        raise HTTPException(status_code=400, detail="A holiday cannot be moved to a different calendar.")
    _reject_duplicate_holiday_date(payload.calendar_id, payload.holiday_date, exclude_id=object_id)
    if previous.get("status") == "Cancelled":
        raise HTTPException(status_code=409, detail="A cancelled holiday cannot be edited.")
    previous_date = previous["holiday_date"]
    if isinstance(previous_date, datetime):
        previous_date = previous_date.date()
    if previous_date < datetime.now(ORGANIZATION_TIMEZONE).date():
        raise HTTPException(status_code=400, detail="Past holidays cannot be changed.")
    # Religion-calendar scope is applied first so resubmitting the same values is a no-op.
    submitted_fields = {
        **payload.model_dump(),
        **_religion_calendar_scope(calendar),
        # Status is managed by the server (draft/publish/cancel), never by the client.
        "status": previous.get("status", "Draft"),
    }
    change_detected = any(
        (previous_date if field == "holiday_date" else previous.get(field))
        != (payload.holiday_date if field == "holiday_date" else value)
        for field, value in submitted_fields.items()
    )
    if not change_detected:
        return serialize_holiday(previous)
    if calendar.get("status") == "Published" and not reason:
        raise HTTPException(status_code=422, detail="Provide a reason for changing a published holiday.")
    update_time = datetime.now(timezone.utc)
    update_fields = {
        **submitted_fields,
        "holiday_date": datetime.combine(
            payload.holiday_date, datetime.min.time(), tzinfo=timezone.utc
        ),
        "updated_at": update_time,
    }
    if calendar.get("status") == "Published":
        update_fields.update(
            {"status": "Published", "amended_at": datetime.now(timezone.utc), "amendment_reason": reason}
        )
    try:
        result = holidays_collection.update_one(
        {"_id": object_id, "is_active": True},
        {"$set": update_fields},
        )
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="A holiday already exists on this date.") from exc
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Holiday not found")
    updated = get_holiday_reference(holiday_id)
    _record_organization_audit(
        "holiday_changed", holiday_id, calendar["calendar_year"],
        {"calendar_id": payload.calendar_id, "before": previous, "after": update_fields, "reason": reason},
    )
    if calendar.get("status") == "Published":
        _notify_calendar_employees(
            calendar,
            f"Holiday updated: {payload.holiday_name}",
            (
                f"{previous.get('holiday_name')} ({previous_date.strftime('%d %b %Y')}) in the {_calendar_label(calendar)} "
                f"is now {payload.holiday_name} on {payload.holiday_date.strftime('%d %b %Y')}. Reason: {reason}. "
                f"Modified {_ist_stamp()}."
            ),
            "holiday-change:" + hashlib.sha256(
                f"{holiday_id}:{previous.get('updated_at')}:{payload.model_dump_json()}:{reason or ''}".encode()
            ).hexdigest(),
            days=7,
        )
        _reconcile_payroll(calendar, [previous_date, payload.holiday_date], f"Holiday {payload.holiday_name} was changed.")
    return serialize_holiday(updated)


@router.delete("/api/holidays/{holiday_id}")
def deactivate_holiday(
    holiday_id: str,
    reason: str | None = Query(default=None, min_length=3, max_length=500),
):
    previous = get_holiday_reference(holiday_id)
    if previous.get("status") == "Cancelled":
        raise HTTPException(status_code=409, detail="Holiday is already cancelled.")
    old_date = previous["holiday_date"]
    if isinstance(old_date, datetime):
        old_date = old_date.date()
    if old_date < datetime.now(ORGANIZATION_TIMEZONE).date():
        raise HTTPException(status_code=400, detail="Past holidays cannot be removed.")
    calendar = get_holiday_calendar_reference(previous["calendar_id"])
    if calendar.get("status") == "Published" and not reason:
        raise HTTPException(status_code=422, detail="Provide a reason for cancelling a published holiday.")
    object_id = parse_object_id(holiday_id, "holiday")
    result = holidays_collection.update_one(
        {"_id": object_id, "is_active": True},
        {
            "$set": {
                "status": "Cancelled",
                "cancelled_at": datetime.now(timezone.utc),
                "cancellation_reason": reason,
                "is_active": True,
            }
        },
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Holiday not found")
    _record_organization_audit(
        "holiday_removed", holiday_id, calendar["calendar_year"],
        {"calendar_id": previous["calendar_id"], "holiday": previous, "reason": reason},
    )
    if calendar.get("status") == "Published":
        _notify_calendar_employees(
            calendar,
            f"Holiday removed: {previous.get('holiday_name')}",
            (
                f"{previous.get('holiday_name')} on {old_date.strftime('%d %b %Y')} was removed from the "
                f"{_calendar_label(calendar)}; it is now a regular working day. Reason: {reason}. Modified {_ist_stamp()}."
            ),
            f"holiday-cancel:{holiday_id}",
            days=7,
        )
        _reconcile_payroll(calendar, [old_date], f"Holiday {previous.get('holiday_name')} was removed.")
    return {"id": holiday_id, "message": "Holiday deactivated successfully"}


@router.post(
    "/api/employee-holiday-calendars",
    response_model=EmployeeHolidayCalendarResponse,
    status_code=status.HTTP_201_CREATED,
)
def assign_employee_holiday_calendar(payload: EmployeeHolidayCalendarPayload):
    validate_employee_reference(payload.employee_id)
    calendar = get_holiday_calendar_reference(payload.calendar_id)
    if calendar.get("religion_key"):
        raise HTTPException(
            status_code=400,
            detail="Religion holiday calendars apply automatically to employees of that religion; no assignment is needed.",
        )
    existing = employee_holiday_calendars_collection.find_one(
        {
            "employee_id": payload.employee_id,
            "calendar_id": payload.calendar_id,
            "is_active": True,
        }
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Employee already has an active holiday calendar",
        )
    for assignment in employee_holiday_calendars_collection.find(
        {"employee_id": payload.employee_id, "is_active": True}
    ):
        assigned_calendar = holiday_calendars_collection.find_one(
            {"_id": parse_object_id(assignment["calendar_id"], "holiday calendar")}
        )
        if assigned_calendar and assigned_calendar.get("calendar_year") == calendar["calendar_year"]:
            raise HTTPException(
                status_code=409,
                detail=f"Employee already has an active holiday calendar for {calendar['calendar_year']}.",
            )
    assignment = {
        **payload.model_dump(),
        "assigned_at": datetime.now(timezone.utc),
        "is_active": True,
    }
    result = employee_holiday_calendars_collection.insert_one(assignment)
    assignment["_id"] = result.inserted_id
    return serialize_employee_holiday_calendar(assignment)


@router.get(
    "/api/employee-holiday-calendars",
    response_model=list[EmployeeHolidayCalendarResponse],
)
def list_employee_holiday_calendars(user: dict = Depends(_organization_reader)):
    role = str(user.get("role", "")).casefold()
    query = {"is_active": True}
    if role not in {"hr admin", "administrator", "hr manager"}:
        employee_id = str(user.get("employee_id") or "")
        user_id = str(user["_id"])
        employee = employees_collection.find_one({"employee_id": employee_id}) if employee_id else None
        assignment_ids = [user_id, employee_id]
        if employee:
            assignment_ids.append(str(employee["_id"]))
        query["employee_id"] = {"$in": [value for value in assignment_ids if value]}
    records = employee_holiday_calendars_collection.find(query).sort("assigned_at", -1)
    return [serialize_employee_holiday_calendar(record) for record in records]


@router.get(
    "/api/employees/{employee_id}/holiday-calendar",
    response_model=HolidayCalendarResponse,
)
def get_employee_holiday_calendar(
    employee_id: str,
    user: dict = Depends(_organization_reader),
):
    employee = validate_employee_reference(employee_id)
    if isinstance(user, dict):
        role = str(user.get("role", "")).casefold()
        own_employee_id = str(user.get("employee_id") or "")
        is_admin_or_manager = role in {"hr admin", "administrator", "hr manager", "manager"}
        is_self = employee_id in {str(user["_id"]), own_employee_id, str(employee.get("_id"))}
        if not is_admin_or_manager and not is_self:
            raise HTTPException(status_code=403, detail="You may only view your own holiday calendar.")
    assignment = employee_holiday_calendars_collection.find_one(
        {"employee_id": employee_id, "is_active": True}
    )
    if assignment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active holiday calendar assigned to employee",
        )
    calendar = get_holiday_calendar_reference(assignment["calendar_id"])
    if (
        isinstance(user, dict)
        and str(user.get("role", "")).casefold() not in {"hr admin", "administrator", "hr manager", "manager"}
        and calendar.get("status") != "Published"
    ):
        raise HTTPException(status_code=404, detail="Holiday calendar not found")
    return serialize_holiday_calendar(calendar)


@router.delete("/api/employee-holiday-calendars/{assignment_id}")
def unassign_employee_holiday_calendar(assignment_id: str):
    object_id = parse_object_id(assignment_id, "employee holiday calendar assignment")
    result = employee_holiday_calendars_collection.update_one(
        {"_id": object_id, "is_active": True},
        {"$set": {"is_active": False}},
    )
    if result.matched_count == 0:
        raise HTTPException(
            status_code=404,
            detail="Employee holiday calendar assignment not found",
        )
    return {
        "id": assignment_id,
        "message": "Employee holiday calendar assignment removed successfully",
    }


@router.post(
    "/api/employees",
    response_model=EmployeeResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_employee(payload: EmployeePayload):
    now = datetime.now(timezone.utc)
    employee = {
        **payload.model_dump(),
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    }
    result = employees_collection.insert_one(employee)
    employee["_id"] = result.inserted_id
    return serialize_employee(employee)


@router.get("/api/employees", response_model=list[EmployeeResponse])
def list_employees():
    employees = employees_collection.find({"is_active": True}).sort("created_at", -1)
    return [serialize_employee(employee) for employee in employees]


@router.get("/api/employees/{employee_id}", response_model=EmployeeResponse)
def get_employee(employee_id: str):
    return get_active_employee(employee_id)


@router.put("/api/employees/{employee_id}", response_model=EmployeeResponse)
def update_employee(employee_id: str, payload: EmployeePayload):
    object_id = parse_employee_id(employee_id)
    now = datetime.now(timezone.utc)
    result = employees_collection.update_one(
        {"_id": object_id, "is_active": True},
        {"$set": {**payload.model_dump(), "updated_at": now}},
    )
    if result.matched_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Employee not found",
        )
    return get_active_employee(employee_id)


@router.delete(
    "/api/employees/{employee_id}",
    summary="Deactivate an employee",
    description=(
        "Marks the employee inactive. The document is retained; permanent deletion "
        "policy remains TBD."
    ),
)
def deactivate_employee(employee_id: str):
    object_id = parse_employee_id(employee_id)
    now = datetime.now(timezone.utc)
    result = employees_collection.update_one(
        {"_id": object_id, "is_active": True},
        {"$set": {"is_active": False, "updated_at": now, "deactivated_at": now}},
    )
    if result.matched_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Employee not found",
        )
    return {"id": employee_id, "message": "Employee deactivated successfully"}


@router.get("/health")
def health_check():
    client.admin.command("ping")

    return {
        "status": "ok",
        "message": "HRMS Backend is running",
        "database": MONGODB_DATABASE
    }
