"""FastAPI application entrypoint, middleware and router registration."""

import secrets
import smtplib
import os
import logging
import threading
import hashlib
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from bson import ObjectId
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import AliasChoices, BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator
from pymongo.errors import DuplicateKeyError
from app.security import hash_password, verify_password
from app.activation import build_activation_url, hash_activation_token, send_activation_email
from app.password_reset import router as password_reset_router
from app.employee_details import router as employee_details_router
from app.reporting_manager import router as reporting_manager_router
from app.leave import router as leave_router
from app.payroll import router as payroll_router, ensure_payroll_collections
from app.session import active_sessions
from app.core_routes import router as core_router, _organization_user

#Attendance
from app.attendance import router as attendance_router, ensure_attendance_indexes
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


app = FastAPI(title="HRMS Backend", description="Backend API for HRMS application", version="1.0.0")
logger = logging.getLogger(__name__)
ORGANIZATION_TIMEZONE = ZoneInfo(os.getenv("ORG_TIMEZONE", "Asia/Kolkata"))


@app.middleware("http")
async def protect_organization_routes(request: Request, call_next):
    organization_prefixes = (
        "/api/companies",
        "/api/business-units",
        "/api/departments",
        "/api/teams",
        "/api/designations",
        "/api/holiday-calendars",
        "/api/holidays",
        "/api/employee-holiday-calendars",
        "/api/organization",
    )
    if not request.url.path.startswith(organization_prefixes):
        return await call_next(request)
    if request.method == "OPTIONS":
        return await call_next(request)
    try:
        user = _organization_user(request.headers.get("authorization"))
        role = str(user.get("role", "")).strip().casefold()
        if role not in {"hr admin", "administrator", "hr manager", "manager", "employee"}:
            raise HTTPException(status_code=403, detail="You are not authorized to view organization information.")
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and role not in {"hr admin", "hr manager", "administrator"}:
            raise HTTPException(status_code=403, detail="Only HR Admin, HR Manager or Administrator can manage organization setup.")
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return await call_next(request)

app.include_router(
    password_reset_router
)

#Attendance Router
app.include_router(attendance_router)
app.include_router(essl_attendance_router)

# Employee Master / employee extra details router
app.include_router(
    employee_details_router
)

# Reporting Manager assignment router
app.include_router(
    reporting_manager_router
)

# Notifications Router
app.include_router(notifications_router)

# Leave Management Router
app.include_router(leave_router)
app.include_router(payroll_router)
app.include_router(core_router)
# --- x ---

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv(
            "CORS_ALLOWED_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173",
        ).split(",")
        if origin.strip()
    ],
    # Permit the Vite frontend from a private LAN address (for phone testing).
    # Keep this limited to local/private IPv4 addresses on the Vite dev port.
    allow_origin_regex=os.getenv(
        "CORS_ALLOWED_ORIGIN_REGEX",
        r"https?://(localhost|127\.0\.0\.1|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(1[6-9]|2[0-9]|3[01])\.\d{1,3}\.\d{1,3}):5173",
    ),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


