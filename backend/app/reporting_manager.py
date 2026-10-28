from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app.db.mongodb import db
from app.session import active_sessions


router = APIRouter(
    prefix="/api/reporting-manager",
    tags=["Reporting Manager"],
)


users_collection = db["users"]
employee_details_collection = db["employee_details"]
attendance_collection = db["attendance"]
leave_requests_collection = db["leave_requests"]
leave_types_collection = db["leave_types"]


# ============================================================
# MODELS
# ============================================================

class ReportingManagerPayload(BaseModel):
    reporting_manager_id: str | None = None


# ============================================================
# AUTHENTICATION
# ============================================================

def _current_user(authorization: str | None) -> dict[str, Any]:
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
        )

    token = authorization.removeprefix("Bearer ").strip()

    user_id = active_sessions.get(token)

    if not user_id or not ObjectId.is_valid(user_id):
        raise HTTPException(
            status_code=401,
            detail="Session expired. Please login again.",
        )

    user = users_collection.find_one(
        {
            "_id": ObjectId(user_id),
            "is_active": True,
        }
    )

    if not user:
        raise HTTPException(
            status_code=401,
            detail="User not found or inactive",
        )

    return user


def _require_hr_admin(user: dict[str, Any]) -> None:
    role = str(user.get("role", "")).strip().lower()

    if role != "hr admin":
        raise HTTPException(
            status_code=403,
            detail="Only HR Admin can manage Reporting Manager assignments",
        )


def _is_reporting_manager(user_id: str) -> bool:
    """
    A user is considered a Reporting Manager when at least one
    active user has this user's ID as reporting_manager_id.
    """

    if not ObjectId.is_valid(user_id):
        return False

    manager = users_collection.find_one(
        {
            "reporting_manager_id": user_id,
            "is_active": True,
        },
        {
            "_id": 1,
        },
    )

    return manager is not None


def _require_reporting_manager(user: dict[str, Any]) -> None:
    user_id = str(user["_id"])

    if not _is_reporting_manager(user_id):
        raise HTTPException(
            status_code=403,
            detail="Only Reporting Managers can access Team Members",
        )


# ============================================================
# SERIALIZERS
# ============================================================

def _serialize_manager(
    user: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": str(user["_id"]),
        "employee_id": user.get("employee_id"),
        "name": user.get("name"),
        "work_email": user.get("work_email"),
        "country_code": user.get("country_code"),
        "mobile": user.get("mobile"),
        "role": user.get("role"),
        "is_active": bool(user.get("is_active")),
    }


def _serialize_team_member(
    user: dict[str, Any],
    details: dict[str, Any] | None = None,
    reporting_manager: dict[str, Any] | None = None,
) -> dict[str, Any]:

    details = details or {}

    return {
        "id": str(user["_id"]),

        "employee_id": (
            user.get("employee_id")
            or details.get("employee_id")
        ),

        "name": (
            user.get("name")
            or details.get("employee_name")
            or ""
        ),

        "work_email": (
            user.get("work_email")
            or details.get("email")
            or ""
        ),

        "mobile": (
            user.get("mobile")
            or details.get("phone")
            or ""
        ),

        "role": user.get("role"),

        "is_active": bool(
            user.get("is_active")
        ),

        # Employee details
        "date_of_birth": details.get("date_of_birth"),
        "blood_group": details.get("blood_group"),
        "date_of_joining": details.get("date_of_joining"),
        "work_field": details.get("work_field"),
        "nationality": details.get("nationality"),
        "religion": details.get("religion"),
        "address": details.get("address"),
        "gender": details.get("gender"),
        "marital_status": details.get("marital_status"),
        "city": details.get("city"),
        "state": details.get("state"),
        "country": details.get("country") or "",
        "mobile_country": user.get("country") or user.get("country_iso"),
        "country_iso": user.get("country_iso"),
        "country_code": user.get("country_code"),

        "department": (
            details.get("department")
            or user.get("department")
        ),

        "designation": (
            details.get("designation")
            or user.get("designation")
        ),

        "status": (
            details.get("status")
            or ("Active" if user.get("is_active") else "Inactive")
        ),

        "reporting_manager_id": user.get(
            "reporting_manager_id"
        ),
        "reporting_manager": (
            _serialize_manager(reporting_manager)
            if reporting_manager
            else None
        ),
    }


def _serialize_attendance(record: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}

    for key, value in record.items():

        if key == "_id":
            result["id"] = str(value)

        elif isinstance(value, datetime) and value.tzinfo is None:
            result[key] = value.replace(tzinfo=timezone.utc).isoformat()

        elif isinstance(value, ObjectId):
            result[key] = str(value)

        else:
            result[key] = value

    return result


# ============================================================
# REPORTING MANAGER STATUS
# ============================================================

@router.get("/me")
def reporting_manager_status(
    authorization: str | None = Header(default=None),
):
    """
    Returns whether the currently logged-in user is a
    Reporting Manager.

    This endpoint is used by Header.jsx to decide whether
    Team Members should be displayed.
    """

    user = _current_user(authorization)

    user_id = str(user["_id"])

    is_manager = _is_reporting_manager(user_id)

    return {
        "is_reporting_manager": is_manager,
        "user_id": user_id,
        "team_member_count": (
            users_collection.count_documents(
                {
                    "reporting_manager_id": user_id,
                    "is_active": True,
                }
            )
            if is_manager
            else 0
        ),
    }


# ============================================================
# REPORTING MANAGER OPTIONS
# ============================================================

@router.get("/options")
def reporting_manager_options(
    authorization: str | None = Header(default=None),
):
    user = _current_user(authorization)

    _require_hr_admin(user)

    records = users_collection.find(
        {
            "is_active": True,
        }
    ).sort(
        "name",
        1,
    )

    return [
        _serialize_manager(record)
        for record in records
    ]


# ============================================================
# GET REPORTING MANAGER OF AN EMPLOYEE
# ============================================================

@router.get("/{user_id}")
def get_reporting_manager(
    user_id: str,
    authorization: str | None = Header(default=None),
):
    viewer = _current_user(authorization)

    target_id = (
        ObjectId(user_id)
        if ObjectId.is_valid(user_id)
        else None
    )

    if not target_id:
        raise HTTPException(
            status_code=400,
            detail="Invalid employee user ID",
        )

    if str(viewer["_id"]) != user_id:
        _require_hr_admin(viewer)

    target = users_collection.find_one(
        {
            "_id": target_id,
        }
    )

    if not target:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    manager_id = target.get(
        "reporting_manager_id"
    )

    if not manager_id:

        details = employee_details_collection.find_one(
            {
                "user_id": user_id,
            },
            {
                "reporting_manager_id": 1,
            },
        )

        manager_id = (
            details.get("reporting_manager_id")
            if details
            else None
        )

    manager = None

    if (
        manager_id
        and ObjectId.is_valid(str(manager_id))
    ):
        manager = users_collection.find_one(
            {
                "_id": ObjectId(str(manager_id)),
                "is_active": True,
            }
        )

    return {
        "user_id": user_id,
        "reporting_manager_id": (
            str(manager["_id"])
            if manager
            else None
        ),
        "reporting_manager": (
            _serialize_manager(manager)
            if manager
            else None
        ),
    }


# ============================================================
# SET REPORTING MANAGER
# ============================================================

@router.put("/{user_id}")
def set_reporting_manager(
    user_id: str,
    payload: ReportingManagerPayload,
    authorization: str | None = Header(default=None),
):
    actor = _current_user(authorization)

    _require_hr_admin(actor)

    if not ObjectId.is_valid(user_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid employee user ID",
        )

    target = users_collection.find_one(
        {
            "_id": ObjectId(user_id),
            "is_active": True,
        }
    )

    if not target:
        raise HTTPException(
            status_code=404,
            detail="Active employee not found",
        )

    manager_id = (
        payload.reporting_manager_id.strip()
        if payload.reporting_manager_id
        else None
    )

    if manager_id:

        if not ObjectId.is_valid(manager_id):
            raise HTTPException(
                status_code=400,
                detail="Invalid reporting manager ID",
            )

        if manager_id == user_id:
            raise HTTPException(
                status_code=400,
                detail=(
                    "An employee cannot be their own "
                    "reporting manager"
                ),
            )

        manager = users_collection.find_one(
            {
                "_id": ObjectId(manager_id),
                "is_active": True,
            }
        )

        if not manager:
            raise HTTPException(
                status_code=404,
                detail=(
                    "Reporting manager not found or inactive"
                ),
            )

        # Prevent circular reporting hierarchy.
        visited = {user_id}

        current_id = manager_id

        while current_id:

            if current_id in visited:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Reporting Manager assignment would "
                        "create a circular reporting hierarchy"
                    ),
                )

            visited.add(current_id)

            current = users_collection.find_one(
                {
                    "_id": ObjectId(current_id),
                },
                {
                    "reporting_manager_id": 1,
                },
            )

            next_id = (
                current.get("reporting_manager_id")
                if current
                else None
            )

            current_id = (
                str(next_id)
                if next_id
                else ""
            )

    now = datetime.now(timezone.utc)

    if manager_id:

        users_collection.update_one(
            {
                "_id": ObjectId(user_id),
            },
            {
                "$set": {
                    "reporting_manager_id": manager_id,
                    "updated_at": now,
                }
            },
        )

        employee_details_collection.update_one(
            {
                "user_id": user_id,
            },
            {
                "$set": {
                    "reporting_manager_id": manager_id,
                    "updated_at": now,
                }
            },
            upsert=True,
        )

    else:

        users_collection.update_one(
            {
                "_id": ObjectId(user_id),
            },
            {
                "$unset": {
                    "reporting_manager_id": "",
                },
                "$set": {
                    "updated_at": now,
                },
            },
        )

        employee_details_collection.update_one(
            {
                "user_id": user_id,
            },
            {
                "$unset": {
                    "reporting_manager_id": "",
                },
                "$set": {
                    "updated_at": now,
                },
            },
        )

    manager = None

    if manager_id:

        manager = users_collection.find_one(
            {
                "_id": ObjectId(manager_id),
                "is_active": True,
            }
        )

    return {
        "user_id": user_id,
        "reporting_manager_id": (
            str(manager["_id"])
            if manager
            else None
        ),
        "reporting_manager": (
            _serialize_manager(manager)
            if manager
            else None
        ),
    }


# ============================================================
# TEAM MEMBERS
# ============================================================

@router.get("/team-members/list")
def get_team_members(
    authorization: str | None = Header(default=None),
):
    """
    Returns only the direct reports of the logged-in
    Reporting Manager.
    """

    manager = _current_user(authorization)

    _require_reporting_manager(manager)

    manager_id = str(manager["_id"])

    users = list(
        users_collection.find(
            {
                "reporting_manager_id": manager_id,
                "is_active": True,
            }
        ).sort(
            "name",
            1,
        )
    )

    result = []

    for user in users:

        user_id = str(user["_id"])

        details = employee_details_collection.find_one(
            {
                "user_id": user_id,
            }
        )

        result.append(
            _serialize_team_member(
                user,
                details,
                manager,
            )
        )

    return {
        "reporting_manager": {
            "id": manager_id,
            "employee_id": manager.get("employee_id"),
            "name": manager.get("name"),
        },
        "count": len(result),
        "team_members": result,
    }


# ============================================================
# TEAM MEMBER FULL PROFILE
# ============================================================

@router.get("/team-members/{member_id}")
def get_team_member(
    member_id: str,
    authorization: str | None = Header(default=None),
):
    """
    Returns full read-only profile of a direct team member.
    """

    manager = _current_user(authorization)

    _require_reporting_manager(manager)

    if not ObjectId.is_valid(member_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid employee ID",
        )

    manager_id = str(manager["_id"])

    member = users_collection.find_one(
        {
            "_id": ObjectId(member_id),
            "reporting_manager_id": manager_id,
            "is_active": True,
        }
    )

    if not member:
        raise HTTPException(
            status_code=404,
            detail=(
                "Employee is not a member of your team"
            ),
        )

    details = employee_details_collection.find_one(
        {
            "user_id": member_id,
        }
    )

    return _serialize_team_member(
        member,
        details,
        manager,
    )

# ============================================================
# TEAM MEMBER ATTENDANCE
# ============================================================

@router.get("/team-members/{member_id}/attendance")
def get_team_member_attendance(
    member_id: str,
    year: int | None = None,
    month: int | None = None,
    attendance_type: str | None = None,
    authorization: str | None = Header(default=None),
):
    """
    Returns attendance only for a direct team member.

    Read-only endpoint for Reporting Managers.
    """

    # --------------------------------------------------------
    # Authenticate current user
    # --------------------------------------------------------

    manager = _current_user(authorization)

    _require_reporting_manager(manager)

    # --------------------------------------------------------
    # Validate employee MongoDB ID
    # --------------------------------------------------------

    if not ObjectId.is_valid(member_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid employee ID",
        )

    manager_id = str(manager["_id"])

    # --------------------------------------------------------
    # Verify that employee belongs to this manager
    # --------------------------------------------------------

    member = users_collection.find_one(
        {
            "_id": ObjectId(member_id),
            "reporting_manager_id": manager_id,
            "is_active": True,
        }
    )

    if not member:
        raise HTTPException(
            status_code=404,
            detail="Employee is not a member of your team",
        )

    # --------------------------------------------------------
    # Base attendance query
    # --------------------------------------------------------

    query: dict[str, Any] = {
        "user_id": member_id,
    }

    # --------------------------------------------------------
    # IMPORTANT
    #
    # attendance_date is stored as STRING in the HRMS:
    #
    # "2026-10-01"
    #
    # Therefore DO NOT use Python datetime objects here.
    # --------------------------------------------------------

    if year is not None and month is not None:

        if month < 1 or month > 12:
            raise HTTPException(
                status_code=400,
                detail="Invalid month",
            )

        # Start of selected month
        start_date = (
            f"{year:04d}-{month:02d}-01"
        )

        # Calculate next month
        if month == 12:
            next_year = year + 1
            next_month = 1
        else:
            next_year = year
            next_month = month + 1

        # Start of next month
        end_date = (
            f"{next_year:04d}-{next_month:02d}-01"
        )

        # ----------------------------------------------------
        # String range query
        # ----------------------------------------------------

        query["attendance_date"] = {
            "$gte": start_date,
            "$lt": end_date,
        }

    # --------------------------------------------------------
    # Get attendance records
    # --------------------------------------------------------

    records = list(
        attendance_collection.find(
            query
        ).sort(
            "attendance_date",
            1,
        )
    )

    approved_leaves = []

    if year is not None and month is not None:
        leave_records = leave_requests_collection.find(
            {
                "user_id": member_id,
                "status": "approved",
                "from_date": {"$lt": end_date},
                "to_date": {"$gte": start_date},
            }
        ).sort("from_date", 1)

        for leave in leave_records:
            leave_type_id = leave.get("leave_type_id")
            leave_type_query = (
                {"_id": ObjectId(str(leave_type_id))}
                if ObjectId.is_valid(str(leave_type_id))
                else {"_id": leave_type_id}
            )
            leave_type = leave_types_collection.find_one(
                leave_type_query
            )
            serialized_leave = _serialize_attendance(leave)
            serialized_leave["leave_type_code"] = (
                leave_type.get("code") if leave_type else None
            )
            serialized_leave["leave_type_name"] = (
                leave_type.get("name") if leave_type else None
            )
            approved_leaves.append(serialized_leave)

    # Published holidays that apply to this member (religion + assigned calendar).
    holidays = []
    if year is not None and month is not None:
        from calendar import monthrange
        from datetime import date

        from app.leave import _holiday_names

        month_holidays = _holiday_names(member, date(year, month, 1), date(year, month, monthrange(year, month)[1]))
        holidays = [
            {"holiday_date": day.isoformat(), "holiday_name": name}
            for day, name in sorted(month_holidays.items())
        ]

    attendance_type=attendance_collection.distinct(
        "attendance_type",
        {"user_id": member_id},
    )

    # --------------------------------------------------------
    # Return response
    # --------------------------------------------------------

    return {
        "employee": {
            "id": str(member["_id"]),
            "employee_id": member.get(
                "employee_id"
            ),
            "name": member.get(
                "name"
            ),
        },

        "count": len(records),

        "attendance": [
            _serialize_attendance(record)
            for record in records
        ],
        "approved_leaves": approved_leaves,
        "holidays": holidays,
        "attendance_type":attendance_type,
    }