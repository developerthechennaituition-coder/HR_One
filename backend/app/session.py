"""Shared MongoDB-backed login sessions for all HRMS API workers."""
from __future__ import annotations

import hashlib
import os
import secrets
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.mongodb import db

sessions_collection = db["user_sessions"]
DEFAULT_SESSION_HOURS = 12
_indexes_ready = False
_indexes_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _expiry_hours() -> int:
    try:
        return max(1, min(int(os.getenv("HRMS_SESSION_HOURS", DEFAULT_SESSION_HOURS)), 24 * 30))
    except (TypeError, ValueError):
        return DEFAULT_SESSION_HOURS


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def ensure_session_indexes() -> None:
    """Create idempotent indexes once per worker; TTL cleanup is asynchronous."""
    global _indexes_ready
    if _indexes_ready:
        return
    with _indexes_lock:
        if _indexes_ready:
            return
        sessions_collection.create_index("token_hash", unique=True, name="uq_user_sessions_token_hash")
        sessions_collection.create_index("user_id", name="ix_user_sessions_user_id")
        sessions_collection.create_index("expires_at", expireAfterSeconds=0, name="ttl_user_sessions_expires_at")
        _indexes_ready = True


def create_session(user_id: str) -> str:
    ensure_session_indexes()
    token = secrets.token_urlsafe(32)
    now = _now()
    sessions_collection.insert_one({
        "token_hash": _token_hash(token),
        "user_id": str(user_id),
        "created_at": now,
        "expires_at": now + timedelta(hours=_expiry_hours()),
        "revoked_at": None,
    })
    return token


def get_session_user_id(token: str | None) -> str | None:
    if not isinstance(token, str) or not token.strip():
        return None
    ensure_session_indexes()
    record = sessions_collection.find_one({
        "token_hash": _token_hash(token.strip()),
        "revoked_at": None,
        "expires_at": {"$gt": _now()},
    }, {"user_id": 1})
    return str(record["user_id"]) if record and record.get("user_id") else None


def revoke_session(token: str | None) -> None:
    if isinstance(token, str) and token.strip():
        sessions_collection.update_one(
            {"token_hash": _token_hash(token.strip()), "revoked_at": None},
            {"$set": {"revoked_at": _now()}},
        )


def revoke_user_sessions(user_id: str) -> int:
    result = sessions_collection.update_many(
        {"user_id": str(user_id), "revoked_at": None},
        {"$set": {"revoked_at": _now()}},
    )
    return int(result.modified_count)


def count_user_sessions(user_id: str) -> int:
    now = _now()
    return sessions_collection.count_documents({
        "user_id": str(user_id),
        "revoked_at": None,
        "expires_at": {"$gt": now},
    })


def authenticate_token(token: str | None) -> dict[str, Any] | None:
    """Resolve a bearer token to an active users collection document."""
    from bson import ObjectId
    user_id = get_session_user_id(token)
    if not user_id or not ObjectId.is_valid(user_id):
        return None
    user = db["users"].find_one({"_id": ObjectId(user_id), "is_active": True})
    return user
