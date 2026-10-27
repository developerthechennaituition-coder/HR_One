from __future__ import annotations

import base64
import csv
import io
import hashlib
import json
import logging
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from bson import ObjectId
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, File, Header, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field, field_validator
from pymongo.errors import DuplicateKeyError

from app.attendance import (
    _current_user,
    attendance_collection,
    company_now,
    company_today,
    users_collection,
)
from app.db.mongodb import db


from datetime import datetime, timezone
from uuid import uuid4


def create_excel_import_id() -> str:
    """Generate a unique ID for each Excel import."""
    return str(uuid4())


def set_latest_excel_import(db, import_id: str) -> None:
    """Record the most recently completed Excel import."""
    db.essl_import_state.update_one(
        {"_id": "latest_excel_import"},
        {
            "$set": {
                "import_id": import_id,
                "updated_at": datetime.now(timezone.utc),
            }
        },
        upsert=True,
    )


def get_latest_excel_import_id(db):
    """Return the ID of the latest completed Excel import."""
    state = db.essl_import_state.find_one(
        {"_id": "latest_excel_import"},
        {"import_id": 1},
    )
    return state.get("import_id") if state else None


def get_latest_import_issues(db, issue_collection: str):
    """Return issues belonging only to the latest Excel import."""
    import_id = get_latest_excel_import_id(db)

    if not import_id:
        return []

    collection = db[issue_collection]

    return list(
        collection.find({"import_id": import_id}).sort(
            "timestamp", -1
        )
    )

router = APIRouter(prefix="/api/attendance/essl", tags=["ESSL Attendance"])
logger = logging.getLogger(__name__)
COMPANY_TZ = ZoneInfo(os.getenv("COMPANY_TIMEZONE", "Asia/Kolkata"))
MAX_RESPONSE_BYTES = 10 * 1024 * 1024
CONFIG_ID = "default"

essl_config_collection = db["essl_config"]
essl_sync_history_collection = db["essl_sync_history"]
essl_raw_punches_collection = db["essl_raw_punches"]
essl_punch_issues_collection = db["essl_punch_issues"]
employees_collection = db["employees"]


class EsslConfigPayload(BaseModel):
    api_url: str = Field(min_length=1, max_length=2048)
    method: Literal["GET", "POST"] = "GET"
    auth_type: Literal["none", "bearer", "api_key", "basic"] = "none"
    auth_secret: str | None = Field(default=None, max_length=8192)
    auth_username: str | None = Field(default=None, max_length=512)
    auth_password: str | None = Field(default=None, max_length=8192)
    api_key_header: str = Field(default="X-API-Key", min_length=1, max_length=128)
    request_params: dict[str, Any] | None = None
    request_body: dict[str, Any] | None = None
    request_headers: dict[str, str] | None = None
    records_path: str = Field(default="", max_length=512)
    employee_id_path: str = Field(min_length=1, max_length=512)
    timestamp_path: str = Field(min_length=1, max_length=512)
    punch_id_path: str | None = Field(default=None, max_length=512)
    device_id_path: str | None = Field(default=None, max_length=512)
    from_date_parameter: str | None = Field(default=None, max_length=128)
    to_date_parameter: str | None = Field(default=None, max_length=128)
    timestamp_format: str | None = Field(default=None, max_length=128)

    @field_validator("api_url", "api_key_header", "employee_id_path", "timestamp_path")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field must not be blank.")
        return value

    @field_validator(
        "auth_secret",
        "auth_username",
        "auth_password",
        "records_path",
        "punch_id_path",
        "device_id_path",
        "from_date_parameter",
        "to_date_parameter",
        "timestamp_format",
    )
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class EsslSyncPayload(BaseModel):
    from_date: date
    to_date: date


class EsslEmployeeMappingPayload(BaseModel):
    essl_employee_id: str | None = Field(default=None, max_length=128)

    @field_validator("essl_employee_id")
    @classmethod
    def normalize_mapping(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


def ensure_essl_indexes() -> None:
    users_collection.create_index(
        "essl_employee_id_normalized",
        unique=True,
        sparse=True,
        name="users_essl_employee_id_unique",
    )
    essl_sync_history_collection.create_index(
        [("started_at", -1)],
        name="essl_sync_history_started_at",
    )
    essl_raw_punches_collection.create_index(
        "punch_key",
        unique=True,
        name="essl_raw_punch_key_unique",
    )
    essl_raw_punches_collection.create_index(
        [("user_id", 1), ("punch_at", 1)],
        name="essl_raw_user_punch_at",
    )
    essl_punch_issues_collection.create_index(
        "issue_key",
        unique=True,
        name="essl_punch_issue_key_unique",
    )
    essl_punch_issues_collection.create_index(
        [("last_seen_at", -1)],
        name="essl_punch_issues_last_seen",
    )


def _require_essl_admin(authorization: str | None) -> dict[str, Any]:
    user = _current_user(authorization)
    role = str(user.get("role", "")).strip().casefold()
    if role not in {"hr admin", "administrator"}:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only HR Admin or Administrator can manage ESSL attendance.",
        )
    return user


def _normalise_employee_id(value: Any) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (str, int)):
        normalized = str(value).strip().casefold()
        return normalized or None
    return None


def _fernet() -> Fernet:
    key = os.getenv("ESSL_CONFIG_ENCRYPTION_KEY", "")
    if not key:
        raise RuntimeError("ESSL_CONFIG_ENCRYPTION_KEY is not configured.")
    try:
        return Fernet(key.encode("ascii"))
    except (UnicodeEncodeError, ValueError) as exc:
        raise RuntimeError(
            "ESSL_CONFIG_ENCRYPTION_KEY must be a valid Fernet key."
        ) from exc


def _encrypt_credentials(credentials: dict[str, Any]) -> str:
    return _fernet().encrypt(
        json.dumps(credentials, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")


def _decrypt_credentials(config: dict[str, Any]) -> dict[str, Any]:
    encrypted = config.get("credentials_encrypted")
    if not encrypted:
        return {}
    try:
        decoded = _fernet().decrypt(encrypted.encode("ascii"))
        credentials = json.loads(decoded)
    except (InvalidToken, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError("Stored ESSL credentials could not be decrypted.") from exc
    if not isinstance(credentials, dict):
        raise RuntimeError("Stored ESSL credentials are invalid.")
    return credentials


def _public_config(config: dict[str, Any] | None) -> dict[str, Any]:
    if not config:
        return {"configured": False}
    result = {
        key: config.get(key)
        for key in EsslConfigPayload.model_fields
        if key not in {
            "auth_secret",
            "auth_username",
            "auth_password",
        }
    }
    result.update(
        configured=True,
        has_credentials=bool(config.get("has_credentials")),
        has_request_parameters=bool(config.get("has_request_parameters")),
        has_request_body=bool(config.get("has_request_body")),
        has_request_headers=bool(config.get("has_request_headers")),
    )
    return result


def _load_config() -> dict[str, Any]:
    config = essl_config_collection.find_one({"_id": CONFIG_ID})
    if not config:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "ESSL API is not configured. Ask an HR Admin to configure it first.",
        )
    return config


def _validate_endpoint(api_url: str, api_key_header: str) -> str:
    parsed = urlparse(api_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Enter an HTTP(S) endpoint URL without embedded credentials or query parameters.",
        )
    if any(character in api_key_header for character in "\r\n:"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "The API-key header name is invalid.",
        )
    return api_url


def _read_path(value: Any, path: str) -> Any:
    if not path:
        return value
    current = value
    for part in path.split("."):
        if isinstance(current, dict):
            if part not in current:
                return None
            current = current[part]
        elif isinstance(current, list) and part.isdecimal():
            index = int(part)
            if index >= len(current):
                return None
            current = current[index]
        else:
            return None
    return current


def _parse_punch_timestamp(value: Any, timestamp_format: str | None) -> datetime:
    if isinstance(value, bool) or value is None:
        raise ValueError("missing or invalid timestamp")
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            raise ValueError("invalid numeric timestamp")
        epoch = float(value)
        if abs(epoch) >= 100_000_000_000:
            epoch /= 1000
        try:
            parsed = datetime.fromtimestamp(epoch, tz=timezone.utc)
        except (OverflowError, OSError, ValueError) as exc:
            raise ValueError("impossible timestamp") from exc
    elif isinstance(value, str):
        try:
            if timestamp_format:
                parsed = datetime.strptime(value.strip(), timestamp_format)
            else:
                parsed = datetime.fromisoformat(
                    value.strip().replace("Z", "+00:00")
                )
        except (TypeError, ValueError) as exc:
            raise ValueError("unsupported timestamp format") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=COMPANY_TZ)
    else:
        raise ValueError("unsupported timestamp value")

    local_timestamp = parsed.astimezone(COMPANY_TZ)
    if local_timestamp.year < 2000:
        raise ValueError("impossible timestamp")
    if local_timestamp > company_now():
        raise ValueError("future-dated timestamp")
    return local_timestamp


def _request_json(
    config: dict[str, Any],
    credentials: dict[str, Any],
    from_date: date | None = None,
    to_date: date | None = None,
) -> Any:
    url = config["api_url"]
    params = dict(credentials.get("request_params") or {})
    body = dict(credentials.get("request_body") or {})
    if from_date and to_date:
        from_parameter = config.get("from_date_parameter")
        to_parameter = config.get("to_date_parameter")
        if config.get("method") == "GET":
            if from_parameter:
                params[from_parameter] = from_date.isoformat()
            if to_parameter:
                params[to_parameter] = to_date.isoformat()
        else:
            if from_parameter:
                body[from_parameter] = from_date.isoformat()
            if to_parameter:
                body[to_parameter] = to_date.isoformat()

    if params:
        url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"

    headers = {"Accept": "application/json"}
    for name, value in (credentials.get("request_headers") or {}).items():
        if (
            not name
            or any(character in name for character in "\r\n:")
            or any(character in value for character in "\r\n")
        ):
            raise ValueError("ESSL request contains an invalid HTTP header.")
        headers[name] = value
    auth_type = config.get("auth_type", "none")
    if auth_type == "bearer":
        secret = credentials.get("auth_secret", "")
        if not secret:
            raise RuntimeError("ESSL bearer token is not configured.")
        headers["Authorization"] = f"Bearer {secret}"
    elif auth_type == "api_key":
        secret = credentials.get("auth_secret", "")
        if not secret:
            raise RuntimeError("ESSL API key is not configured.")
        headers[config.get("api_key_header", "X-API-Key")] = secret
    elif auth_type == "basic":
        username = credentials.get("auth_username", "")
        password = credentials.get("auth_password", "")
        if not username or not password:
            raise RuntimeError("ESSL username and password are not configured.")
        encoded = base64.b64encode(
            f"{username}:{password}".encode("utf-8")
        ).decode("ascii")
        headers["Authorization"] = f"Basic {encoded}"

    method = config.get("method", "GET")
    data = None
    if method == "POST":
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method=method,
    )
    opener = urllib.request.build_opener(_NoRedirectHandler)
    try:
        with opener.open(request, timeout=20) as response:
            response_data = response.read(MAX_RESPONSE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"ESSL request failed: {exc}") from exc
    if len(response_data) > MAX_RESPONSE_BYTES:
        raise RuntimeError("ESSL response exceeded the 10 MB size limit.")
    try:
        return json.loads(response_data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("ESSL did not return a valid UTF-8 JSON response.") from exc


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        _request,
        _response,
        _code,
        _message,
        _headers,
        _new_url,
    ):
        del _request, _response, _code, _message, _headers, _new_url
        return None


def _extract_records(config: dict[str, Any], response: Any) -> list[Any]:
    records = _read_path(response, config.get("records_path", ""))
    if not isinstance(records, list):
        raise ValueError(
            "Configured response records path did not resolve to a JSON array."
        )
    return records


def _issue_key(
    employee_id: Any,
    timestamp: Any,
    punch_id: Any,
    device_id: Any,
) -> str:
    parts = [employee_id, timestamp, punch_id, device_id]
    raw_key = json.dumps(parts, ensure_ascii=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _store_issue(
    sync_id: str,
    employee_id: Any,
    timestamp: Any,
    punch_id: Any,
    device_id: Any,
    reason: str,
    now: datetime,
    import_id: str | None = None,
) -> None:
    # Keep a separate issue document per file import so earlier import issues
    # remain available for audit instead of being overwritten by a later upload.
    issue_key = _issue_key(employee_id, timestamp, punch_id, device_id)
    if import_id:
        issue_key = hashlib.sha256(f"{import_id}:{issue_key}".encode("utf-8")).hexdigest()
    essl_punch_issues_collection.update_one(
        {"issue_key": issue_key},
        {
            "$set": {
                "last_sync_id": sync_id,
                "last_reason": reason,
                "last_seen_at": now,
                **({"import_id": import_id} if import_id else {}),
            },
            "$setOnInsert": {
                "essl_employee_id": str(employee_id)[:128] if employee_id is not None else None,
                "punch_timestamp": str(timestamp)[:128] if timestamp is not None else None,
                "punch_id": str(punch_id)[:256] if punch_id is not None else None,
                "device_id": str(device_id)[:128] if device_id is not None else None,
                "first_seen_at": now,
            },
            "$unset": {
                "resolved_at": "",
                "resolved_sync_id": "",
            },
        },
        upsert=True,
    )


def _resolve_punch_issue(
    sync_id: str,
    employee_id: Any,
    timestamp: Any,
    punch_id: Any,
    device_id: Any,
    now: datetime,
) -> None:
    essl_punch_issues_collection.update_one(
        {
            "issue_key": _issue_key(
                employee_id,
                timestamp,
                punch_id,
                device_id,
            ),
            "resolved_at": {"$exists": False},
        },
        {
            "$set": {
                "resolved_at": now,
                "resolved_sync_id": sync_id,
            }
        },
    )


def _save_raw_punch(
    sync_id: str,
    user: dict[str, Any],
    essl_employee_id: str,
    punch_at: datetime,
    punch_id: Any,
    device_id: Any,
    now: datetime,
    source: str = "ESSL",
) -> None:
    key = _issue_key(
        essl_employee_id,
        punch_at.isoformat(),
        punch_id,
        device_id,
    )
    essl_raw_punches_collection.update_one(
        {"punch_key": key},
        {
            "$set": {"last_sync_id": sync_id, "last_seen_at": now},
            "$setOnInsert": {
                "punch_key": key,
                "user_id": str(user["_id"]),
                "employee_id": user.get("employee_id", ""),
                "essl_employee_id": essl_employee_id,
                "punch_at": punch_at.astimezone(timezone.utc),
                "punch_id": str(punch_id)[:256] if punch_id is not None else None,
                "device_id": str(device_id)[:128] if device_id is not None else None,
                "source": source,
                "first_sync_id": sync_id,
                "received_at": now,
            },
        },
        upsert=True,
    )


def _load_daily_raw_punches(
    user_id: str,
    attendance_date: str,
) -> list[dict[str, datetime]]:
    selected = date.fromisoformat(attendance_date)
    start = datetime.combine(selected, time.min, tzinfo=COMPANY_TZ)
    end = start + timedelta(days=1)
    records = essl_raw_punches_collection.find(
        {
            "user_id": user_id,
            "punch_at": {
                "$gte": start.astimezone(timezone.utc),
                "$lt": end.astimezone(timezone.utc),
            },
        },
        {"punch_at": 1},
    )
    punches = []
    for record in records:
        punch_at = record.get("punch_at")
        if not isinstance(punch_at, datetime):
            continue
        if punch_at.tzinfo is None:
            punch_at = punch_at.replace(tzinfo=timezone.utc)
        punches.append({"timestamp": punch_at.astimezone(COMPANY_TZ)})
    return punches


def _save_daily_attendance(
    user: dict[str, Any],
    attendance_date: str,
    group: list[dict[str, Any]],
    sync_id: str,
    now: datetime,
) -> Literal["created", "updated", "unchanged", "protected"]:
    punches_by_time = {item["timestamp"]: item for item in group}
    punches = sorted(punches_by_time.values(), key=lambda item: item["timestamp"])
    first = punches[0]["timestamp"]
    last = punches[-1]["timestamp"] if len(punches) > 1 else None
    key = {
        "user_id": str(user["_id"]),
        "attendance_date": attendance_date,
    }
    values = {
        "employee_id": user.get("employee_id", ""),
        "status": "Present",
        "attendance_type": "OFFICE",
        "attendance_source": "ESSL",
        "check_in_time": first.strftime("%H:%M"),
        "check_out_time": last.strftime("%H:%M") if last else None,
        "first_punch": first.astimezone(timezone.utc),
        "last_punch": last.astimezone(timezone.utc) if last else None,
        "last_essl_sync_at": now,
        "last_essl_sync_id": sync_id,
        "updated_at": now,
    }
    existing = attendance_collection.find_one(key)
    if existing:
        if existing.get("attendance_source") != "ESSL":
            return "protected"
        if _daily_values_match(existing, values):
            return "unchanged"
        attendance_collection.update_one(
            {**key, "attendance_source": "ESSL"},
            {"$set": values},
        )
        return "updated"

    try:
        attendance_collection.update_one(
            key,
            {
                "$set": values,
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
        return "created"
    except DuplicateKeyError:
        raced = attendance_collection.find_one(key)
        if not raced or raced.get("attendance_source") != "ESSL":
            return "protected"
        if _daily_values_match(raced, values):
            return "unchanged"
        attendance_collection.update_one(
            {**key, "attendance_source": "ESSL"},
            {"$set": values},
        )
        return "updated"


def _daily_values_match(
    existing: dict[str, Any],
    incoming: dict[str, Any],
) -> bool:
    fields = (
        "employee_id",
        "status",
        "attendance_type",
        "attendance_source",
        "check_in_time",
        "check_out_time",
        "first_punch",
        "last_punch",
    )
    for field in fields:
        stored = existing.get(field)
        value = incoming[field]
        if isinstance(stored, datetime) and isinstance(value, datetime):
            if stored.tzinfo is None:
                stored = stored.replace(tzinfo=timezone.utc)
            if stored.astimezone(timezone.utc) != value.astimezone(timezone.utc):
                return False
        elif stored != value:
            return False
    return True


def _header_key(value: Any) -> str:
    return "".join(ch for ch in str(value or "").casefold() if ch.isalnum())


def _parse_excel_datetime(value: Any, selected_date: Any = None) -> datetime | None:
    """Parse a timestamp or a time cell paired with a row's attendance date."""
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, datetime):
        result = value
        if selected_date is not None and result.year < 1900:
            base = _parse_excel_datetime(selected_date)
            if base is not None:
                result = datetime.combine(base.date(), result.time())
    elif isinstance(value, date):
        result = datetime.combine(value, time.min)
    elif isinstance(value, time):
        if selected_date is None:
            return None
        base = _parse_excel_datetime(selected_date)
        if base is None:
            return None
        result = datetime.combine(base.date(), value)
    else:
        text = str(value).strip()
        result = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M", "%d-%m-%Y %H:%M", "%d/%m/%Y %H:%M", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%B-%Y", "%d-%b-%Y", "%B %d %Y", "%b %d %Y", "%I:%M:%S %p", "%I:%M %p", "%H:%M:%S", "%H:%M"):
            try:
                parsed = datetime.strptime(text, fmt)
                if fmt in ("%I:%M:%S %p", "%I:%M %p", "%H:%M:%S", "%H:%M"):
                    if selected_date is None:
                        return None
                    base = _parse_excel_datetime(selected_date)
                    if base is None:
                        return None
                    result = datetime.combine(base.date(), parsed.time())
                else:
                    result = parsed
                break
            except ValueError:
                continue
        if result is None:
            return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=COMPANY_TZ)
    return result.astimezone(COMPANY_TZ)


def _read_attendance_upload(filename: str, content: bytes) -> tuple[list[dict[str, Any]], int]:
    """Read raw ESSL logs and repeated-date eTimeTrackLite daily reports."""
    ext = os.path.splitext(filename.casefold())[1]
    try:
        if ext == ".csv":
            text = content.decode("utf-8-sig", errors="replace")
            try:
                dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
            except csv.Error:
                dialect = csv.excel
            rows = list(csv.reader(io.StringIO(text), dialect))
        elif ext == ".xlsx":
            from openpyxl import load_workbook
            workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            sheet = workbook.active
            rows = [list(row) for row in sheet.iter_rows(values_only=True)]
            workbook.close()
        elif ext == ".xls":
            import xlrd
            workbook = xlrd.open_workbook(file_contents=content)
            sheet = workbook.sheet_by_index(0)
            rows = [[sheet.cell_value(r, c) for c in range(sheet.ncols)] for r in range(sheet.nrows)]
            for r_idx, row in enumerate(rows):
                for c_idx, value in enumerate(row):
                    if isinstance(value, (int, float)) and 0 < value < 80000:
                        if sheet.cell_type(r_idx, c_idx) == xlrd.XL_CELL_DATE:
                            try:
                                rows[r_idx][c_idx] = xlrd.xldate.xldate_as_datetime(value, workbook.datemode)
                            except Exception:
                                pass
        else:
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Upload a .csv, .xls, or .xlsx file.")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The uploaded attendance file could not be read.") from exc

    if not rows:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The uploaded file is empty.")

    def parse_report_date(row: list[Any]) -> date | None:
        # eTimeTrackLite places the date after a standalone "Date" label,
        # sometimes with blank/merged columns between the label and value.
        if not row or _header_key(row[0]) != "date":
            return None
        for value in row[1:]:
            if value is None or not str(value).strip():
                continue
            parsed = _parse_excel_datetime(value)
            if parsed:
                return parsed.date()
            text = str(value).strip()
            for fmt in ("%d-%B-%Y", "%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%b %d %Y", "%B %d %Y"):
                try:
                    return datetime.strptime(text, fmt).date()
                except ValueError:
                    continue
        return None

    # eTimeTrackLite's daily report repeats a date banner and a table header
    # for every day. Detect each section instead of assuming one global header.
    report_sections: list[tuple[int, date, dict[str, int]]] = []
    for idx, row in enumerate(rows):
        section_date = parse_report_date(row)
        if section_date is None:
            continue
        for header_idx in range(idx + 1, min(idx + 8, len(rows))):
            header_row = rows[header_idx]
            keys = {_header_key(value): col for col, value in enumerate(header_row)
                    if value is not None and str(value).strip()}
            if "ecode" in keys and "name" in keys and ("sno" in keys or "department" in keys):
                report_sections.append((header_idx, section_date, keys))
                break

    result: list[dict[str, Any]] = []
    invalid = 0
    if report_sections:
        for section_number, (header_idx, section_date, header_map) in enumerate(report_sections):
            next_header_idx = report_sections[section_number + 1][0] if section_number + 1 < len(report_sections) else len(rows)
            emp_col = header_map.get("ecode")
            if emp_col is None:
                continue
            in_cols = sorted((idx, key) for key, idx in header_map.items()
                             if re.fullmatch(r"in\d+", key))
            out_cols = sorted((idx, key) for key, idx in header_map.items()
                              if re.fullmatch(r"out\d+", key))
            # Keep pair identity in each item, but do not fabricate missing halves.
            for row_idx in range(header_idx + 1, next_header_idx):
                row = rows[row_idx]
                if parse_report_date(row) is not None:
                    break
                if emp_col >= len(row) or row[emp_col] is None or not str(row[emp_col]).strip():
                    continue
                raw_employee = row[emp_col]
                if isinstance(raw_employee, float) and raw_employee.is_integer():
                    employee_id = str(int(raw_employee))
                else:
                    employee_id = str(raw_employee).strip()
                # A row is an employee record only if it has a code and some
                # employee-like content; repeated report footer rows are ignored.
                punch_columns = sorted(
                    [(col, "IN") for col, _key in in_cols] +
                    [(col, "OUT") for col, _key in out_cols]
                )
                punches_in_row: list[tuple[Any, str]] = []
                for col, direction in punch_columns:
                    if col < len(row) and row[col] not in (None, "") and str(row[col]).strip() not in {"-", "00:00", "00:00:00"}:
                        punches_in_row.append((row[col], direction))
                for raw_time, direction in punches_in_row:
                    stamp = _parse_excel_datetime(raw_time, section_date)
                    if stamp is None:
                        invalid += 1
                        continue
                    result.append({
                        "essl_employee_id": employee_id,
                        "punch_at": stamp,
                        "direction": direction,
                        "source_row": row_idx + 1,
                        "employee_name": str(row[header_map.get("name", -1)] or "").strip() if header_map.get("name", -1) < len(row) else "",
                        "essl_department": str(row[header_map.get("department", -1)] or "").strip() if header_map.get("department", -1) < len(row) else "",
                        "shift": str(row[header_map.get("shift", -1)] or "").strip() if header_map.get("shift", -1) < len(row) else "",
                    })
        if not result:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "The ESSL daily report sections were detected, but no valid punches were found. Check the date sections and IN/OUT columns.",
            )
        return result, invalid

    # Fallback for flat punch exports and daily tables with one header row.
    employee_names = {"employeeid", "employeecode", "esslemployeeid", "userid", "usercode", "empid", "empcode", "ecode", "enrollid", "enrollmentno", "enrollmentnumber"}
    date_names = {"date", "attendancedate", "punchdate", "transactiondate", "logdate", "workdate"}
    timestamp_names = {"punchtime", "timestamp", "datetime", "punchtimestamp", "transactiontime", "checkintime", "checkouttime", "datetimepunch"}
    time_names = {"time", "punchtime", "checkintime", "checkouttime"}
    header_index = None
    header_map: dict[str, int] = {}
    for idx, row in enumerate(rows[:100]):
        keys = {_header_key(v): col for col, v in enumerate(row) if v is not None and str(v).strip()}
        has_employee = any(k in employee_names for k in keys)
        has_time = any(k in timestamp_names or k in date_names or k.startswith("in") or k.startswith("out") for k in keys)
        if has_employee and has_time:
            header_index, header_map = idx, keys
            break
    if header_index is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Could not identify the header row. Expected an Employee ID/Code column and a Punch Date/Time or IN/OUT columns.")

    def find_col(names: set[str]) -> int | None:
        return next((header_map[name] for name in names if name in header_map), None)

    emp_col = find_col(employee_names)
    date_col = find_col(date_names)
    stamp_col = find_col(timestamp_names)
    direction_col = find_col({"direction", "eventtype", "punchtype", "status", "inout", "verifytype"})
    in_cols = sorted((idx, key) for key, idx in header_map.items() if re.fullmatch(r"in\d+", key))
    out_cols = sorted((idx, key) for key, idx in header_map.items() if re.fullmatch(r"out\d+", key))
    if not in_cols and not out_cols and stamp_col is None and not any(k in header_map for k in time_names):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No punch timestamp columns were found in this file.")

    for row_num, row in enumerate(rows[header_index + 1:], start=header_index + 2):
        if emp_col is None or emp_col >= len(row):
            continue
        employee = row[emp_col]
        if employee is None or str(employee).strip() == "":
            continue
        row_date = row[date_col] if date_col is not None and date_col < len(row) else None
        values: list[tuple[Any, str | None]] = []
        if stamp_col is not None and stamp_col < len(row) and row[stamp_col] not in (None, ""):
            direction = str(row[direction_col]).strip().upper() if direction_col is not None and direction_col < len(row) and row[direction_col] is not None else None
            values.append((row[stamp_col], direction))
        else:
            t_col = find_col(time_names)
            if t_col is not None and t_col < len(row) and row[t_col] not in (None, ""):
                direction = str(row[direction_col]).strip().upper() if direction_col is not None and direction_col < len(row) and row[direction_col] is not None else None
                values.append((row[t_col], direction))
            for col, key in in_cols:
                if col < len(row) and row[col] not in (None, "") and str(row[col]).strip() not in {"00:00:00", "00:00", "-"}:
                    values.append((row[col], "IN"))
            for col, key in out_cols:
                if col < len(row) and row[col] not in (None, "") and str(row[col]).strip() not in {"00:00:00", "00:00", "-"}:
                    values.append((row[col], "OUT"))
        for raw_stamp, direction in values:
            stamp = _parse_excel_datetime(raw_stamp, row_date)
            if stamp is None:
                invalid += 1
                continue
            if isinstance(employee, float) and employee.is_integer():
                employee = str(int(employee))
            result.append({"essl_employee_id": str(employee).strip(), "punch_at": stamp, "direction": direction, "source_row": row_num})
    if not result:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"No valid punch timestamps were found. Invalid/empty punch values: {invalid}.")
    return result, invalid


def _read_essl_report_employee_metadata(filename: str, content: bytes) -> dict[str, dict[str, str]]:
    """Collect employee rows, including people who have no punches in the report."""
    if os.path.splitext(filename.casefold())[1] != ".xlsx":
        return {}
    try:
        from openpyxl import load_workbook
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        rows = [list(row) for row in workbook.active.iter_rows(values_only=True)]
        workbook.close()
    except Exception:
        return {}
    metadata: dict[str, dict[str, str]] = {}
    for idx, row in enumerate(rows):
        if not row or _header_key(row[0]) != "date":
            continue
        header_map: dict[str, int] | None = None
        for header_idx in range(idx + 1, min(idx + 8, len(rows))):
            candidate = {_header_key(value): col for col, value in enumerate(rows[header_idx])
                         if value is not None and str(value).strip()}
            if "ecode" in candidate and "name" in candidate:
                header_map = candidate
                break
        if not header_map:
            continue
        for data_row in rows[header_idx + 1:]:
            if data_row and _header_key(data_row[0]) == "date":
                break
            code_idx = header_map["ecode"]
            if code_idx >= len(data_row) or data_row[code_idx] in (None, ""):
                continue
            raw_code = data_row[code_idx]
            code = str(int(raw_code)) if isinstance(raw_code, float) and raw_code.is_integer() else str(raw_code).strip()
            if not code:
                continue
            def cell(field: str) -> str:
                col = header_map.get(field)
                return str(data_row[col]).strip() if col is not None and col < len(data_row) and data_row[col] is not None else ""
            metadata.setdefault(code, {
                "essl_employee_id": code,
                "name": cell("name"),
                "department": cell("department"),
                "shift": cell("shift"),
            })
    return metadata


@router.post("/preview-file")
async def preview_essl_attendance_file(
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
):
    """Validate and preview an ESSL file without writing attendance data."""
    _require_essl_admin(authorization)
    filename = file.filename or "attendance.xlsx"
    content = await file.read()
    if len(content) > 15 * 1024 * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File size must not exceed 15 MB.")
    punches, invalid_values = _read_attendance_upload(filename, content)
    unique_employees: dict[str, dict[str, Any]] = _read_essl_report_employee_metadata(filename, content)
    daily_keys: set[tuple[str, str]] = set()
    seen_punches: set[tuple[str, datetime]] = set()
    duplicate_punches = 0
    for punch in punches:
        code = str(punch["essl_employee_id"]).strip()
        unique_employees.setdefault(code, {
            "essl_employee_id": code,
            "name": punch.get("employee_name") or "",
            "department": punch.get("essl_department") or "",
            "shift": punch.get("shift") or "",
        })
        daily_keys.add((code, punch["punch_at"].date().isoformat()))
        punch_identity = (code, punch["punch_at"])
        if punch_identity in seen_punches:
            duplicate_punches += 1
        seen_punches.add(punch_identity)
    mapping = {
        item["essl_employee_id_normalized"]: item
        for item in users_collection.find({
            "employee_id": {"$type": "string", "$ne": ""},
            "essl_employee_id_normalized": {"$type": "string"},
        }, {"essl_employee_id_normalized": 1, "name": 1, "employee_id": 1, "department": 1, "is_active": 1})
    }
    # Employee master records use `status` (for example, "Active") rather than
    # consistently storing `is_active`. Do not filter on a field that may not
    # exist; that was causing every HRMS department to appear blank in ESSL.
    departments: dict[str, str] = {}
    for item in employees_collection.find(
        {"employee_id": {"$type": "string", "$ne": ""}},
        {"employee_id": 1, "department": 1, "status": 1, "is_active": 1},
    ):
        employee_status = str(item.get("status", "Active")).strip().casefold()
        if item.get("is_active") is False or employee_status in {"inactive", "terminated", "disabled"}:
            continue
        employee_key = str(item.get("employee_id", "")).strip()
        department_name = str(item.get("department", "") or "").strip()
        if employee_key and department_name:
            departments[employee_key] = department_name
    employee_rows = []
    for code, item in sorted(unique_employees.items(), key=lambda pair: pair[0].casefold()):
        mapped = mapping.get(_normalise_employee_id(code))
        hrms_department = (mapped.get("department") or departments.get(mapped.get("employee_id"), "")) if mapped else ""
        essl_department = str(item.get("department") or "").strip()
        department_mismatch = bool(essl_department and hrms_department and
                                   essl_department.casefold() != hrms_department.casefold())
        employee_rows.append({
            **item,
            "mapping_status": "unmapped" if not mapped else ("department_mismatch" if department_mismatch else "mapped"),
            "hrms_employee_id": mapped.get("employee_id", "") if mapped else "",
            "hrms_employee_name": mapped.get("name", "") if mapped else "",
            "hrms_department": hrms_department,
            "department_mismatch": department_mismatch,
        })
    stamps = [punch["punch_at"].date().isoformat() for punch in punches]
    return {
        "filename": filename,
        "status": "VALIDATED",
        "reporting_period": {"from_date": min(stamps), "to_date": max(stamps)},
        "employee_count": len(unique_employees),
        "daily_employee_records": len(daily_keys),
        "valid_punches": len(punches),
        "potential_duplicate_punches": duplicate_punches,
        "invalid_punch_values": invalid_values,
        "unmapped_employee_count": sum(1 for item in employee_rows if item["mapping_status"] == "unmapped"),
        "mapped_employee_count": sum(1 for item in employee_rows if item["mapping_status"] != "unmapped"),
        "department_mismatch_count": sum(1 for item in employee_rows if item.get("department_mismatch")),
        "employees": employee_rows,
        "preview": [
            {
                "employee_code": punch["essl_employee_id"],
                "employee_name": punch.get("employee_name", ""),
                "attendance_date": punch["punch_at"].date().isoformat(),
                "punch_time": punch["punch_at"].strftime("%H:%M:%S"),
                "direction": punch.get("direction"),
            }
            for punch in punches[:100]
        ],
        "preview_limit": 100,
        # Partial imports are supported: only block if no ESSL employee is mapped.
        "requires_mapping_before_import": not any(item["mapping_status"] != "unmapped" for item in employee_rows),
    }


@router.post("/import-file")
async def import_essl_attendance_file(
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
):
    admin = _require_essl_admin(authorization)
    filename = file.filename or "attendance.csv"
    content = await file.read()
    if len(content) > 15 * 1024 * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File size must not exceed 15 MB.")
    punches, invalid_values = _read_attendance_upload(filename, content)
    now = datetime.now(timezone.utc)
    sync_id = str(ObjectId())
    import_id = create_excel_import_id()
    mapping = {
        user["essl_employee_id_normalized"]: user
        for user in users_collection.find({
            "employee_id": {"$type": "string", "$ne": ""},
            "essl_employee_id_normalized": {"$type": "string"},
        })
    }

    # Build the complete upload snapshot first. Do not mutate stored punches while
    # parsing/mapping the file: a later row error must not leave a partially
    # replaced attendance day.
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    users_by_id: dict[str, dict[str, Any]] = {}
    unmatched: set[str] = set()
    duplicate_in_file = 0
    seen: set[tuple[str, datetime]] = set()
    for punch in punches:
        normalized = _normalise_employee_id(punch["essl_employee_id"])
        user = mapping.get(normalized)
        if not user:
            unmatched.add(normalized or punch["essl_employee_id"])
            _store_issue(
                sync_id,
                punch["essl_employee_id"],
                punch["punch_at"].isoformat(),
                None,
                None,
                "No HRMS employee is mapped to this ESSL employee ID.",
                now,
                import_id=import_id,
            )
            continue
        stamp = punch["punch_at"]
        user_id = str(user["_id"])
        unique = (user_id, stamp)
        if unique in seen:
            duplicate_in_file += 1
            continue
        seen.add(unique)
        users_by_id[user_id] = user
        grouped.setdefault((user_id, stamp.date().isoformat()), []).append({"timestamp": stamp, "essl_employee_id": punch["essl_employee_id"]})

    created = updated = protected = unchanged = 0
    imported = 0
    for (user_id, day), day_punches in grouped.items():
        user = users_by_id[user_id]
        attendance_key = {"user_id": user_id, "attendance_date": day}
        existing_attendance = attendance_collection.find_one(attendance_key)
        # Manual and WFH attendance is deliberately protected. Do not inject ESSL
        # punches behind a protected record, because the daily breakdown reads the
        # raw-punch collection as its source for office intervals.
        if existing_attendance and existing_attendance.get("attendance_source") != "ESSL":
            protected += 1
            continue

        selected = date.fromisoformat(day)
        day_start = datetime.combine(selected, time.min, tzinfo=COMPANY_TZ)
        day_end = day_start + timedelta(days=1)
        utc_start = day_start.astimezone(timezone.utc)
        utc_end = day_end.astimezone(timezone.utc)

        # An uploaded workbook is the authoritative snapshot for each employee/day
        # it contains. Remove obsolete file-import punches for that day before
        # saving the corrected snapshot. Preserve API-synchronized punches: API sync
        # runs have a matching essl_sync_history record, while legacy file imports
        # did not create one. This also cleans up legacy file-import records created
        # before source="ESSL_FILE" was introduced.
        old_raw = list(essl_raw_punches_collection.find(
            {
                "user_id": user_id,
                "punch_at": {"$gte": utc_start, "$lt": utc_end},
            },
            {"_id": 1, "source": 1, "first_sync_id": 1},
        ))
        candidate_sync_ids = []
        for old in old_raw:
            if old.get("source") == "ESSL_FILE":
                continue
            try:
                candidate_sync_ids.append(ObjectId(str(old.get("first_sync_id", ""))))
            except Exception:
                pass
        api_sync_ids = set()
        if candidate_sync_ids:
            api_sync_ids = {
                str(item["_id"])
                for item in essl_sync_history_collection.find(
                    {"_id": {"$in": candidate_sync_ids}}, {"_id": 1}
                )
            }
        obsolete_ids = []
        for old in old_raw:
            first_sync_id = str(old.get("first_sync_id", ""))
            if old.get("source") == "ESSL_FILE" or first_sync_id not in api_sync_ids:
                obsolete_ids.append(old["_id"])
        if obsolete_ids:
            essl_raw_punches_collection.delete_many({"_id": {"$in": obsolete_ids}})

        for item in sorted(day_punches, key=lambda entry: entry["timestamp"]):
            stamp = item["timestamp"]
            _save_raw_punch(sync_id, user, str(item["essl_employee_id"]), stamp, None, None, now, source="ESSL_FILE")
            _resolve_punch_issue(sync_id, item["essl_employee_id"], stamp.isoformat(), None, None, now)
            imported += 1

        result = _save_daily_attendance(user, day, _load_daily_raw_punches(user_id, day), sync_id, now)
        if result == "created": created += 1
        elif result == "updated": updated += 1
        elif result == "unchanged": unchanged += 1
        elif result == "protected": protected += 1

    # Mark the import as latest only after the file has been processed.
    # Historical issue documents are retained, but the UI endpoint below filters
    # to this import ID only.
    set_latest_excel_import(db, import_id)

    return {
        "status": "COMPLETED",
        "import_id": import_id,
        "filename": filename,
        "rows_with_punches": len(punches),
        "punches_imported": imported,
        "duplicate_punches_in_file": duplicate_in_file,
        "invalid_punch_values": invalid_values,
        "unmatched_employee_ids": sorted(unmatched),
        "attendance_records_created": created,
        "attendance_records_updated": updated,
        "attendance_records_unchanged": unchanged,
        "attendance_records_protected": protected,
        "requested_by": str(admin.get("_id", "")),
        "import_behavior": "The uploaded file replaces prior file-import punches only for employee/date pairs present in the file; API-synchronized punches and manual/WFH attendance are preserved.",
    }


@router.get("/config")
def get_essl_config(authorization: str | None = Header(default=None)):
    _require_essl_admin(authorization)
    return _public_config(essl_config_collection.find_one({"_id": CONFIG_ID}))


@router.put("/config")
def save_essl_config(
    payload: EsslConfigPayload,
    authorization: str | None = Header(default=None),
):
    admin = _require_essl_admin(authorization)
    _validate_endpoint(payload.api_url, payload.api_key_header)
    existing = essl_config_collection.find_one({"_id": CONFIG_ID})
    try:
        previous_credentials = (
            _decrypt_credentials(existing)
            if existing and existing.get("credentials_encrypted")
            else {}
        )
    except RuntimeError as exc:
        logger.exception("Stored ESSL configuration could not be decrypted")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    credentials = dict(previous_credentials)
    previous_auth_type = existing.get("auth_type") if existing else None
    if previous_auth_type != payload.auth_type:
        credentials.pop("auth_secret", None)
        credentials.pop("auth_username", None)
        credentials.pop("auth_password", None)
    if payload.auth_type == "none":
        credentials.pop("auth_secret", None)
        credentials.pop("auth_username", None)
        credentials.pop("auth_password", None)
    elif payload.auth_type in {"bearer", "api_key"}:
        if payload.auth_secret:
            credentials["auth_secret"] = payload.auth_secret
        if not credentials.get("auth_secret"):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Enter an API token/key before saving this authentication type.",
            )
    else:
        if payload.auth_username:
            credentials["auth_username"] = payload.auth_username
        if payload.auth_password:
            credentials["auth_password"] = payload.auth_password
        if not credentials.get("auth_username") or not credentials.get("auth_password"):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Enter both the ESSL username and password before saving.",
            )

    credentials["request_params"] = (
        payload.request_params
        if payload.request_params is not None
        else previous_credentials.get("request_params", {})
    )
    credentials["request_body"] = (
        payload.request_body
        if payload.request_body is not None
        else previous_credentials.get("request_body", {})
    )
    credentials["request_headers"] = (
        payload.request_headers
        if payload.request_headers is not None
        else previous_credentials.get("request_headers", {})
    )

    if credentials:
        try:
            encrypted = _encrypt_credentials(credentials)
        except RuntimeError as exc:
            logger.exception("ESSL configuration encryption is unavailable")
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    else:
        encrypted = None

    config = payload.model_dump(
        exclude={
            "auth_secret",
            "auth_username",
            "auth_password",
            "request_params",
            "request_body",
            "request_headers",
        }
    )
    config["api_url"] = _validate_endpoint(
        config["api_url"],
        config["api_key_header"],
    )
    config.update(
        _id=CONFIG_ID,
        credentials_encrypted=encrypted,
        has_credentials=payload.auth_type != "none",
        has_request_parameters=bool(credentials.get("request_params")),
        has_request_body=bool(credentials.get("request_body")),
        has_request_headers=bool(credentials.get("request_headers")),
        updated_at=datetime.now(timezone.utc),
        updated_by=str(admin["_id"]),
    )
    essl_config_collection.replace_one({"_id": CONFIG_ID}, config, upsert=True)
    return _public_config(config)


@router.get("/employees")
def list_essl_employee_mappings(
    authorization: str | None = Header(default=None),
):
    """List HRMS users with departments resolved from the authoritative HR data.

    Department can be stored on the login user, employee master, employee
    details, or as a department reference. Resolve these sources in priority
    order without changing the underlying employee record.
    """
    _require_essl_admin(authorization)
    employees = list(users_collection.find(
        {"employee_id": {"$type": "string", "$ne": ""}},
        {
            "name": 1, "employee_id": 1, "essl_employee_id": 1,
            "is_active": 1, "department": 1, "department_id": 1,
            "role": 1, "status": 1,
        },
    ).sort("name", 1))

    def clean(value: Any) -> str:
        if isinstance(value, dict):
            return str(value.get("department_name") or value.get("name") or value.get("department") or "").strip()
        return str(value or "").strip()

    # Employee master keyed by employee_id is the authoritative source when
    # the login-user record does not carry the department itself.
    employee_master_by_id: dict[str, dict[str, Any]] = {}
    for record in employees_collection.find(
        {"employee_id": {"$type": "string", "$ne": ""}},
        {"employee_id": 1, "department": 1, "department_id": 1, "status": 1, "is_active": 1},
    ):
        key = str(record.get("employee_id") or "").strip().casefold()
        if key:
            employee_master_by_id[key] = record

    # Employee details may contain the latest department assignment.
    details_collection = db["employee_details"]
    details_by_employee_id: dict[str, dict[str, Any]] = {}
    details_by_user_id: dict[str, dict[str, Any]] = {}
    for record in details_collection.find({}, {
        "employee_id": 1, "user_id": 1, "department": 1, "department_id": 1,
    }):
        employee_key = str(record.get("employee_id") or "").strip().casefold()
        user_key = str(record.get("user_id") or "").strip()
        if employee_key:
            details_by_employee_id[employee_key] = record
        if user_key:
            details_by_user_id[user_key] = record

    department_names_by_id: dict[str, str] = {}
    for record in db["departments"].find({}, {"department_name": 1, "name": 1}):
        department_id = str(record.get("_id") or "")
        department_name = clean(record.get("department_name") or record.get("name"))
        if department_id and department_name:
            department_names_by_id[department_id] = department_name

    result = []
    for employee in employees:
        employee_id = str(employee.get("employee_id") or "").strip()
        employee_key = employee_id.casefold()
        user_id = str(employee["_id"])
        master = employee_master_by_id.get(employee_key, {})
        details = details_by_user_id.get(user_id) or details_by_employee_id.get(employee_key) or {}

        department = (
            clean(employee.get("department"))
            or clean(details.get("department"))
            or clean(master.get("department"))
        )
        department_ref = (
            employee.get("department_id")
            or details.get("department_id")
            or master.get("department_id")
        )
        if not department and department_ref is not None:
            department = department_names_by_id.get(str(department_ref), "")
        # Some installations store the department as an ObjectId string or
        # reference in the department field rather than the display name.
        if department and department in department_names_by_id:
            department = department_names_by_id[department]

        active = employee.get("is_active")
        if active is None:
            status_value = str(employee.get("status") or master.get("status") or "Active").strip().casefold()
            active = status_value not in {"inactive", "terminated", "disabled"}
        result.append({
            "user_id": user_id,
            "name": employee.get("name", "") or master.get("employee_name", ""),
            "employee_id": employee_id,
            "essl_employee_id": employee.get("essl_employee_id", ""),
            "is_active": bool(active),
            "role": employee.get("role", ""),
            "department": department or "",
        })
    return result


@router.put("/employees/{user_id}")
def update_essl_employee_mapping(
    user_id: str,
    payload: EsslEmployeeMappingPayload,
    authorization: str | None = Header(default=None),
):
    admin = _require_essl_admin(authorization)
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid employee ID.")
    employee = users_collection.find_one(
        {
            "_id": ObjectId(user_id),
            "employee_id": {"$type": "string", "$ne": ""},
        }
    )
    if not employee:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Employee not found.")
    normalized = _normalise_employee_id(payload.essl_employee_id)
    values: dict[str, Any] = {
        "essl_employee_id": payload.essl_employee_id,
        "essl_employee_id_normalized": normalized,
        "essl_employee_mapping_updated_at": datetime.now(timezone.utc),
        "essl_employee_mapping_updated_by": str(admin["_id"]),
    }
    if normalized is None:
        values.pop("essl_employee_id")
        values.pop("essl_employee_id_normalized")
    try:
        users_collection.update_one({"_id": ObjectId(user_id)}, {"$set": values})
    except DuplicateKeyError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "That ESSL employee ID is already mapped to another employee.",
        ) from exc
    return {
        "user_id": user_id,
        "employee_id": employee.get("employee_id", ""),
        "essl_employee_id": payload.essl_employee_id,
    }


@router.post("/test-connection")
def test_essl_connection(authorization: str | None = Header(default=None)):
    _require_essl_admin(authorization)
    config = _load_config()
    try:
        credentials = _decrypt_credentials(config)
        response = _request_json(config, credentials, company_today(), company_today())
        records = _extract_records(config, response)
        if records and not any(
            isinstance(record, dict)
            and _read_path(record, config["employee_id_path"]) is not None
            and _read_path(record, config["timestamp_path"]) is not None
            for record in records
        ):
            raise ValueError(
                "ESSL response did not contain the configured employee and timestamp fields."
            )
    except Exception as exc:
        logger.exception("ESSL connection test failed")
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "ESSL connection failed. Verify the endpoint, authentication, and response field mappings.",
        ) from exc
    return {
        "success": True,
        "message": "Connection successful.",
        "records_returned": len(records),
    }


@router.post("/sync")
def synchronize_essl_attendance(
    payload: EsslSyncPayload,
    authorization: str | None = Header(default=None),
):
    admin = _require_essl_admin(authorization)
    if payload.from_date > payload.to_date:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "From Date must not be after To Date.",
        )
    if payload.to_date > company_today():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "ESSL synchronization cannot include a future date.",
        )

    sync_id = str(ObjectId())
    started_at = datetime.now(timezone.utc)
    essl_sync_history_collection.insert_one(
        {
            "_id": ObjectId(sync_id),
            "status": "RUNNING",
            "started_at": started_at,
            "requested_by": str(admin["_id"]),
            "from_date": payload.from_date.isoformat(),
            "to_date": payload.to_date.isoformat(),
        }
    )

    try:
        config = _load_config()
        credentials = _decrypt_credentials(config)
        response = _request_json(
            config,
            credentials,
            payload.from_date,
            payload.to_date,
        )
        records = _extract_records(config, response)
    except Exception as exc:
        logger.exception("ESSL attendance request or response validation failed")
        essl_sync_history_collection.update_one(
            {"_id": ObjectId(sync_id)},
            {
                "$set": {
                    "status": "FAILED",
                    "completed_at": datetime.now(timezone.utc),
                    "error_message": (
                        "ESSL synchronization failed. Existing attendance data has not been changed."
                    ),
                    "failure_type": type(exc).__name__,
                }
            },
        )
        if isinstance(exc, HTTPException) and exc.status_code == status.HTTP_409_CONFLICT:
            raise
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "ESSL synchronization failed. Existing attendance data has not been changed.",
        ) from exc

    now = datetime.now(timezone.utc)
    mapping = {
        user["essl_employee_id_normalized"]: user
        for user in users_collection.find(
            {
                "role": {"$regex": "^employee$", "$options": "i"},
                "essl_employee_id_normalized": {"$type": "string"},
            }
        )
    }
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    processed_punches = 0
    unmatched_employee_ids: set[str] = set()
    invalid_records = 0
    for item in records:
        if not isinstance(item, dict):
            invalid_records += 1
            _store_issue(sync_id, None, None, None, None, "Punch record is not a JSON object.", now)
            continue
        raw_employee_id = _read_path(item, config["employee_id_path"])
        raw_timestamp = _read_path(item, config["timestamp_path"])
        raw_punch_id = _read_path(item, config.get("punch_id_path") or "")
        raw_device_id = _read_path(item, config.get("device_id_path") or "")
        normalized_employee_id = _normalise_employee_id(raw_employee_id)
        if normalized_employee_id is None:
            invalid_records += 1
            _store_issue(
                sync_id,
                raw_employee_id,
                raw_timestamp,
                raw_punch_id,
                raw_device_id,
                "Missing or invalid ESSL employee ID.",
                now,
            )
            continue
        if raw_timestamp is None:
            invalid_records += 1
            _store_issue(
                sync_id,
                raw_employee_id,
                raw_timestamp,
                raw_punch_id,
                raw_device_id,
                "Missing punch timestamp.",
                now,
            )
            continue
        try:
            punch_at = _parse_punch_timestamp(
                raw_timestamp,
                config.get("timestamp_format"),
            )
        except ValueError as exc:
            invalid_records += 1
            _store_issue(
                sync_id,
                raw_employee_id,
                raw_timestamp,
                raw_punch_id,
                raw_device_id,
                str(exc),
                now,
            )
            continue

        attendance_date = punch_at.date().isoformat()
        if not payload.from_date.isoformat() <= attendance_date <= payload.to_date.isoformat():
            continue
        user = mapping.get(normalized_employee_id)
        if not user:
            unmatched_employee_ids.add(normalized_employee_id)
            _store_issue(
                sync_id,
                raw_employee_id,
                raw_timestamp,
                raw_punch_id,
                raw_device_id,
                "No HRMS employee is mapped to this ESSL employee ID.",
                now,
            )
            continue
        processed_punches += 1
        _resolve_punch_issue(
            sync_id,
            raw_employee_id,
            raw_timestamp,
            raw_punch_id,
            raw_device_id,
            now,
        )
        _save_raw_punch(
            sync_id,
            user,
            str(raw_employee_id).strip(),
            punch_at,
            raw_punch_id,
            raw_device_id,
            now,
        )
        grouped.setdefault((str(user["_id"]), attendance_date), []).append(
            {"timestamp": punch_at}
        )

    employees_processed = len({user_id for user_id, _ in grouped})
    created = 0
    updated = 0
    protected = 0
    for (user_id, attendance_date), punches in grouped.items():
        user = next(
            employee
            for employee in mapping.values()
            if str(employee["_id"]) == user_id
        )
        result = _save_daily_attendance(
            user,
            attendance_date,
            punches + _load_daily_raw_punches(user_id, attendance_date),
            sync_id,
            now,
        )
        if result == "created":
            created += 1
        elif result == "updated":
            updated += 1
        elif result == "protected":
            protected += 1

    completed_at = datetime.now(timezone.utc)
    summary = {
        "sync_id": sync_id,
        "status": "COMPLETED",
        "employees_processed": employees_processed,
        "punches_received": len(records),
        "processed_punches": processed_punches,
        "attendance_records_created": created,
        "attendance_records_updated": updated,
        "unmatched_employees": len(unmatched_employee_ids),
        "invalid_records": invalid_records,
        "attendance_records_protected": protected,
        "from_date": payload.from_date.isoformat(),
        "to_date": payload.to_date.isoformat(),
        "started_at": started_at,
        "completed_at": completed_at,
    }
    essl_sync_history_collection.update_one(
        {"_id": ObjectId(sync_id)},
        {"$set": summary | {"requested_by": str(admin["_id"])}},
    )
    return summary


@router.get("/sync-history")
def get_essl_sync_history(
    limit: int = Query(default=20, ge=1, le=100),
    authorization: str | None = Header(default=None),
):
    _require_essl_admin(authorization)
    return [
        {
            **{
                key: value
                for key, value in record.items()
                if key != "_id"
            },
            "sync_id": str(record["_id"]),
        }
        for record in essl_sync_history_collection.find().sort(
            "started_at", -1
        ).limit(limit)
    ]


@router.get("/issues")
def get_essl_punch_issues(
    limit: int = Query(default=100, ge=1, le=500),
    authorization: str | None = Header(default=None),
):
    _require_essl_admin(authorization)
    latest_import_id = get_latest_excel_import_id(db)
    if not latest_import_id:
        return []

    return [
        {
            **{key: value for key, value in issue.items() if key != "_id"},
            "id": str(issue["_id"]),
        }
        for issue in essl_punch_issues_collection.find(
            {
                "import_id": latest_import_id,
                "resolved_at": {"$exists": False},
            }
        ).sort("last_seen_at", -1).limit(limit)
    ]
