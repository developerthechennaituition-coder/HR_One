from datetime import datetime, timedelta, timezone
from typing import Literal

from bson import ObjectId
from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.db.mongodb import db


router = APIRouter(
    prefix="/api/notifications",
    tags=["Notifications"],
)


# ============================================================
# MongoDB Collections
# ============================================================

notifications_collection = db["notifications"]
notification_recipients_collection = db["notification_recipients"]
leave_notifications_collection = db["leave_notifications"]
notification_sequences_collection = db["notification_sequences"]
employee_details_collection = db["employee_details"]
departments_collection = db["departments"]


# ============================================================
# Constants
# ============================================================

ALLOWED_NOTIFICATION_ROLES = {
    "hr admin",
    "manager",
    "administrator",
}


# ============================================================
# Date Helpers
# ============================================================

UTC = timezone.utc


def utc_now():
    return datetime.now(UTC)


def ensure_utc(value):
    """
    MongoDB may return naive UTC datetime depending on MongoClient
    configuration. Convert it to timezone-aware UTC.
    """

    if value is None:
        return None

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)

    return value.astimezone(UTC)


# ============================================================
# ObjectId Helper
# ============================================================

def parse_object_id(value: str, field_name: str = "id"):
    try:
        return ObjectId(value)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {field_name}",
        )


# ============================================================
# Current User Authentication
# ============================================================

def get_authenticated_user(
    authorization: str | None = Header(default=None),
):
    """
    Uses the existing HRMS active_sessions dictionary.

    Current project:
        active_sessions[token] = user_id
    """

    if not isinstance(authorization, str):
        authorization = None

    token = (
        authorization.removeprefix("Bearer ").strip()
        if authorization
        else None
    )

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    # Import inside function to avoid circular import.
    from app.main import active_sessions

    session_user_id = active_sessions.get(token)

    if not session_user_id or not isinstance(session_user_id, str):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
        )

    try:
        user_object_id = ObjectId(session_user_id)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user session",
        )

    user = notifications_users_collection().find_one(
        {"_id": user_object_id}
    )

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    if not user.get("is_active", False):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account is inactive",
        )

    return user


def notifications_users_collection():
    """
    Return existing HRMS users collection.
    """

    return db["users"]


# ============================================================
# Role Permission
# ============================================================

def require_notification_manager(user):
    role = str(user.get("role", "")).strip().lower()

    if role not in ALLOWED_NOTIFICATION_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Only HR Admin, Manager or Administrator "
                "can manage notifications"
            ),
        )

    return user


# ============================================================
# Pydantic Models
# ============================================================

class NotificationAudience(BaseModel):
    type: Literal["all", "roles", "teams", "departments", "users"]

    role_ids: list[str] = Field(default_factory=list)
    team_ids: list[str] = Field(default_factory=list)
    department_ids: list[str] = Field(default_factory=list)
    user_ids: list[str] = Field(default_factory=list)


class NotificationCreate(BaseModel):
    title: str = Field(
        min_length=1,
        max_length=200,
    )

    message: str = Field(
        min_length=1,
        max_length=5000,
    )

    audience: NotificationAudience

    validity_value: int | None = Field(default=None, gt=0, le=24 * 60 * 30)
    validity_unit: Literal["minutes", "hours", "days", "weeks"] = "hours"
    validity_hours: int | None = Field(default=None, gt=0, le=24 * 30 * 7)
    status: Literal["ACTIVE", "HOLD"] = "ACTIVE"

    def duration(self) -> timedelta:
        value = self.validity_value
        unit = self.validity_unit
        if value is None:
            value = self.validity_hours
            unit = "hours"
        if value is None or value <= 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Validity must be a positive number with a supported unit.",
            )
        duration = timedelta(**{unit: value})
        if duration > timedelta(days=30):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Notification validity cannot exceed 30 days.",
            )
        return duration


class NotificationUpdate(BaseModel):
    title: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
    )

    message: str | None = Field(
        default=None,
        min_length=1,
        max_length=5000,
    )
    audience: NotificationAudience | None = None
    validity_value: int | None = Field(default=None, gt=0, le=24 * 60 * 30)
    validity_unit: Literal["minutes", "hours", "days", "weeks"] | None = None
    status: Literal["ACTIVE", "HOLD"] | None = None


# ============================================================
# Index Creation
# ============================================================

def create_notification_indexes():
    """
    Call this from main.py startup.
    """

    leave_notifications_collection.create_index(
        [
            ("recipient_user_id", 1),
            ("is_read", 1),
            ("is_starred", 1),
            ("expires_at", 1),
            ("created_at", -1),
        ]
    )

    notifications_collection.create_index(
        [
            ("created_by_user_id", 1),
            ("created_at", -1),
        ]
    )

    notifications_collection.create_index(
        [
            ("expires_at", 1),
            ("is_deleted", 1),
            ("status", 1),
        ]
    )

    notification_recipients_collection.create_index(
        [
            ("notification_id", 1),
            ("user_id", 1),
        ],
        unique=True,
    )

    notification_recipients_collection.create_index(
        [
            ("user_id", 1),
            ("acknowledged_at", 1),
        ]
    )

    notification_recipients_collection.create_index(
        [
            ("user_id", 1),
            ("is_read", 1),
            ("is_deleted", 1),
        ]
    )
    notification_recipients_collection.create_index(
        [
            ("user_id", 1),
            ("is_starred", 1),
            ("is_deleted", 1),
        ]
    )

    _backfill_notification_ids()
    leave_notifications_collection.create_index(
        "legacy_notification_id",
        unique=True,
        sparse=True,
    )
    _migrate_legacy_leave_notifications()
    notifications_collection.create_index("notification_id", unique=True)
    leave_notifications_collection.create_index("leave_notification_id", unique=True)
    notifications_collection.create_index(
        "event_key", unique=True, sparse=True, name="notification_event_key_unique"
    )


def create_system_notification(
    *,
    title: str,
    message: str,
    validity: timedelta,
    event_key: str,
    user_ids: list[str] | None = None,
    roles: set[str] | None = None,
):
    """Persist idempotent organization notifications in each recipient's inbox."""
    existing_notification = notifications_collection.find_one(
        {"event_key": event_key}, {"_id": 1}
    )
    if existing_notification:
        return str(existing_notification["_id"])
    user_query: dict = {"is_active": True}
    if user_ids is not None:
        valid_ids = [ObjectId(value) for value in user_ids if ObjectId.is_valid(value)]
        user_query["_id"] = {"$in": valid_ids}
    elif roles:
        user_query["role"] = {"$in": list(roles)}
    recipients = [
        str(record["_id"])
        for record in notifications_users_collection().find(user_query, {"_id": 1})
    ]
    if not recipients:
        return None
    now = utc_now()
    document = {
        "notification_id": _next_business_id("common_notification", "N"),
        "notification_type": "organization",
        "title": title,
        "message": message,
        "created_by_user_id": "system",
        "created_by_name": "HRMS",
        "created_by_role": "system",
        "audience": {
            "type": "users" if user_ids is not None else "roles" if roles else "all"
        },
        "recipient_user_ids": recipients,
        "created_at": now,
        "updated_at": now,
        "expires_at": now + validity,
        "validity_value": int(validity.total_seconds() // 86400),
        "validity_unit": "days",
        "status": "ACTIVE",
        "is_deleted": False,
        "deleted_at": None,
        "updates": [],
        "event_key": event_key,
        "action_url": "/organization/holiday-calendar",
    }
    try:
        result = notifications_collection.insert_one(document)
    except DuplicateKeyError:
        return None
    now_id = result.inserted_id
    recipient_documents = [
        {
            "notification_id": now_id,
            "user_id": recipient_id,
            "viewed_at": None,
            "acknowledged_at": None,
            "read_at": None,
            "is_read": False,
            "is_starred": False,
            "is_deleted": False,
            "created_at": now,
        }
        for recipient_id in recipients
    ]
    if recipient_documents:
        notification_recipients_collection.insert_many(recipient_documents)
    return str(now_id)


def _next_business_id(sequence_name: str, prefix: str) -> str:
    sequence = notification_sequences_collection.find_one_and_update(
        {"_id": sequence_name},
        {"$inc": {"value": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return f"{prefix}{int(sequence['value']):03d}"


def _seed_sequence(sequence_name: str, prefix: str, collection, field: str) -> None:
    maximum = 0
    for record in collection.find({field: {"$regex": f"^{prefix}\\d+$"}}, {field: 1}):
        try:
            maximum = max(maximum, int(record[field][len(prefix):]))
        except (TypeError, ValueError):
            continue
    notification_sequences_collection.update_one(
        {"_id": sequence_name},
        {"$max": {"value": maximum}},
        upsert=True,
    )


def _backfill_notification_ids() -> None:
    _seed_sequence("common_notification", "N", notifications_collection, "notification_id")
    seen_ids = set()
    for notification in notifications_collection.find(
        {"type": {"$ne": "leave"}},
        {"notification_id": 1, "status": 1, "is_deleted": 1},
    ).sort("_id", 1):
        current_id = notification.get("notification_id")
        update_values = {}
        if not isinstance(current_id, str) or not current_id.startswith("N") or not current_id[1:].isdigit() or current_id in seen_ids:
            new_id = _next_business_id("common_notification", "N")
            update_values["notification_id"] = new_id
            current_id = new_id
        if "status" not in notification:
            update_values["status"] = "DELETED" if notification.get("is_deleted") else "ACTIVE"
        if update_values:
            notifications_collection.update_one(
                {"_id": notification["_id"]},
                {"$set": update_values},
            )
        seen_ids.add(current_id)


def _migrate_legacy_leave_notifications() -> None:
    _seed_sequence(
        "leave_notification",
        "L",
        leave_notifications_collection,
        "leave_notification_id",
    )
    for legacy in notifications_collection.find({"type": "leave"}):
        existing = leave_notifications_collection.find_one(
            {"legacy_notification_id": legacy["_id"]},
            {"_id": 1},
        )
        if existing:
            notifications_collection.delete_one({"_id": legacy["_id"]})
            continue

        user_id = str(legacy.get("user_id", ""))
        user = notifications_users_collection().find_one(
            {"_id": ObjectId(user_id)} if ObjectId.is_valid(user_id) else {"_id": None},
            {"name": 1, "employee_id": 1},
        )
        try:
            leave_notifications_collection.insert_one(
                {
                    "leave_notification_id": _next_business_id("leave_notification", "L"),
                    "legacy_notification_id": legacy["_id"],
                    "recipient_user_id": user_id,
                    "employee_name": user.get("name") if user else None,
                    "employee_id": user.get("employee_id") if user else None,
                    "leave_request_id": legacy.get("leave_request_id"),
                    "notification_type": legacy.get("type", "leave"),
                    "title": legacy.get("title", ""),
                    "message": legacy.get("message", ""),
                    "is_read": bool(legacy.get("is_read", False)),
                    "is_starred": False,
                    "is_deleted": bool(legacy.get("is_deleted", False)),
                    "created_at": legacy.get("created_at") or utc_now(),
                    "expires_at": legacy.get("expires_at"),
                    "read_at": legacy.get("read_at"),
                    "deleted_at": legacy.get("deleted_at"),
                }
            )
        except DuplicateKeyError:
            if not leave_notifications_collection.find_one(
                {"legacy_notification_id": legacy["_id"]},
                {"_id": 1},
            ):
                raise
        notifications_collection.delete_one({"_id": legacy["_id"]})
    notification_recipients_collection.create_index(
        [
            ("user_id", 1),
            ("viewed_at", 1),
        ]
    )


# ============================================================
# Resolve Recipients
# ============================================================

def resolve_recipient_user_ids(audience: NotificationAudience):
    """
    Resolve the final list of users when notification is created.

    Important:
    Audience is snapshotted at posting time.

    Example:
        Role = Manager

    Only users who are Managers at that time receive the notification.
    """

    users_collection = notifications_users_collection()

    if audience.type == "all":

        users = users_collection.find(
            {
                "is_active": True,
            },
            {
                "_id": 1,
            },
        )

        return [
            str(user["_id"])
            for user in users
        ]

    if audience.type == "users":

        if not audience.user_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Please select at least one user",
            )

        valid_ids = []

        for user_id in audience.user_ids:

            query = {"_id": ObjectId(user_id)} if ObjectId.is_valid(user_id) else {"employee_id": user_id}
            user = users_collection.find_one(
                {**query, "is_active": True},
                {"_id": 1},
            )

            if user:
                valid_ids.append(str(user["_id"]))

        if not valid_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No valid active users were selected",
            )

        return list(dict.fromkeys(valid_ids))

    if audience.type == "roles":

        if not audience.role_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Please select at least one role",
            )

        role_values = [
            str(value).strip()
            for value in audience.role_ids
            if str(value).strip()
        ]

        role_values_lower = [
            value.lower()
            for value in role_values
        ]

        users = users_collection.find(
            {
                "is_active": True,
                "$or": [
                    {
                        "role_id": {
                            "$in": role_values
                        }
                    },
                    {
                        "role": {
                            "$in": role_values
                        }
                    },
                    {
                        "role": {
                            "$in": role_values_lower
                        }
                    },
                ],
            },
            {
                "_id": 1,
            },
        )

        user_ids = [
            str(user["_id"])
            for user in users
        ]

        if not user_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No active users found for selected role",
            )

        return list(dict.fromkeys(user_ids))

    if audience.type == "teams":

        if not audience.team_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Please select at least one team",
            )

        team_values = [
            str(value).strip()
            for value in audience.team_ids
            if str(value).strip()
        ]

        users = users_collection.find(
            {
                "is_active": True,
                "$or": [
                    {
                        "team_id": {
                            "$in": team_values
                        }
                    },
                    {
                        "team": {
                            "$in": team_values
                        }
                    },
                ],
            },
            {
                "_id": 1,
            },
        )

        user_ids = [
            str(user["_id"])
            for user in users
        ]

        if not user_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No active users found for selected team",
            )

        return list(dict.fromkeys(user_ids))

    if audience.type == "departments":
        if not audience.department_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Please select at least one department",
            )

        department_ids = [str(value).strip() for value in audience.department_ids if str(value).strip()]
        department_names = []
        valid_department_ids = []
        for department_id in department_ids:
            department = departments_collection.find_one(
                {"_id": ObjectId(department_id), "is_active": True}
                if ObjectId.is_valid(department_id)
                else {"department_name": department_id, "is_active": True},
                {"department_name": 1},
            )
            if department:
                valid_department_ids.append(str(department["_id"]))
                department_names.append(department.get("department_name", ""))

        detail_user_ids = [
            detail.get("user_id")
            for detail in employee_details_collection.find(
                {"department": {"$in": [*department_names, *valid_department_ids]}},
                {"user_id": 1},
            )
            if detail.get("user_id")
        ]
        query = {
            "is_active": True,
            "$or": [
                {"department_id": {"$in": valid_department_ids}},
                {"department": {"$in": department_names}},
                {"_id": {"$in": [ObjectId(value) for value in detail_user_ids if ObjectId.is_valid(value)]}},
            ],
        }
        user_ids = [str(item["_id"]) for item in users_collection.find(query, {"_id": 1})]
        if not user_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No active users found for the selected department",
            )
        return list(dict.fromkeys(user_ids))

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Invalid notification audience",
    )


# ============================================================
# Serialize Notification
# ============================================================

def serialize_notification(
    notification,
    recipient_state=None,
    viewer_count=0,
):
    recipient_state = recipient_state or {}

    created_at = ensure_utc(
        notification.get("created_at")
    )

    updated_at = ensure_utc(
        notification.get("updated_at")
    )

    expires_at = ensure_utc(
        notification.get("expires_at")
    )

    updates = []

    for update in notification.get("updates", []):

        updates.append(
            {
                "changed_at": ensure_utc(
                    update.get("changed_at")
                ),
                "changed_by_user_id": update.get(
                    "changed_by_user_id"
                ),
                "changed_by_name": update.get(
                    "changed_by_name"
                ),
                "changes": update.get(
                    "changes",
                    [],
                ),
            }
        )

    return {
        "id": str(notification["_id"]),
        "notification_id": notification.get("notification_id"),

        "title": notification.get(
            "title",
            "",
        ),

        "message": notification.get(
            "message",
            "",
        ),
        "notification_type": notification.get("notification_type", "announcement"),

        # ----------------------------------------------------
        # Creator information
        # ----------------------------------------------------

        "created_by_user_id": notification.get(
            "created_by_user_id"
        ),

        "created_by_name": notification.get(
            "created_by_name",
            "Unknown User",
        ),

        "created_by_role": notification.get(
            "created_by_role",
            "",
        ),

        # ----------------------------------------------------
        # Dates
        # ----------------------------------------------------

        "created_at": created_at,

        "updated_at": updated_at,

        "expires_at": expires_at,
        "status": notification.get("status", "ACTIVE"),
        "validity_value": notification.get("validity_value"),
        "validity_unit": notification.get("validity_unit", "hours"),

        # ----------------------------------------------------
        # Audience
        # ----------------------------------------------------

        "audience": notification.get(
            "audience",
            {},
        ),

        # ----------------------------------------------------
        # Current user state
        # ----------------------------------------------------

        "viewed_at": ensure_utc(
            recipient_state.get("viewed_at")
        ),

        "acknowledged_at": ensure_utc(
            recipient_state.get("acknowledged_at")
        ),

        "is_viewed": (
            recipient_state.get("viewed_at")
            is not None
        ),

        "is_acknowledged": (
            recipient_state.get("is_read", recipient_state.get("acknowledged_at") is not None)
        ),
        "is_read": bool(recipient_state.get("is_read", recipient_state.get("acknowledged_at") is not None)),
        "is_starred": bool(recipient_state.get("is_starred", False)),
        "is_deleted": bool(recipient_state.get("is_deleted", False)),

        # ----------------------------------------------------
        # Creator analytics
        # ----------------------------------------------------

        "viewer_count": viewer_count,

        # ----------------------------------------------------
        # Update history
        # ----------------------------------------------------

        "updates": updates,

        # ----------------------------------------------------
        # Creator permissions
        # ----------------------------------------------------

        "can_edit": False,

        "can_delete": False,
    }


# ============================================================
# CREATE NOTIFICATION
# ============================================================

@router.post("")
def create_notification(
    payload: NotificationCreate,
    authorization: str | None = Header(default=None),
):

    current_user = get_authenticated_user(
        authorization
    )

    require_notification_manager(
        current_user
    )

    now = utc_now()

    title = payload.title.strip()
    message = payload.message.strip()

    if not title:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Notification title is required",
        )

    if not message:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Notification message is required",
        )

    # --------------------------------------------------------
    # Resolve recipients
    # --------------------------------------------------------

    recipient_user_ids = resolve_recipient_user_ids(
        payload.audience
    )

    if not recipient_user_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No recipients found",
        )

    expires_at = now + payload.duration()

    # --------------------------------------------------------
    # Creator details
    # --------------------------------------------------------

    creator_user_id = str(
        current_user["_id"]
    )

    creator_name = (
        str(
            current_user.get(
                "name",
                "",
            )
        ).strip()
        or str(
            current_user.get(
                "work_email",
                "",
            )
        ).strip()
        or str(
            current_user.get(
                "employee_id",
                "",
            )
        ).strip()
        or "Unknown User"
    )

    creator_role = str(
        current_user.get(
            "role",
            "",
        )
    ).strip()

    # --------------------------------------------------------
    # Notification document
    # --------------------------------------------------------

    notification_document = {
        "notification_id": _next_business_id("common_notification", "N"),
        "notification_type": "announcement",
        "title": title,

        "message": message,

        # Creator identity
        "created_by_user_id": creator_user_id,
        "created_by_name": creator_name,
        "created_by_role": creator_role,

        # Audience snapshot
        "audience": payload.audience.model_dump(),

        "recipient_user_ids": recipient_user_ids,

        # Dates
        "created_at": now,
        "updated_at": now,
        "expires_at": expires_at,
        "validity_value": payload.validity_value or payload.validity_hours,
        "validity_unit": payload.validity_unit if payload.validity_value else "hours",

        # Status
        "status": payload.status,
        "is_deleted": False,
        "deleted_at": None,

        # Update history
        "updates": [],
    }

    result = notifications_collection.insert_one(
        notification_document
    )

    notification_id = result.inserted_id

    # --------------------------------------------------------
    # Create recipient state documents
    # --------------------------------------------------------

    recipient_documents = []

    for user_id in recipient_user_ids:

        recipient_documents.append(
            {
                "notification_id": notification_id,

                "user_id": user_id,

                "viewed_at": None,

                "acknowledged_at": None,
                "read_at": None,
                "is_read": False,
                "is_starred": False,
                "is_deleted": False,

                "created_at": now,
            }
        )

    if recipient_documents:

        notification_recipients_collection.insert_many(
            recipient_documents
        )

    notification_document["_id"] = notification_id

    return {
        "message": "Notification created successfully",

        "notification": serialize_notification(
            notification_document,
            {
                "viewed_at": None,
                "acknowledged_at": None,
            },
            0,
        ),

        "recipient_count": len(
            recipient_user_ids
        ),
    }


# ============================================================
# LIST MY NOTIFICATIONS
# ============================================================

@router.get("")
def list_my_notifications(
    authorization: str | None = Header(default=None),
):
    current_user = get_authenticated_user(
        authorization
    )
    user_id = str(
        current_user["_id"]
    )
    now = utc_now()
    recipient_documents = list(
        notification_recipients_collection.find(
            {"user_id": user_id, "is_deleted": {"$ne": True}}
        )
    )
    notification_ids = [
        item["notification_id"]
        for item in recipient_documents
    ]
    state_map = {
        item["notification_id"]: item
        for item in recipient_documents
    }
    response = []
    query = {
        "_id": {"$in": notification_ids},
        "is_deleted": {"$ne": True},
        "status": {"$ne": "HOLD"},
        "$or": [{"expires_at": {"$gt": now}}, {"expires_at": None}, {"expires_at": {"$exists": False}}],
    }
    if require_notification_manager_role(current_user):
        query["$or"] = [
            {
                "$and": [
                    {"_id": {"$in": notification_ids}},
                    {"is_deleted": {"$ne": True}},
                    {"$or": [{"expires_at": {"$gt": now}}, {"expires_at": None}, {"expires_at": {"$exists": False}}]},
                ]
            },
            {
                "created_by_user_id": user_id,
                "is_deleted": {"$ne": True},
                "$or": [{"expires_at": {"$gt": now}}, {"expires_at": None}, {"expires_at": {"$exists": False}}],
            },
        ]
        query.pop("_id", None)
        query.pop("status", None)
    notification_documents = list(notifications_collection.find(query).sort("created_at", -1))
    for notification in notification_documents:
        notification_id = notification["_id"]
        state = state_map.get(
            notification_id,
            {},
        )
        item = serialize_notification(
            notification,
            state,
            notification_recipients_collection.count_documents(
                {"notification_id": notification_id, "viewed_at": {"$ne": None}}
            ),
        )
        item["can_edit"] = (
            notification.get("created_by_user_id") == user_id
        )
        item["can_delete"] = (
            notification.get("created_by_user_id") == user_id
        )
        item["is_recipient"] = notification_id in state_map
        if notification.get("status") == "HOLD" and not item["can_edit"]:
            continue
        response.append(item)

    response.sort(key=lambda item: item.get("created_at") or datetime.min.replace(tzinfo=UTC), reverse=True)

    return {
        "notifications": response,
        "unread_count": _unread_common_count_for_user(user_id),
    }


# ============================================================
# UNREAD COUNT
# ============================================================

@router.get("/unread-count")
def get_unread_notification_count(
    authorization: str | None = Header(default=None),
):

    current_user = get_authenticated_user(
        authorization
    )

    user_id = str(
        current_user["_id"]
    )

    _expire_notifications()
    return {"unread_count": _unread_common_count_for_user(user_id)}


def require_notification_manager_role(user) -> bool:
    return str(user.get("role", "")).strip().lower() in ALLOWED_NOTIFICATION_ROLES


def _unread_common_count_for_user(user_id: str) -> int:
    now = utc_now()
    states = notification_recipients_collection.find(
        {
            "user_id": user_id,
            "is_deleted": {"$ne": True},
            "is_read": {"$ne": True},
            "acknowledged_at": None,
        },
        {"notification_id": 1},
    )
    common_ids = [item["notification_id"] for item in states]
    common_count = notifications_collection.count_documents(
        {
            "_id": {"$in": common_ids},
            "is_deleted": {"$ne": True},
            "status": {"$ne": "HOLD"},
            "$or": [{"expires_at": {"$gt": now}}, {"expires_at": None}, {"expires_at": {"$exists": False}}],
        }
    ) if common_ids else 0
    return common_count


def _unread_leave_count_for_user(user_id: str) -> int:
    now = utc_now()
    return leave_notifications_collection.count_documents(
        {
            "recipient_user_id": user_id,
            "is_read": {"$ne": True},
            "is_deleted": {"$ne": True},
            "deleted_at": None,
            "$or": [{"expires_at": {"$gt": now}}, {"expires_at": None}, {"expires_at": {"$exists": False}}],
        }
    )


@router.get("/snapshot")
def get_notification_snapshot(
    authorization: str | None = Header(default=None),
):
    user = get_authenticated_user(authorization)
    user_id = str(user["_id"])
    _expire_notifications()
    now = utc_now()
    recipient_states = notification_recipients_collection.find(
        {"user_id": user_id, "is_deleted": {"$ne": True}},
        {"notification_id": 1},
    )
    common_ids = [item["notification_id"] for item in recipient_states]
    common = notifications_collection.find(
        {
            "_id": {"$in": common_ids},
            "is_deleted": {"$ne": True},
            "status": {"$ne": "HOLD"},
            "$or": [{"expires_at": {"$gt": now}}, {"expires_at": None}, {"expires_at": {"$exists": False}}],
        },
        {"notification_id": 1},
    )
    leave = leave_notifications_collection.find(
        {
            "recipient_user_id": user_id,
            "is_deleted": {"$ne": True},
            "deleted_at": None,
            "$or": [{"expires_at": {"$gt": now}}, {"expires_at": None}, {"expires_at": {"$exists": False}}],
        },
        {"leave_notification_id": 1},
    )
    return {
        "notification_ids": [
            *[item["notification_id"] for item in common if item.get("notification_id")],
        ],
        "leave_notification_ids": [
            item["leave_notification_id"]
            for item in leave
            if item.get("leave_notification_id")
        ],
        "unread_count": _unread_common_count_for_user(user_id),
    }


@router.get("/lookups")
def notification_lookups(
    authorization: str | None = Header(default=None),
):
    user = get_authenticated_user(authorization)
    require_notification_manager(user)
    roles = [
        {
            "id": str(item.get("role_id") or item.get("name", "")).strip().lower().replace(" ", "_"),
            "name": item.get("name", ""),
        }
        for item in db["roles"].find({"status": {"$ne": "Inactive"}}, {"name": 1}).sort("name", 1)
        if item.get("name")
    ]
    role_names = {item["name"].strip().lower() for item in roles}
    for role in db["users"].distinct("role", {"is_active": True}):
        if role and role.strip().lower() not in role_names:
            roles.append({"id": role, "name": role})
            role_names.add(role.strip().lower())
    departments = [
        {"id": str(item["_id"]), "name": item.get("department_name", "")}
        for item in departments_collection.find({"is_active": True}, {"department_name": 1}).sort("department_name", 1)
        if item.get("department_name")
    ]
    employees = [
        {
            "id": str(item["_id"]),
            "employee_id": item.get("employee_id", ""),
            "name": item.get("name", ""),
        }
        for item in notifications_users_collection().find(
            {"is_active": True},
            {"employee_id": 1, "name": 1},
        ).sort("employee_id", 1)
    ]
    return {"roles": roles, "departments": departments, "employees": employees}


# ============================================================
# MARK AS VIEWED
# ============================================================

@router.post("/{notification_id}/view")
def mark_notification_viewed(
    notification_id: str,
    authorization: str | None = Header(default=None),
):

    current_user = get_authenticated_user(
        authorization
    )

    user_id = str(
        current_user["_id"]
    )

    object_id = parse_object_id(
        notification_id,
        "notification id",
    )

    notification = notifications_collection.find_one(
        {
            "_id": object_id,
            "is_deleted": {"$ne": True},
            "status": {"$ne": "HOLD"},
        }
    )

    if notification is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found",
        )

    expires_at = ensure_utc(
        notification.get("expires_at")
    )

    if expires_at and expires_at <= utc_now():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification has expired",
        )

    recipient = notification_recipients_collection.find_one(
        {
            "notification_id": object_id,
            "user_id": user_id,
            "is_deleted": {"$ne": True},
        }
    )

    if recipient is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a recipient of this notification",
        )

    now = utc_now()

    # Only set first-view time.
    notification_recipients_collection.update_one(
        {
            "_id": recipient["_id"],
            "viewed_at": None,
        },
        {
            "$set": {
                "viewed_at": now,
            }
        },
    )

    updated_recipient = (
        notification_recipients_collection.find_one(
            {
                "_id": recipient["_id"]
            }
        )
    )

    viewer_count = (
        notification_recipients_collection.count_documents(
            {
                "notification_id": object_id,
                "viewed_at": {
                    "$ne": None
                },
            }
        )
    )

    return {
        "message": "Notification marked as viewed",

        "viewed_at": ensure_utc(
            updated_recipient.get(
                "viewed_at"
            )
        ),

        "viewer_count": viewer_count,
    }


# ============================================================
# ACKNOWLEDGE / GREEN TICK
# ============================================================

@router.post("/{notification_id}/acknowledge")
def acknowledge_notification(
    notification_id: str,
    authorization: str | None = Header(default=None),
):

    current_user = get_authenticated_user(
        authorization
    )

    user_id = str(
        current_user["_id"]
    )

    object_id = parse_object_id(
        notification_id,
        "notification id",
    )

    notification = notifications_collection.find_one(
        {
            "_id": object_id,
            "is_deleted": {"$ne": True},
            "status": {"$ne": "HOLD"},
        }
    )

    if notification is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found",
        )
    expires_at = ensure_utc(notification.get("expires_at"))
    if expires_at and expires_at <= utc_now():
        raise HTTPException(status_code=404, detail="Notification has expired")

    recipient = notification_recipients_collection.find_one(
        {
            "notification_id": object_id,
            "user_id": user_id,
            "is_deleted": {"$ne": True},
        }
    )

    if recipient is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a recipient of this notification",
        )

    now = utc_now()

    notification_recipients_collection.update_one(
        {
            "_id": recipient["_id"],
        },
        {
            "$set": {
                "acknowledged_at": now,
                "read_at": now,
                "is_read": True,
            }
        },
    )

    return {
        "message": "Notification acknowledged successfully",

        "acknowledged_at": now,

        "unread_count": get_unread_count_internal(
            user_id
        ),
    }


@router.post("/{notification_id}/star")
def star_notification(
    notification_id: str,
    authorization: str | None = Header(default=None),
):
    return _set_common_recipient_state(notification_id, authorization, is_starred=True)


@router.post("/{notification_id}/unstar")
def unstar_notification(
    notification_id: str,
    authorization: str | None = Header(default=None),
):
    return _set_common_recipient_state(notification_id, authorization, is_starred=False)


def _set_common_recipient_state(notification_id, authorization, is_starred):
    user = get_authenticated_user(authorization)
    object_id = parse_object_id(notification_id, "notification id")
    notification = notifications_collection.find_one(
        {"_id": object_id, "is_deleted": {"$ne": True}, "status": {"$ne": "HOLD"}}
    )
    if not notification:
        raise HTTPException(status_code=404, detail="Notification is not active")
    expires_at = ensure_utc(notification.get("expires_at"))
    if expires_at and expires_at <= utc_now():
        raise HTTPException(status_code=404, detail="Notification has expired")
    recipient = notification_recipients_collection.find_one(
        {"notification_id": object_id, "user_id": str(user["_id"]), "is_deleted": {"$ne": True}}
    )
    if not recipient:
        raise HTTPException(status_code=404, detail="Notification recipient record not found")
    notification_recipients_collection.update_one(
        {"_id": recipient["_id"]},
        {"$set": {"is_starred": is_starred, "updated_at": utc_now()}},
    )
    return {"is_starred": is_starred}


@router.post("/leave/{leave_notification_id}/read")
def mark_leave_notification_read(
    leave_notification_id: str,
    authorization: str | None = Header(default=None),
):
    return _update_leave_recipient_state(
        leave_notification_id,
        authorization,
        {"is_read": True, "read_at": utc_now()},
    )


@router.post("/leave/{leave_notification_id}/star")
def star_leave_notification(
    leave_notification_id: str,
    authorization: str | None = Header(default=None),
):
    return _update_leave_recipient_state(
        leave_notification_id,
        authorization,
        {"is_starred": True},
    )


@router.post("/leave/{leave_notification_id}/unstar")
def unstar_leave_notification(
    leave_notification_id: str,
    authorization: str | None = Header(default=None),
):
    return _update_leave_recipient_state(
        leave_notification_id,
        authorization,
        {"is_starred": False},
    )


@router.delete("/leave/{leave_notification_id}")
def delete_leave_notification(
    leave_notification_id: str,
    authorization: str | None = Header(default=None),
):
    return _update_leave_recipient_state(
        leave_notification_id,
        authorization,
        {"is_deleted": True, "deleted_at": utc_now()},
    )


def _update_leave_recipient_state(leave_notification_id, authorization, updates):
    user = get_authenticated_user(authorization)
    notification = leave_notifications_collection.find_one(
        {
            "leave_notification_id": leave_notification_id,
            "recipient_user_id": str(user["_id"]),
            "is_deleted": {"$ne": True},
        }
    )
    if not notification:
        raise HTTPException(status_code=404, detail="Leave notification not found")
    expires_at = ensure_utc(notification.get("expires_at"))
    if expires_at and expires_at <= utc_now() and not updates.get("is_deleted"):
        raise HTTPException(status_code=404, detail="Leave notification has expired")
    leave_notifications_collection.update_one({"_id": notification["_id"]}, {"$set": updates})
    return {
        "message": "Leave notification updated successfully",
        "unread_count": _unread_leave_count_for_user(str(user["_id"])),
    }


# ============================================================
# INTERNAL UNREAD COUNT
# ============================================================

def get_unread_count_internal(user_id: str):
    return _unread_common_count_for_user(user_id)


# ============================================================
# VIEWER LIST
#
# Creator can use this to see exactly who viewed.
# ============================================================

@router.get("/{notification_id}/viewers")
def get_notification_viewers(
    notification_id: str,
    authorization: str | None = Header(default=None),
):

    current_user = get_authenticated_user(
        authorization
    )

    current_user_id = str(
        current_user["_id"]
    )

    object_id = parse_object_id(
        notification_id,
        "notification id",
    )

    notification = notifications_collection.find_one(
        {
            "_id": object_id,
        }
    )

    if notification is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found",
        )

    # --------------------------------------------------------
    # Only creator can see viewer analytics.
    # --------------------------------------------------------

    if notification.get(
        "created_by_user_id"
    ) != current_user_id:

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Only the notification creator "
                "can view viewer details"
            ),
        )

    viewed_recipients = list(
        notification_recipients_collection.find(
            {
                "notification_id": object_id,
                "viewed_at": {
                    "$ne": None
                },
            }
        ).sort(
            "viewed_at",
            1,
        )
    )

    user_ids = [
        item["user_id"]
        for item in viewed_recipients
    ]

    object_ids = []

    for user_id in user_ids:

        try:
            object_ids.append(
                ObjectId(user_id)
            )
        except Exception:
            pass

    users_collection = notifications_users_collection()

    users = list(
        users_collection.find(
            {
                "_id": {
                    "$in": object_ids
                }
            },
            {
                "name": 1,
                "work_email": 1,
                "employee_id": 1,
                "role": 1,
            },
        )
    )

    user_map = {
        str(user["_id"]): user
        for user in users
    }

    viewers = []

    for recipient in viewed_recipients:

        viewer_user_id = recipient.get(
            "user_id"
        )

        user = user_map.get(
            viewer_user_id,
            {},
        )

        viewers.append(
            {
                "user_id": viewer_user_id,

                "name": (
                    user.get("name")
                    or user.get("work_email")
                    or user.get("employee_id")
                    or "Unknown User"
                ),

                "work_email": user.get(
                    "work_email"
                ),

                "employee_id": user.get(
                    "employee_id"
                ),

                "role": user.get(
                    "role"
                ),

                "viewed_at": ensure_utc(
                    recipient.get(
                        "viewed_at"
                    )
                ),

                "acknowledged_at": ensure_utc(
                    recipient.get(
                        "acknowledged_at"
                    )
                ),

                "is_acknowledged": (
                    recipient.get(
                        "acknowledged_at"
                    )
                    is not None
                ),
            }
        )

    return {
        "notification_id": notification_id,

        "viewer_count": len(viewers),

        "viewers": viewers,
    }


# ============================================================
# UPDATE NOTIFICATION
# ============================================================

@router.put("/{notification_id}")
def update_notification(
    notification_id: str,
    payload: NotificationUpdate,
    authorization: str | None = Header(default=None),
):

    current_user = get_authenticated_user(
        authorization
    )

    require_notification_manager(
        current_user
    )

    current_user_id = str(
        current_user["_id"]
    )

    object_id = parse_object_id(
        notification_id,
        "notification id",
    )

    notification = notifications_collection.find_one(
        {
            "_id": object_id,
            "is_deleted": False,
        }
    )

    if notification is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found",
        )

    # --------------------------------------------------------
    # Only creator can update.
    # --------------------------------------------------------

    if notification.get(
        "created_by_user_id"
    ) != current_user_id:

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "You can update only notifications "
                "created by you"
            ),
        )

    expires_at = ensure_utc(
        notification.get(
            "expires_at"
        )
    )

    if expires_at and expires_at <= utc_now():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Expired notifications cannot be updated",
        )

    changes = []

    update_fields = {
        "updated_at": utc_now()
    }

    # --------------------------------------------------------
    # Title change
    # --------------------------------------------------------

    if payload.title is not None:

        new_title = payload.title.strip()

        if not new_title:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Notification title cannot be empty",
            )

        old_title = notification.get(
            "title",
            "",
        )

        if new_title != old_title:

            changes.append(
                {
                    "field": "Title",

                    "old_value": old_title,

                    "new_value": new_title,
                }
            )

            update_fields["title"] = new_title

    # --------------------------------------------------------
    # Message change
    # --------------------------------------------------------

    if payload.message is not None:

        new_message = payload.message.strip()

        if not new_message:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Notification message cannot be empty",
            )

        old_message = notification.get(
            "message",
            "",
        )

        if new_message != old_message:

            changes.append(
                {
                    "field": "Message",

                    "old_value": old_message,

                    "new_value": new_message,
                }
            )

            update_fields["message"] = new_message

    if payload.audience is not None:
        recipient_user_ids = resolve_recipient_user_ids(payload.audience)
        if payload.audience.model_dump() != notification.get("audience"):
            changes.append({"field": "Audience", "old_value": notification.get("audience"), "new_value": payload.audience.model_dump()})
            update_fields["audience"] = payload.audience.model_dump()
            update_fields["recipient_user_ids"] = recipient_user_ids
            notification_recipients_collection.delete_many({"notification_id": object_id})
            notification_recipients_collection.insert_many(
                [
                    {
                        "notification_id": object_id,
                        "user_id": recipient_id,
                        "viewed_at": None,
                        "acknowledged_at": None,
                        "read_at": None,
                        "is_read": False,
                        "is_starred": False,
                        "is_deleted": False,
                        "created_at": utc_now(),
                    }
                    for recipient_id in recipient_user_ids
                ]
            )

    if payload.validity_value is not None:
        validity_unit = payload.validity_unit or "hours"
        if validity_unit == "minutes":
            duration = timedelta(minutes=payload.validity_value)
        elif validity_unit == "hours":
            duration = timedelta(hours=payload.validity_value)
        elif validity_unit == "days":
            duration = timedelta(days=payload.validity_value)
        else:
            duration = timedelta(weeks=payload.validity_value)
        if duration > timedelta(days=30):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Notification validity cannot exceed 30 days.",
            )
        update_fields.update({
            "validity_value": payload.validity_value,
            "validity_unit": validity_unit,
            "expires_at": utc_now() + duration,
        })
        changes.append({
            "field": "Validity",
            "old_value": notification.get("expires_at"),
            "new_value": update_fields["expires_at"],
        })

    if payload.status is not None and payload.status != notification.get("status", "ACTIVE"):
        update_fields["status"] = payload.status
        changes.append({
            "field": "Status",
            "old_value": notification.get("status", "ACTIVE"),
            "new_value": payload.status,
        })

    if not changes:

        return {
            "message": "No changes were made"
        }

    # --------------------------------------------------------
    # Update history
    # --------------------------------------------------------

    creator_name = (
        str(
            current_user.get(
                "name",
                "",
            )
        ).strip()
        or str(
            current_user.get(
                "work_email",
                "",
            )
        ).strip()
        or "Unknown User"
    )

    update_history = {
        "changed_at": utc_now(),

        "changed_by_user_id": current_user_id,

        "changed_by_name": creator_name,

        "changes": changes,
    }

    update_fields.setdefault(
        "updated_at",
        utc_now(),
    )

    notifications_collection.update_one(
        {
            "_id": object_id,
        },
        {
            "$set": update_fields,

            "$push": {
                "updates": update_history
            },
        },
    )

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # Existing recipients should see the updated notification
    # again.
    #
    # Therefore reset viewed/acknowledged state.
    # This means an important update becomes unread again.
    # --------------------------------------------------------

    notification_recipients_collection.update_many(
        {
            "notification_id": object_id,
        },
        {
            "$set": {
                "viewed_at": None,
                "acknowledged_at": None,
                "read_at": None,
                "is_read": False,
                "is_deleted": False,
            }
        },
    )

    updated_notification = (
        notifications_collection.find_one(
            {
                "_id": object_id
            }
        )
    )

    return {
        "message": "Notification updated successfully",

        "notification": serialize_notification(
            updated_notification,
            {
                "viewed_at": None,
                "acknowledged_at": None,
            },
            notification_recipients_collection.count_documents(
                {
                    "notification_id": object_id,
                    "viewed_at": {
                        "$ne": None
                    },
                }
            ),
        ),
    }


# ============================================================
# DELETE NOTIFICATION
# ============================================================

@router.delete("/{notification_id}")
def delete_notification(
    notification_id: str,
    authorization: str | None = Header(default=None),
):

    current_user = get_authenticated_user(
        authorization
    )

    current_user_id = str(
        current_user["_id"]
    )

    object_id = parse_object_id(
        notification_id,
        "notification id",
    )

    notification = notifications_collection.find_one(
        {
            "_id": object_id,
            "is_deleted": {"$ne": True},
        }
    )

    if notification is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found",
        )

    now = utc_now()
    if notification.get("created_by_user_id") == current_user_id:
        require_notification_manager(current_user)
        notifications_collection.update_one(
            {"_id": object_id},
            {"$set": {"is_deleted": True, "status": "DELETED", "deleted_at": now}},
        )
    else:
        recipient = notification_recipients_collection.find_one(
            {"notification_id": object_id, "user_id": current_user_id}
        )
        if not recipient:
            raise HTTPException(status_code=403, detail="You are not a recipient of this notification")
        notification_recipients_collection.update_one(
            {"_id": recipient["_id"]},
            {"$set": {"is_deleted": True, "deleted_at": now}},
        )

    return {
        "message": "Notification deleted successfully",
        "unread_count": _unread_common_count_for_user(current_user_id),
    }


@router.post("/{notification_id}/hold")
def hold_notification(
    notification_id: str,
    authorization: str | None = Header(default=None),
):
    return _set_notification_lifecycle(notification_id, authorization, "HOLD")


@router.post("/{notification_id}/resume")
def resume_notification(
    notification_id: str,
    authorization: str | None = Header(default=None),
):
    return _set_notification_lifecycle(notification_id, authorization, "ACTIVE")


def _set_notification_lifecycle(notification_id, authorization, next_status):
    user = get_authenticated_user(authorization)
    require_notification_manager(user)
    object_id = parse_object_id(notification_id, "notification id")
    notification = notifications_collection.find_one(
        {"_id": object_id, "is_deleted": {"$ne": True}}
    )
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
    if notification.get("created_by_user_id") != str(user["_id"]):
        raise HTTPException(status_code=403, detail="You can manage only notifications created by you")
    expires_at = ensure_utc(notification.get("expires_at"))
    if expires_at and expires_at <= utc_now():
        _expire_notifications()
        raise HTTPException(status_code=410, detail="This notification has expired and cannot be resumed.")
    notifications_collection.update_one(
        {"_id": object_id},
        {"$set": {"status": next_status, "updated_at": utc_now()}},
    )
    return {"message": f"Notification {next_status.lower()} successfully", "status": next_status}


def _expire_notifications():
    now = utc_now()
    notifications_collection.update_many(
        {"expires_at": {"$type": "date", "$lte": now}, "status": {"$in": ["ACTIVE", "HOLD"]}},
        {"$set": {"status": "EXPIRED", "updated_at": now}},
    )
    leave_notifications_collection.update_many(
        {"expires_at": {"$type": "date", "$lte": now}, "is_deleted": {"$ne": True}},
        {"$set": {"status": "EXPIRED"}},
    )