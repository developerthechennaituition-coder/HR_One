from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from bson import ObjectId
from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field, field_validator, model_validator
from pymongo.errors import DuplicateKeyError

from app.db.mongodb import db

from app.payslip_pdf import _payslip_pdf_response, payslip_view_data

router = APIRouter(prefix="/api/payroll", tags=["Payroll"])
users_collection = db["users"]
salaries_collection = db["employee_salary_profiles"]


def current_user(authorization: str | None, x_user_id: str | None = None) -> dict:
    """Resolve the authenticated session using the application's session store."""
    from app.session import active_sessions
    token = None
    if authorization:
        value = authorization.strip()
        if value.lower().startswith("bearer "):
            token = value[7:].strip()
    user_id = active_sessions.get(token) if token else None
    # Do not trust a user-id header as authentication: callers must present a
    # valid session token so one employee cannot impersonate another.
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not ObjectId.is_valid(str(user_id)):
        raise HTTPException(status_code=401, detail="Invalid session user")
    user = users_collection.find_one({"_id": ObjectId(str(user_id))})
    if not user or user.get("is_active") is False:
        raise HTTPException(status_code=401, detail="Active user session required")
    return user


def authenticated_user(authorization: str | None, x_user_id: str | None) -> dict:
    """HR-admin-only authorization for salary administration endpoints."""
    user = current_user(authorization, x_user_id)
    if str(user.get("role", "")).strip().casefold() != "hr admin":
        raise HTTPException(status_code=403, detail="Only HR Admin can administer payroll")
    return user


def _employee_identifier(user: dict) -> str:
    employee_id = str(user.get("employee_id") or "").strip()
    if employee_id:
        return employee_id
    # Some legacy user accounts were created before employee_id was stored on
    # the login record. Resolve only by the signed-in account's verified email.
    email = str(user.get("work_email") or user.get("email") or "").strip().casefold()
    if email:
        employee = db["employees"].find_one({"email": {"$regex": f"^{__import__('re').escape(email)}$", "$options": "i"}}, {"employee_id": 1})
        if employee and employee.get("employee_id"):
            return str(employee["employee_id"]).strip()
    raise HTTPException(status_code=403, detail="Your account is not linked to an Employee Master record.")


def money(value: Any, label: str) -> float:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise HTTPException(status_code=422, detail=f"{label} must be a valid amount")
    if not number.is_finite() or number < 0 or number > Decimal("999999999.99"):
        raise HTTPException(status_code=422, detail=f"{label} must be between 0 and 999999999.99")
    return float(number.quantize(Decimal("0.01")))

def _company_snapshot(employee_id: str | None = None) -> dict:
    """Return a snapshot of the company/branch assigned to this employee."""
    company = None
    employee_id = str(employee_id or "").strip()

    if employee_id:
        employee = db["employees"].find_one({"employee_id": employee_id}) or {}
        user = users_collection.find_one({"employee_id": employee_id}) or {}

        # Company Setup may store the assigned employee as an Employee Master
        # document ID, login-user ID, or (in older data) the employee_id itself.
        assigned_ids = {
            str(value)
            for value in (employee.get("_id"), user.get("_id"), employee_id)
            if value is not None and str(value).strip()
        }
        if assigned_ids:
            company = db["companies"].find_one({
                "is_active": {"$ne": False},
                "employee_ids": {"$in": list(assigned_ids)},
            })

        # Legacy fallback: match the employee's recorded work location to a
        # company branch/location, but never choose an arbitrary branch.
        if not company:
            work_location = str(
                employee.get("work_location")
                or employee.get("location")
                or user.get("work_location")
                or user.get("location")
                or ""
            ).strip()
            if work_location:
                company = db["companies"].find_one({
                    "is_active": {"$ne": False},
                    "$or": [
                        {"location": {"$regex": f"^{__import__('re').escape(work_location)}$", "$options": "i"}},
                        {"branch": {"$regex": f"^{__import__('re').escape(work_location)}$", "$options": "i"}},
                        {"branch_code": {"$regex": f"^{__import__('re').escape(work_location)}$", "$options": "i"}},
                    ],
                })

    if not company:
        active_companies = list(db["companies"].find({"is_active": {"$ne": False}}).limit(2))
        # Never assign the primary branch arbitrarily to an employee in a
        # multi-branch organization. Use a fallback only when it is unambiguous.
        if len(active_companies) == 1:
            company = active_companies[0]
        elif not employee_id:
            company = db["companies"].find_one({
                "organization_singleton_key": "primary",
                "is_active": {"$ne": False},
            }) or {}

    branch = str(company.get("branch") or "").strip()
    location = str(company.get("location") or "").strip()
    # Payslip header shows location first; branch is shown only if location is blank.
    display_location = location or branch
    return {
        "name": company.get("legal_entity") or company.get("company") or "HR One",
        "company_name": company.get("company") or company.get("legal_entity") or "HR One",
        "legal_entity": company.get("legal_entity") or "",
        "branch": branch,
        "branch_code": str(company.get("branch_code") or "").strip(),
        "address": display_location,
        "location": location,
    }


class SalaryComponent(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    amount: float = Field(default=0, ge=0, le=999999999.99)
    category: str = Field(default="earning", pattern="^(earning|deduction)$")
    calculation_type: str = Field(default="fixed", pattern="^(fixed|percentage)$")
    percentage: float = Field(default=0, ge=0, le=1000)
    included_in_monthly_salary: bool = False
    fixed_amount: bool = False

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def fixed_amount_must_use_amount_calculation(self):
        if self.fixed_amount and self.calculation_type != "fixed":
            raise ValueError("Fixed Amount components must use a fixed amount, not percentage calculation.")
        return self


class SalaryProfilePayload(BaseModel):
    employee_id: str = Field(min_length=1, max_length=100)
    monthly_salary: float = Field(gt=0, le=999999999.99)
    bank_name: str = Field(default="", max_length=120)
    bank_account_number: str = Field(default="", max_length=40)
    pan_card_number: str = Field(default="", max_length=10)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    effective_from: str = Field(min_length=10, max_length=10)
    components: list[SalaryComponent] = Field(default_factory=list, max_length=50)
    attendance_deduction_enabled: bool = True
    attendance_deduction_basis: str = Field(default="calendar_days", pattern="^(calendar_days|working_days|fixed_amount)$")
    attendance_deduction_amount_per_day: float = Field(default=0, ge=0, le=999999999.99)
    notes: str = Field(default="", max_length=1000)

    @field_validator("pan_card_number")
    @classmethod
    def validate_pan(cls, value: str) -> str:
        value = value.strip().upper()
        if value and not __import__("re").fullmatch(r"[A-Z]{5}[0-9]{4}[A-Z]", value):
            raise ValueError("PAN must use the format ABCDE1234F")
        return value

    @field_validator("bank_name")
    @classmethod
    def clean_bank_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("bank_account_number")
    @classmethod
    def clean_bank_account(cls, value: str) -> str:
        return value.strip()

    @field_validator("employee_id", "currency", "notes")
    @classmethod
    def trim_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("effective_from")
    @classmethod
    def validate_effective_date(cls, value: str) -> str:
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            raise ValueError("effective_from must use YYYY-MM-DD format")
        return value


def _employee_master_data(employee_id: str) -> dict:
    """Resolve current employee details from Employee Master and legacy collections.

    Employee Master stores department/designation in employee_details, linked to
    the login user through employee_details.user_id. The employees and users
    collections remain fallbacks for older records.
    """
    employee_id = str(employee_id or "").strip()
    if not employee_id:
        return {}

    user = users_collection.find_one({"employee_id": employee_id}) or {}
    employee = db["employees"].find_one({"employee_id": employee_id}) or {}

    details = {}
    if user.get("_id") is not None:
        details = db["employee_details"].find_one(
            {"user_id": str(user["_id"])}
        ) or {}

    # Employee Master details are the source of truth when populated.
    department = (
        details.get("department")
        or employee.get("department")
        or user.get("department")
        or ""
    )
    designation = (
        details.get("designation")
        or employee.get("designation")
        or user.get("designation")
        or ""
    )

    return {
        "employee_id": employee_id,
        "name": user.get("name") or employee.get("employee_name") or employee.get("name") or employee_id,
        "email": user.get("work_email") or user.get("email") or employee.get("email") or "",
        "department": department,
        "designation": designation,
        "status": employee.get("status")
        or ("Active" if user.get("is_active", True) else "Inactive"),
    }


def serialize_profile(record: dict) -> dict:
    # Keep salary values/history from the profile, but always expose current
    # Employee Master department and designation to the Payroll UI.
    employee = _employee_master_data(record.get("employee_id", ""))
    if employee:
        record["employee_name"] = employee.get("name") or record.get("employee_name", "")
        record["department"] = employee.get("department") or record.get("department", "")
        record["designation"] = employee.get("designation") or record.get("designation", "")
    record["id"] = str(record.pop("_id"))
    for field in ("created_at", "updated_at"):
        if isinstance(record.get(field), datetime):
            record[field] = record[field].isoformat()
    return record


@router.get("/employees")
def list_payroll_employees(
    authorization: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
):
    authenticated_user(authorization, x_user_id)
    # Include IDs from both login users and the employee master collection.
    # Department/designation are resolved through employee_details first.
    employee_ids = set()
    for item in users_collection.find({}, {"employee_id": 1}):
        employee_id = str(item.get("employee_id") or "").strip()
        if employee_id:
            employee_ids.add(employee_id)
    for item in db["employees"].find({}, {"employee_id": 1}):
        employee_id = str(item.get("employee_id") or "").strip()
        if employee_id:
            employee_ids.add(employee_id)

    combined = [_employee_master_data(employee_id) for employee_id in employee_ids]
    combined = [item for item in combined if item.get("employee_id")]
    return sorted(combined, key=lambda item: str(item.get("name") or "").casefold())


@router.get("/salary-profiles")
def list_salary_profiles(
    authorization: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
):
    authenticated_user(authorization, x_user_id)
    return [serialize_profile(row) for row in salaries_collection.find({}).sort("employee_name", 1)]


@router.put("/salary-profiles/{employee_id}")
def upsert_salary_profile(
    employee_id: str,
    payload: SalaryProfilePayload,
    authorization: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
):
    actor = authenticated_user(authorization, x_user_id)
    if employee_id.strip() != payload.employee_id:
        raise HTTPException(status_code=422, detail="Path employee_id must match payload employee_id")
    employee = _employee_master_data(payload.employee_id)
    if not employee.get("employee_id"):
        raise HTTPException(status_code=404, detail="Employee not found. Check the employee ID in Employee Master.")
    employee_name = employee.get("name") or payload.employee_id
    # Start from Decimal so empty component lists produce Decimal("0.00"),
    # rather than int(0), which does not support Decimal.quantize().
    def configured_amount(component):
        if component.calculation_type == "percentage":
            return (Decimal(str(payload.monthly_salary)) * Decimal(str(component.percentage)) / Decimal("100")).quantize(Decimal("0.01"))
        return Decimal(str(component.amount)).quantize(Decimal("0.01"))
    earnings = sum((configured_amount(c) for c in payload.components if c.category == "earning"), Decimal("0.00"))
    deductions = sum((configured_amount(c) for c in payload.components if c.category == "deduction"), Decimal("0.00"))
    now = datetime.now(timezone.utc)
    existing = salaries_collection.find_one({"employee_id": payload.employee_id})
    if existing:
        old_locked = {
            str(item.get("name", "")).strip().casefold(): item
            for item in existing.get("components", [])
            if item.get("fixed_amount")
        }
        incoming_names = {component.name.strip().casefold() for component in payload.components}
        for locked_name, old in old_locked.items():
            if locked_name not in incoming_names:
                raise HTTPException(
                    status_code=409,
                    detail=f'{old.get("name", "This component")} is locked as a fixed amount. Disable its Fixed Amount lock before removing it.'
                )
        for component in payload.components:
            old = old_locked.get(component.name.strip().casefold())
            if old and component.fixed_amount:
                changed_value = (
                    str(old.get("calculation_type", "fixed")) != component.calculation_type
                    or abs(float(old.get("amount", 0) or 0) - float(component.amount or 0)) > 0.0001
                    or abs(float(old.get("percentage", 0) or 0) - float(component.percentage or 0)) > 0.0001
                )
                if changed_value:
                    raise HTTPException(
                        status_code=409,
                        detail=f'{component.name} is locked as a fixed amount. Disable its Fixed Amount lock before changing it.'
                    )
    record = {
        "employee_id": payload.employee_id,
        "employee_name": employee_name,
        "department": employee.get("department") or "",
        "designation": employee.get("designation") or "",
        "currency": payload.currency.upper(),
        "monthly_salary": money(payload.monthly_salary, "monthly_salary"),
        "bank_name": payload.bank_name,
        "bank_account_number": payload.bank_account_number,
        "pan_card_number": payload.pan_card_number,
        "components": [c.model_dump() for c in payload.components],
        "total_earnings_components": float(earnings.quantize(Decimal("0.01"))),
        "total_deduction_components": float(deductions.quantize(Decimal("0.01"))),
        "effective_from": payload.effective_from,
        "attendance_deduction_enabled": payload.attendance_deduction_enabled,
        "attendance_deduction_basis": payload.attendance_deduction_basis,
        "attendance_deduction_amount_per_day": money(payload.attendance_deduction_amount_per_day, "attendance_deduction_amount_per_day"),
        "notes": payload.notes,
        "updated_at": now,
        "updated_by": str(actor.get("_id")),
    }
    if existing:
        # Keep a dated snapshot whenever a salary profile changes.
        history_entry = {k: existing.get(k) for k in ("employee_id", "employee_name", "monthly_salary", "bank_name", "bank_account_number", "pan_card_number", "currency", "components", "effective_from", "attendance_deduction_enabled", "attendance_deduction_basis", "attendance_deduction_amount_per_day", "notes", "updated_at", "updated_by", "department", "designation")}
        history_entry["archived_at"] = now
        salaries_collection.update_one({"_id": existing["_id"]}, {"$push": {"history": history_entry}, "$set": record})
        saved = salaries_collection.find_one({"_id": existing["_id"]})
    else:
        record["created_at"] = now
        record["created_by"] = str(actor.get("_id"))
        salaries_collection.insert_one(record)
        saved = salaries_collection.find_one({"employee_id": payload.employee_id})
    return serialize_profile(saved)


# Monthly payroll workflow, attendance review and payslip generation.
from calendar import monthrange
from fastapi import Body
from fastapi.responses import StreamingResponse
from io import BytesIO

payroll_collection = db["monthly_payroll_records"]
payslip_collection = db["payslip_records"]

def _attendance_summary_for_employee(employee_id: str, month: int, year: int, profile: dict | None = None) -> dict:
    """Summarize unique attendance/approved-leave days for a payroll period.

    Payable days are explicitly earned days (present + paid leave + holidays).
    Missing attendance records are not automatically treated as present or absent.
    """
    from datetime import date, timedelta

    days_in_month = monthrange(year, month)[1]
    start_date = date(year, month, 1)
    end_date = date(year, month, days_in_month)
    start, end = start_date.isoformat(), end_date.isoformat()

    attendance_collection = db["attendance"]
    leave_types_collection = db["leave_types"]
    leave_requests_collection = db["leave_requests"]

    records = list(attendance_collection.find({
        "employee_id": employee_id,
        "attendance_date": {"$gte": start, "$lte": end},
    }, {"status": 1, "leave_type_code": 1, "attendance_date": 1, "leave_day_part": 1, "medical_leave": 1}))

    leave_codes = {
        str(row.get("leave_type_code") or "").upper()
        for row in records
        if str(row.get("status", "")).casefold() == "leave"
    }
    leave_types = {
        str(item.get("code", "")).upper(): bool(item.get("is_paid", True))
        for item in leave_types_collection.find({"code": {"$in": list(leave_codes)}})
    }

    # Keep one payroll category per date to prevent duplicate attendance rows
    # from inflating paid days. Present/Holiday records take precedence over leave.
    day_status: dict[str, tuple[str, bool, float]] = {}
    for row in records:
        day = str(row.get("attendance_date") or "")
        if not day:
            continue
        status_value = str(row.get("status") or "").strip().casefold()
        if status_value == "present":
            day_status[day] = ("present", True, 1.0)
        elif status_value == "holiday":
            day_status[day] = ("holiday", True, 1.0)
        elif status_value == "absent":
            day_status.setdefault(day, ("absent", False, 1.0))
        elif status_value == "not applicable":
            # Set by HR Admin on an attendance request; always Loss of Pay.
            day_status[day] = ("not_applicable", False, 1.0)
        elif status_value == "weekly off":
            if day_status.get(day, ("absent",))[0] == "absent":
                day_status[day] = ("weekly_off", True, 1.0)
        elif status_value == "leave" and row.get("medical_leave"):
            # Monthly Medical Leave granted through an attendance request is paid.
            day_status[day] = ("medical_leave", True, 1.0)
        elif status_value == "leave":
            code = str(row.get("leave_type_code") or "").upper()
            is_paid = leave_types.get(code, code != "LOP")
            part = str(row.get("leave_day_part") or "").casefold()
            fraction = 0.5 if "half" in part else 1.0
            day_status[day] = ("paid_leave" if is_paid else "unpaid_leave", is_paid, fraction)

    # Include eligible holidays from the employee's assigned holiday calendar.
    # Existing explicit Present records still win, and optional holidays are not
    # assumed payable unless Attendance explicitly marks that date as Holiday.
    assigned_calendars = list(db["employee_holiday_calendars"].find(
        {"employee_id": employee_id, "is_active": {"$ne": False}},
        {"calendar_id": 1},
    ))
    calendar_ids = [str(item.get("calendar_id")) for item in assigned_calendars if item.get("calendar_id")]
    if calendar_ids:
        calendar_filter = {"calendar_id": {"$in": calendar_ids}}
        holiday_query = {
            **calendar_filter,
            "holiday_date": {
                "$gte": datetime.combine(start_date, datetime.min.time()),
                "$lt": datetime.combine(end_date + timedelta(days=1), datetime.min.time()),
            },
            "is_active": {"$ne": False},
            "is_optional": {"$ne": True},
        }
        for holiday_record in db["holidays"].find(holiday_query, {"holiday_date": 1}):
            holiday_date = holiday_record.get("holiday_date")
            if isinstance(holiday_date, datetime):
                holiday_key = holiday_date.date().isoformat()
            elif hasattr(holiday_date, "isoformat"):
                holiday_key = holiday_date.isoformat()[:10]
            else:
                holiday_key = str(holiday_date or "")[:10]
            if holiday_key and holiday_key not in day_status:
                day_status[holiday_key] = ("holiday", True, 1.0)
            elif holiday_key and day_status.get(holiday_key, (None,))[0] == "absent":
                day_status[holiday_key] = ("holiday", True, 1.0)

    # Published religion-based holidays for this employee follow the same rule:
    # they fill unrecorded or Absent dates and never replace Present or leave.
    from app.religion_holidays import published_religion_holidays
    religion_user = users_collection.find_one({"employee_id": employee_id})
    if religion_user:
        for holiday_day in published_religion_holidays(religion_user, start_date, end_date, include_optional=False):
            holiday_key = holiday_day.isoformat()
            if day_status.get(holiday_key, ("absent",))[0] == "absent":
                day_status[holiday_key] = ("holiday", True, 1.0)

    # Unrecorded HR-configured weekly-off days are payable, from the joining
    # date up to today; any explicit attendance record for the date wins.
    from app.attendance import company_today, company_weekly_off_weekdays
    weekly_off_weekdays = company_weekly_off_weekdays()
    if weekly_off_weekdays:
        from app import leave as leave_module
        employee_user = users_collection.find_one({"employee_id": employee_id})
        joining = leave_module._joining_date(employee_user) if employee_user else None
        current = max(start_date, joining) if joining else start_date
        last = min(end_date, company_today())
        while current <= last:
            if current.weekday() in weekly_off_weekdays and current.isoformat() not in day_status:
                day_status[current.isoformat()] = ("weekly_off", True, 1.0)
            current += timedelta(days=1)

    # Approved leave requests are the source of truth for approved leave. Use
    # them to fill dates not already represented by a higher-priority attendance
    # record (for example, a leave approval not yet synced into attendance).
    user_ids = [
        str(user.get("_id"))
        for user in users_collection.find({"employee_id": employee_id}, {"_id": 1})
    ]
    employee_master = db["employees"].find_one({"employee_id": employee_id}, {"email": 1, "work_email": 1}) or {}
    email = str(employee_master.get("work_email") or employee_master.get("email") or "").strip().casefold()
    if not user_ids and email:
        user_ids = [
            str(user.get("_id"))
            for user in users_collection.find(
                {"$or": [{"work_email": {"$regex": f"^{__import__('re').escape(email)}$", "$options": "i"}},
                         {"email": {"$regex": f"^{__import__('re').escape(email)}$", "$options": "i"}}]},
                {"_id": 1},
            )
        ]

    if user_ids:
        approved_leaves = leave_requests_collection.find({
            "user_id": {"$in": user_ids},
            "status": {"$regex": "^approved$", "$options": "i"},
            "from_date": {"$lte": end},
            "to_date": {"$gte": start},
        })
        for leave in approved_leaves:
            leave_type_id = str(leave.get("leave_type_id") or "")
            leave_type = None
            if ObjectId.is_valid(leave_type_id):
                leave_type = leave_types_collection.find_one({"_id": ObjectId(leave_type_id)}, {"code": 1, "is_paid": 1})
            is_paid = bool(leave_type.get("is_paid", True)) if leave_type else True
            day_part = str(leave.get("day_part") or "full_day").casefold()
            fraction = 0.5 if "half" in day_part else 1.0
            leave_dates = leave.get("leave_dates") or []
            if leave_dates:
                covered_dates = [
                    str(value)[:10] for value in leave_dates
                    if start <= str(value)[:10] <= end
                ]
            else:
                current = max(start_date, date.fromisoformat(str(leave.get("from_date"))[:10]))
                final = min(end_date, date.fromisoformat(str(leave.get("to_date"))[:10]))
                covered_dates = []
                while current <= final:
                    covered_dates.append(current.isoformat())
                    current += timedelta(days=1)
            for day in covered_dates:
                existing = day_status.get(day)
                if not existing or existing[0] in {"absent", "unpaid_leave", "paid_leave"}:
                    category = "paid_leave" if is_paid else "unpaid_leave"
                    day_status[day] = (category, is_paid, fraction)

    present = sum(value[2] for value in day_status.values() if value[0] == "present")
    paid_leave = sum(value[2] for value in day_status.values() if value[0] == "paid_leave")
    unpaid_leave = sum(value[2] for value in day_status.values() if value[0] == "unpaid_leave")
    absent = sum(value[2] for value in day_status.values() if value[0] == "absent")
    holiday = sum(value[2] for value in day_status.values() if value[0] == "holiday")
    weekly_off = sum(value[2] for value in day_status.values() if value[0] == "weekly_off")
    medical_leave = sum(value[2] for value in day_status.values() if value[0] == "medical_leave")
    payable_days = min(float(days_in_month), present + paid_leave + holiday + weekly_off + medical_leave)
    not_applicable = sum(value[2] for value in day_status.values() if value[0] == "not_applicable")
    lop = absent + unpaid_leave + not_applicable

    return {
        "employee_id": employee_id, "month": month, "year": year, "calendar_days": days_in_month,
        "present_days": round(present, 2), "absent_days": round(absent, 2),
        "paid_leave_days": round(paid_leave, 2), "unpaid_leave_days": round(unpaid_leave, 2),
        "holiday_days": round(holiday, 2),
        "weekly_off_days": round(weekly_off, 2), "medical_leave_days": round(medical_leave, 2),
        "not_applicable_days": round(not_applicable, 2),
        "working_days": round(present + absent + paid_leave + unpaid_leave + medical_leave, 2),
        "lop_days": round(lop, 2), "paid_days": round(payable_days, 2),
        "recorded_attendance_days": len(day_status),
        "review": "Payable days are present days + approved paid leave + eligible holidays + weekly offs + medical leave. Unpaid leave, absences and Not Applicable days are Loss of Pay.",
    }


@router.get("/attendance-summary")
def attendance_summary(month: int, year: int,
                       authorization: str | None = Header(default=None),
                       x_user_id: str | None = Header(default=None)):
    authenticated_user(authorization, x_user_id)
    if month < 1 or month > 12 or year < 2000 or year > 2100:
        raise HTTPException(status_code=422, detail="Invalid payroll month or year.")
    period = _period_key(month, year)
    summaries = []
    for stored in salaries_collection.find({}):
        profile = _salary_version_for_period(stored, period)
        if not profile:
            continue
        info = _attendance_summary_for_employee(str(stored.get("employee_id", "")), month, year, profile)
        employee = _employee_master_data(str(stored.get("employee_id", "")))
        info["employee_name"] = employee.get("name") or profile.get("employee_name", stored.get("employee_id", ""))
        info["department"] = employee.get("department") or profile.get("department", stored.get("department", ""))
        info["designation"] = employee.get("designation") or profile.get("designation", stored.get("designation", ""))
        summaries.append(info)
    return summaries


class PayrollRunRequest(BaseModel):
    month: int = Field(ge=1, le=12)
    year: int = Field(ge=2000, le=2100)
    employee_ids: list[str] | None = None
    paid_days: dict[str, float] = Field(default_factory=dict)
    lop_days: dict[str, float] = Field(default_factory=dict)
    overtime_amounts: dict[str, float] = Field(default_factory=dict)


def _payroll_auth(authorization, x_user_id):
    return authenticated_user(authorization, x_user_id)


def _period_key(month: int, year: int) -> str:
    return f"{year:04d}-{month:02d}"


def _salary_version_for_period(profile: dict, period: str) -> dict | None:
    """Return the latest salary version effective on the first day of a period."""
    versions = list(profile.get("history", []))
    versions.append({key: value for key, value in profile.items() if key != "history"})
    eligible = [v for v in versions if str(v.get("effective_from") or "")[:7] <= period]
    if not eligible:
        return None
    return max(
        eligible,
        key=lambda v: (
            str(v.get("effective_from") or ""),
            str(v.get("updated_at") or v.get("archived_at") or ""),
        ),
    )


def _money_decimal(value):
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))

@router.get("/records")
def list_payroll_records(month: int | None = None, year: int | None = None,
                         authorization: str | None = Header(default=None),
                         x_user_id: str | None = Header(default=None)):
    _payroll_auth(authorization, x_user_id)
    query = {}
    if month and year:
        query["period"] = _period_key(month, year)
    rows = list(payroll_collection.find(query).sort([("year", -1), ("month", -1), ("employee_name", 1)]))
    for row in rows:
        row["id"] = str(row.pop("_id"))
        for k in ("created_at", "updated_at"):
            if isinstance(row.get(k), datetime): row[k] = row[k].isoformat()
    return rows


@router.post("/calculate")
def calculate_monthly_payroll(payload: PayrollRunRequest,
                              authorization: str | None = Header(default=None),
                              x_user_id: str | None = Header(default=None)):
    actor = _payroll_auth(authorization, x_user_id)
    period = _period_key(payload.month, payload.year)
    stored_profiles = list(salaries_collection.find({}))
    profiles = []
    for stored in stored_profiles:
        version = _salary_version_for_period(stored, period)
        if version:
            profiles.append({**stored, **version, "history": stored.get("history", [])})
    if not payload.employee_ids:
        raise HTTPException(status_code=422, detail="Select at least one employee before calculating payroll.")
    wanted = {str(item).strip() for item in payload.employee_ids if str(item).strip()}
    if not wanted:
        raise HTTPException(status_code=422, detail="Select at least one employee before calculating payroll.")
    profiles = [p for p in profiles if p.get("employee_id") in wanted]
    if not profiles:
        raise HTTPException(status_code=422, detail="No effective salary profiles found for this period.")
    days_in_month = monthrange(payload.year, payload.month)[1]
    results, errors = [], []
    for profile in profiles:
        emp_id = profile["employee_id"]
        # Do not read attendance while an HR attendance-request response is
        # being written for this employee and period.
        if db["attendance_requests"].find_one({"employee_id": emp_id, "status": "processing", "periods": period}, {"_id": 1}):
            errors.append({"employee_id": emp_id, "employee_name": profile.get("employee_name", emp_id), "error": "An attendance request for this period is being applied. Retry payroll calculation in a moment."})
            continue
        attendance = _attendance_summary_for_employee(emp_id, payload.month, payload.year, profile)
        # HR may confirm/adjust the automatically fetched values after reviewing
        # the attendance breakdown in the UI. Missing values use the database summary.
        default_paid = attendance["paid_days"]
        default_lop = attendance["lop_days"]
        paid_days = Decimal(str(payload.paid_days.get(emp_id, default_paid)))
        lop_days = Decimal(str(payload.lop_days.get(emp_id, default_lop)))
        if paid_days < 0 or paid_days > days_in_month or lop_days < 0 or lop_days > days_in_month:
            errors.append({"employee_id": emp_id, "employee_name": profile.get("employee_name", emp_id), "error": "Paid/LOP days are outside the month range."})
            continue
        base_monthly = _money_decimal(profile.get("monthly_salary"))
        # Prorate monthly salary against calendar days using payable days.
        # This replaces the previous "full salary minus an extra LOP deduction"
        # calculation, which could disagree with the requested payable-day rule.
        attendance_proration = (
            Decimal(str(paid_days)) / Decimal(days_in_month)
            if profile.get("attendance_deduction_enabled", True)
            else Decimal("1")
        )
        attendance_proration = max(Decimal("0"), min(Decimal("1"), attendance_proration))
        prorated_base = (base_monthly * attendance_proration).quantize(Decimal("0.01"))
        earnings, deductions = [], []
        components = profile.get("components", [])
        has_salary_breakdown = any(
            c.get("category") == "earning"
            and c.get("included_in_monthly_salary", False)
            and not __import__("re").search(r"\b(?:ot|overtime|over\s*time)\b", str(c.get("name", "")), __import__("re").IGNORECASE)
            for c in components
        )
        # When HR defines Basic/HRA/etc. as a breakdown of monthly salary, use
        # those included components instead of adding them on top of salary.
        gross = Decimal("0.00") if has_salary_breakdown else prorated_base
        for component in components:
            # OT is entered per employee and payroll month, not in the permanent salary profile.
            if __import__("re").search(r"\b(?:ot|overtime|over\s*time)\b", str(component.get("name", "")), __import__("re").IGNORECASE):
                continue
            if component.get("calculation_type") == "percentage":
                monthly_amount = (_money_decimal(base_monthly) * Decimal(str(component.get("percentage", 0))) / Decimal("100")).quantize(Decimal("0.01"))
            else:
                monthly_amount = _money_decimal(component.get("amount"))
            # Explicitly fixed components retain their configured monthly value;
            # ordinary components follow attendance-based salary proration.
            amount = (
                monthly_amount
                if component.get("fixed_amount", False)
                else (monthly_amount * attendance_proration).quantize(Decimal("0.01"))
            )
            amount = amount.quantize(Decimal("0.01"))
            item = {"name": component.get("name", "Component"), "amount": float(amount)}
            if component.get("category") == "earning":
                earnings.append(item)
                if has_salary_breakdown and component.get("included_in_monthly_salary", False):
                    gross += amount
                elif not component.get("included_in_monthly_salary", False):
                    gross += amount
            else:
                deductions.append(item)
        raw_overtime = payload.overtime_amounts.get(emp_id, 0)
        try:
            overtime_amount = Decimal(str(raw_overtime)).quantize(Decimal("0.01"))
        except (InvalidOperation, TypeError, ValueError):
            errors.append({"employee_id": emp_id, "employee_name": profile.get("employee_name", emp_id), "error": "Overtime amount must be a valid number."})
            continue
        if not overtime_amount.is_finite() or overtime_amount < 0 or overtime_amount > Decimal("999999999.99"):
            errors.append({"employee_id": emp_id, "employee_name": profile.get("employee_name", emp_id), "error": "Overtime amount must be between 0 and 999999999.99."})
            continue
        if overtime_amount > 0:
            earnings.append({"name": "Overtime (OT)", "amount": float(overtime_amount)})
            gross += overtime_amount
        total_deductions = sum((_money_decimal(x["amount"]) for x in deductions), Decimal(0))
        net = max(Decimal(0), gross - total_deductions).quantize(Decimal("0.01"))
        existing_payroll = payroll_collection.find_one({"employee_id": emp_id, "period": period})
        if existing_payroll and existing_payroll.get("status") in {"Calculated", "Approved", "Processed", "Paid"}:
            errors.append({"employee_id": emp_id, "employee_name": profile.get("employee_name", emp_id), "error": f"Payroll is already {existing_payroll.get('status')}; disapprove it before recalculating."})
            continue
        now = datetime.now(timezone.utc)
        employee_info = _employee_master_data(emp_id)
        record = {
            "employee_id": emp_id, "employee_name": employee_info.get("name") or profile.get("employee_name", emp_id),
            "department": employee_info.get("department") or profile.get("department", ""),
            "designation": employee_info.get("designation") or profile.get("designation", ""),
            "month": payload.month, "year": payload.year, "period": period,
            "currency": profile.get("currency", "INR"), "paid_days": float(paid_days), "lop_days": float(lop_days),
            "calendar_days": days_in_month, "working_days": attendance["working_days"],
            "recorded_attendance_days": attendance["recorded_attendance_days"],
            "present_days": attendance["present_days"], "paid_leave_days": attendance["paid_leave_days"],
            "unpaid_leave_days": attendance["unpaid_leave_days"], "absent_days": attendance["absent_days"],
            "holiday_days": attendance["holiday_days"], "weekly_off_days": attendance["weekly_off_days"],
            "medical_leave_days": attendance["medical_leave_days"], "not_applicable_days": attendance["not_applicable_days"],
            "attendance_review": attendance["review"],
            "monthly_salary": float(base_monthly), "bank_name": profile.get("bank_name", ""), "bank_account_number": profile.get("bank_account_number", ""), "pan_card_number": profile.get("pan_card_number", ""), "salary_per_calendar_day": float((base_monthly / Decimal(days_in_month)).quantize(Decimal("0.01"))),
            "attendance_proration": float(attendance_proration), "overtime_amount": float(overtime_amount), "earnings": earnings, "deductions": deductions, "gross_earnings": float(gross),
            "total_deductions": float(total_deductions), "net_salary": float(net),
            "status": "Calculated", "salary_snapshot": {k: profile.get(k) for k in ("monthly_salary", "bank_name", "bank_account_number", "pan_card_number", "currency", "components", "effective_from")},
            "company_snapshot": _company_snapshot(emp_id),
            "updated_at": now, "updated_by": str(actor.get("_id")),
        }
        payroll_collection.update_one({"employee_id": emp_id, "period": period, "status": {"$nin": ["Approved", "Processed", "Paid"]}}, {"$set": record}, upsert=True)
        saved = payroll_collection.find_one({"employee_id": emp_id, "period": period})
        saved["id"] = str(saved.pop("_id"))
        saved["updated_at"] = now.isoformat()
        results.append(saved)
    return {"period": period, "calculated": len(results), "errors": errors, "records": results}


class PayrollActionRequest(BaseModel):
    action: str = Field(pattern="^(approve|disapprove|process|paid)$")
    payment_reference: str = Field(default="", max_length=200)


@router.post("/records/{record_id}/action")
def payroll_action(record_id: str, payload: PayrollActionRequest,
                   authorization: str | None = Header(default=None),
                   x_user_id: str | None = Header(default=None)):
    actor = _payroll_auth(authorization, x_user_id)
    if not ObjectId.is_valid(record_id): raise HTTPException(status_code=404, detail="Payroll record not found")
    row = payroll_collection.find_one({"_id": ObjectId(record_id)})
    if not row: raise HTTPException(status_code=404, detail="Payroll record not found")
    if payload.action == "disapprove":
        if row.get("status") != "Calculated":
            raise HTTPException(status_code=409, detail=f"Cannot disapprove a payroll record in {row.get('status')} status; expected Calculated.")
        audit_collection = db["payroll_review_audit"]
        audit_collection.insert_one({
            "payroll_record_id": str(row["_id"]),
            "employee_id": row.get("employee_id"),
            "period": row.get("period"),
            "action": "disapproved",
            "previous_status": row.get("status"),
            "employee_name": row.get("employee_name"),
            "gross_earnings": row.get("gross_earnings", 0),
            "total_deductions": row.get("total_deductions", 0),
            "net_salary": row.get("net_salary", 0),
            "reviewed_by": str(actor.get("_id")),
            "reviewed_at": datetime.now(timezone.utc),
        })
        payroll_collection.delete_one({"_id": row["_id"], "status": "Calculated"})
        return {"id": record_id, "employee_id": row.get("employee_id"), "period": row.get("period"), "status": "Disapproved", "deleted": True}
    transitions = {"approve": ("Calculated", "Approved"), "process": ("Approved", "Processed"), "paid": ("Processed", "Paid")}
    expected, target = transitions[payload.action]
    if row.get("status") != expected:
        raise HTTPException(status_code=409, detail=f"Cannot {payload.action} a payroll record in {row.get('status')} status; expected {expected}.")
    update = {"status": target, "updated_at": datetime.now(timezone.utc), "updated_by": str(actor.get("_id"))}
    if payload.action == "paid":
        if not payload.payment_reference.strip(): raise HTTPException(status_code=422, detail="Payment reference is required to mark payroll as paid.")
        update["payment_reference"] = payload.payment_reference.strip()
    # An HR attendance-request decision made after calculation flags the draft
    # as outdated; it must be disapproved and recalculated before approval.
    outdated_detail = "Attendance changed after this payroll was calculated. Disapprove and recalculate it before approving."
    if payload.action == "approve" and row.get("attendance_outdated"):
        raise HTTPException(status_code=409, detail=outdated_detail)
    query = {"_id": row["_id"], "status": expected}
    if payload.action == "approve":
        query["attendance_outdated"] = {"$ne": True}
    changed = payroll_collection.update_one(query, {"$set": update})
    if changed.matched_count != 1:
        if payload.action == "approve" and (payroll_collection.find_one({"_id": row["_id"]}) or {}).get("attendance_outdated"):
            raise HTTPException(status_code=409, detail=outdated_detail)
        raise HTTPException(status_code=409, detail="Payroll status changed while this action was being submitted. Refresh and try again.")
    if payload.action == "paid":
        payslip_collection.update_one(
            {"employee_id": row["employee_id"], "period": row["period"]},
            {"$set": {"employee_id": row["employee_id"], "period": row["period"], "payroll_record_id": str(row["_id"]), "status": "Available", "created_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
    updated = payroll_collection.find_one({"_id": row["_id"]})
    updated["id"] = str(updated.pop("_id")); updated["updated_at"] = updated["updated_at"].isoformat()
    return updated


@router.get("/payslips")
def list_payslips(month: int | None = None, year: int | None = None,
                  authorization: str | None = Header(default=None),
                  x_user_id: str | None = Header(default=None)):
    _payroll_auth(authorization, x_user_id)
    query = {}
    if month and year: query["period"] = _period_key(month, year)
    rows = []
    for slip in payslip_collection.find(query).sort([("period", -1), ("employee_id", 1)]):
        record = payroll_collection.find_one({"employee_id": slip["employee_id"], "period": slip["period"], "status": "Paid"})
        if not record: continue
        rows.append({"id": str(slip["_id"]), "employee_id": record["employee_id"], "employee_name": record["employee_name"], "department": record.get("department", ""), "designation": record.get("designation", ""), "period": record["period"], "net_salary": record["net_salary"], "gross_earnings": record.get("gross_earnings", 0), "total_deductions": record.get("total_deductions", 0), "currency": record.get("currency", "INR"), "paid_days": record.get("paid_days", 0), "lop_days": record.get("lop_days", 0), "present_days": record.get("present_days", 0), "paid_leave_days": record.get("paid_leave_days", 0), "unpaid_leave_days": record.get("unpaid_leave_days", 0), "absent_days": record.get("absent_days", 0), "status": record.get("status", "Paid"), "payslip_status": slip.get("status", "Available"), "payroll_record_id": str(record["_id"])})
    return rows

@router.get("/payslips/{record_id}")
def get_payslip(record_id: str, authorization: str | None = Header(default=None),
                x_user_id: str | None = Header(default=None)):
    """HR Admin: on-screen payslip for a finalized (Paid) payroll record."""
    authenticated_user(authorization, x_user_id)
    if not ObjectId.is_valid(record_id):
        raise HTTPException(status_code=404, detail="Payslip not found")
    row = payroll_collection.find_one({"_id": ObjectId(record_id), "status": "Paid"})
    if not row:
        raise HTTPException(status_code=404, detail="Finalized payslip not found")
    return payslip_view_data(row)


@router.get("/payslips/{record_id}/pdf")
def download_payslip(record_id: str, inline: bool = False,
                     authorization: str | None = Header(default=None),
                     x_user_id: str | None = Header(default=None)):
    authenticated_user(authorization, x_user_id)
    if not ObjectId.is_valid(record_id):
        raise HTTPException(status_code=404, detail="Payslip not found")
    row = payroll_collection.find_one({"_id": ObjectId(record_id), "status": "Paid"})
    if not row:
        raise HTTPException(status_code=404, detail="Finalized payslip not found")
    return _payslip_pdf_response(row, inline)


def _own_paid_payslip(user: dict, record_id: str) -> dict:
    employee_id = _employee_identifier(user)
    if not ObjectId.is_valid(record_id):
        raise HTTPException(status_code=404, detail="Payslip not found")
    row = payroll_collection.find_one({"_id": ObjectId(record_id), "employee_id": employee_id, "status": "Paid"})
    if not row:
        raise HTTPException(status_code=404, detail="Payslip not found")
    return row


@router.get("/my-payslips")
def list_my_payslips(month: int | None = None, year: int | None = None,
                     authorization: str | None = Header(default=None),
                     x_user_id: str | None = Header(default=None)):
    user = current_user(authorization, x_user_id)
    employee_id = _employee_identifier(user)
    query = {"employee_id": employee_id, "status": "Paid"}
    if month and year:
        query["period"] = _period_key(month, year)
    rows = []
    for row in payroll_collection.find(query).sort("period", -1):
        rows.append({"id": str(row["_id"]), "employee_id": employee_id,
            "employee_name": row.get("employee_name") or user.get("name", ""),
            "department": row.get("department", ""), "designation": row.get("designation", ""),
            "period": row.get("period", ""), "month": row.get("month"), "year": row.get("year"),
            "net_salary": row.get("net_salary", 0), "currency": row.get("currency", "INR"),
            "status": row.get("status"), "paid_days": row.get("paid_days", 0), "lop_days": row.get("lop_days", 0),
            "working_days": row.get("working_days", 0), "present_days": row.get("present_days", 0), "paid_leave_days": row.get("paid_leave_days", 0),
            "unpaid_leave_days": row.get("unpaid_leave_days", 0), "absent_days": row.get("absent_days", 0),
            "gross_earnings": row.get("gross_earnings", 0), "total_deductions": row.get("total_deductions", 0)})
    return rows


@router.get("/my-payslips/{record_id}")
def get_my_payslip(record_id: str, authorization: str | None = Header(default=None),
                   x_user_id: str | None = Header(default=None)):
    user = current_user(authorization, x_user_id)
    row = _own_paid_payslip(user, record_id)
    allowed_fields = (
        "employee_id", "employee_name", "department", "designation", "period", "month", "year",
        "currency", "paid_days", "lop_days", "working_days", "present_days", "paid_leave_days",
        "unpaid_leave_days", "absent_days", "holiday_days", "weekly_off_days", "medical_leave_days", "not_applicable_days", "earnings", "deductions",
        "gross_earnings", "total_deductions", "net_salary", "payment_reference", "updated_at",
    )
    result = {key: row.get(key) for key in allowed_fields}
    result["id"] = str(row["_id"])
    for key, value in list(result.items()):
        if isinstance(value, datetime): result[key] = value.isoformat()
    # Same on-screen view model as the HR endpoint, scoped to the caller's own Paid record.
    return {**result, **payslip_view_data(row)}


@router.get("/my-payslips/{record_id}/pdf")
def download_my_payslip(record_id: str, inline: bool = False,
                        authorization: str | None = Header(default=None),
                        x_user_id: str | None = Header(default=None)):
    user = current_user(authorization, x_user_id)
    return _payslip_pdf_response(_own_paid_payslip(user, record_id), inline)


def ensure_payroll_collections():
    salaries_collection.create_index("employee_id", unique=True, name="employee_salary_profile_employee_unique")
    salaries_collection.create_index([("employee_name", 1)], name="employee_salary_profile_name_idx")
    payroll_collection.create_index([("employee_id", 1), ("period", 1)], unique=True, name="monthly_payroll_employee_period_unique")
    payroll_collection.create_index([("period", 1), ("status", 1)], name="monthly_payroll_period_status_idx")
    payslip_collection.create_index([("employee_id", 1), ("period", 1)], unique=True, name="payslip_employee_period_unique")


@router.get("/salary-profiles/{employee_id}/history")
def salary_profile_history(employee_id: str, authorization: str | None = Header(default=None), x_user_id: str | None = Header(default=None)):
    _payroll_auth(authorization, x_user_id)
    profile = salaries_collection.find_one({"employee_id": employee_id}, {"history": 1, "employee_id": 1, "employee_name": 1, "effective_from": 1, "monthly_salary": 1, "bank_name": 1, "bank_account_number": 1, "pan_card_number": 1, "currency": 1, "components": 1, "updated_at": 1})
    if not profile: raise HTTPException(status_code=404, detail="Salary profile not found")
    entries = list(profile.get("history", [])) + [{k: profile.get(k) for k in ("employee_id", "employee_name", "effective_from", "monthly_salary", "bank_name", "bank_account_number", "pan_card_number", "currency", "components", "updated_at")} ]
    for item in entries:
        for key in ("archived_at", "updated_at"):
            if isinstance(item.get(key), datetime): item[key] = item[key].isoformat()
    return entries
