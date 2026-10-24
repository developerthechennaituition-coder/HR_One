from datetime import date, datetime, timezone
import re

from bson import ObjectId
from fastapi import APIRouter, Header, HTTPException, Query, status
from pydantic import BaseModel, field_validator

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
        }

    return {
        "date_of_birth": serialize_date(record.get("date_of_birth")),
        "blood_group": record.get("blood_group") or "",
        "date_of_joining": serialize_date(record.get("date_of_joining")),
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
    }
