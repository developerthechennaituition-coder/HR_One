# HRMS – Human Resource Management System

HRMS is a web-based Human Resource Management System with a **React** single-page frontend and a **Python FastAPI** backend backed by **MongoDB**. It covers account and organization administration, employee records, attendance (manual, ESSL biometric device/file import, and face-verified work-from-home), leave and permission workflows, notifications, holiday calendars, and payroll with PDF payslips.

This document describes the system as implemented in the repository. For frontend-only details (pages, components, build configuration) see [`frontend/README.md`](frontend/README.md).

## Contents

- [Features and modules](#features-and-modules)
- [Architecture](#architecture)
- [Technology stack](#technology-stack)
- [Repository structure](#repository-structure)
- [Prerequisites](#prerequisites)
- [Installation and execution](#installation-and-execution)
- [Configuration](#configuration)
- [Roles and access](#roles-and-access)
- [Authentication and sessions](#authentication-and-sessions)
- [Database](#database)
- [API reference](#api-reference)
- [Business rules](#business-rules)
- [Scripts and tests](#scripts-and-tests)
- [Deployment notes](#deployment-notes)
- [Known limitations and security notes](#known-limitations-and-security-notes)
- [Troubleshooting](#troubleshooting)

## Features and modules

| Module | What it provides | Main backend file(s) |
|---|---|---|
| Authentication and accounts | Login with work email or employee ID, email activation links, change password, OTP-based forgot/reset password, login lockout, user management | `core_routes.py`, `activation.py`, `password_reset.py`, `security.py` |
| Organization setup | Companies / legal entities / branches, business units, departments, teams, designations, organization hierarchy | `core_routes.py` |
| Holiday calendars | Calendars and holidays with Draft/Published status, audit history, publish action, religion-based calendars, per-employee calendar assignment, "my holidays", automatic reminder scheduler | `core_routes.py`, `religion_holidays.py` |
| Employee Master | Employee records and extended employee details, including scheduled working hours and working-schedule history | `core_routes.py`, `employee_details.py`, `working_details.py` |
| Reporting hierarchy | Reporting Manager / HOD / HR Admin approver assignment per employee, team-member list and team attendance | `reporting_manager.py` |
| Attendance | Daily attendance management, monthly and per-day views, remarks, half-day calculation, payroll locks, historical modification, weekly-off generation, Excel export | `attendance.py`, `attendance_calculations.py`, `attendance_export.py`, `weekly_off_planner.py` |
| Attendance requests | Employees request corrections for past dates; HR Admin approves or rejects each date | `attendance_requests.py` |
| ESSL biometric integration | Configurable external API sync, `.csv`/`.xls`/`.xlsx` file preview and import, employee ID mapping, sync history, punch issues | `essl.py` |
| Work From Home (WFH) | Face verification with liveness challenge, browser location check against approved locations, check-in/out, breaks, email/QR fallback | `attendance.py`, `wfh_biometrics.py` |
| Leave and permissions | Leave types, policies, balances, multi-stage approval, cancellation workflow, short-duration permissions, dashboard, reports (JSON/CSV), leave notifications | `leave.py` |
| Notifications | Targeted notifications (all, roles, teams, departments, users) with validity period, hold/resume, view/acknowledge tracking, starring | `notifications.py` |
| Payroll | Salary profiles with components, attendance-based monthly calculation, approval workflow, payslip generation and PDF download | `payroll.py`, `payslip_pdf.py` |

## Architecture

```text
┌──────────────────────────┐   HTTP/JSON (Bearer token)   ┌───────────────────────────┐
│  React SPA (Vite)        │ ───────────────────────────► │  FastAPI backend          │
│  frontend/               │   /api/...                   │  backend/app/             │
│  - hash-based navigation │ ◄─────────────────────────── │  - routers per module     │
│  - Bootstrap 5 UI        │                              │  - in-memory sessions     │
└────────────┬─────────────┘                              │  - background scheduler   │
             │ (dev only) Vite proxies /api               └──────┬──────────┬─────────┘
             └─────────────────────────────────────────────────► │          │
                                                                  ▼          ▼
                                                        ┌──────────────┐  ┌───────────────────────┐
                                                        │   MongoDB    │  │ External services     │
                                                        │ (PyMongo)    │  │ SMTP (email)          │
                                                        └──────────────┘  │ ESSL device API (opt.)│
                                                                          └───────────────────────┘
```

- The frontend is a client-rendered SPA. In development, Vite proxies `/api` to the backend, so the browser only talks to the Vite origin. In production the frontend is built to static files and calls the backend URL set at build time (`VITE_API_BASE_URL`).
- The backend is one FastAPI application (`app.main:app`) that registers a router per module. On startup it creates collections, validators and indexes idempotently and starts a background thread for holiday-calendar reminders.
- Business rules for attendance durations and half-day classification live in `attendance_calculations.py`, which has no FastAPI or MongoDB imports and is unit-tested in isolation. Weekly-off planning rules are likewise isolated in `weekly_off_planner.py`.
- Face matching and liveness for WFH run locally (no cloud calls) in `wfh_biometrics.py`.
- Excel rendering for attendance exports is separated into `attendance_export.py`; payslip PDF generation is in `payslip_pdf.py` (ReportLab).

## Technology stack

### Frontend

React 19, Vite 8, JavaScript (JSX), Bootstrap 5 and Bootstrap Icons, `libphonenumber-js`, `qrcode.react`, `pdfjs-dist`, Oxlint. Details: [`frontend/README.md`](frontend/README.md).

### Backend

| Area | Technology |
|---|---|
| Language | Python 3.10+ (the code uses `X \| None` type syntax at runtime) |
| Web framework | FastAPI `0.141.1`, Starlette `1.6.0`, Pydantic `2.13.5`, Uvicorn `0.53.0` |
| Database driver | PyMongo `4.18.0` (MongoDB) |
| Configuration | `python-dotenv` |
| Cryptography | `cryptography` `45.0.7` (Fernet encryption for ESSL credentials and WFH face templates) |
| Passwords | PBKDF2-HMAC-SHA256 (120,000 iterations, per-password salt) via the standard library |
| Email | Standard-library `smtplib` |
| Excel | `openpyxl` (export), `openpyxl` + `xlrd` (ESSL file import of `.xlsx` / legacy `.xls`) |
| PDF | `reportlab` (payslips), bundled Noto Sans fonts |
| File uploads | `python-multipart` |
| WFH biometrics (optional install) | `face-recognition` `1.3.0`, `mediapipe` `0.10.21` |

Pinned versions come from `backend/requirements.txt` and `backend/requirements-wfh.txt`.

## Repository structure

The project root contains the `frontend/` and `backend/` directories.

```text
<project-root>/
├── README.md                      # this file
├── certs/                         # optional: localhost-key.pem + localhost.pem for HTTPS dev (not part of the packaged sources)
├── frontend/                      # React SPA (see frontend/README.md)
│   ├── package.json
│   ├── vite.config.js
│   ├── index.html
│   ├── public/
│   └── src/
│       ├── App.jsx, main.jsx, auth.js, logout.js
│       ├── config/api.js
│       ├── components/            # layout, sidebar, header, attendance, notifications, payslip viewer, ESSL admin
│       ├── pages/                 # route-level screens
│       └── services/              # shared API/helper modules
└── backend/
    ├── requirements.txt           # core dependencies
    ├── requirements-wfh.txt       # WFH face-verification dependencies (+ file-import libraries)
    ├── .env.example               # environment template
    ├── app/
    │   ├── main.py                # FastAPI app, CORS, organization-route middleware, router registration
    │   ├── core_routes.py         # auth, users, organization, holidays, employees, startup tasks, /health
    │   ├── db/mongodb.py          # Mongo client, schema validators, indexes, leave collection setup
    │   ├── session.py             # in-memory session store (token -> user id)
    │   ├── security.py            # password hashing
    │   ├── activation.py          # activation tokens and activation email
    │   ├── password_reset.py      # OTP password reset
    │   ├── employee_details.py    # extended employee details
    │   ├── working_details.py     # scheduled working hours + history
    │   ├── reporting_manager.py   # approver assignment and team views
    │   ├── attendance.py          # attendance, historical edits, weekly off, WFH, export endpoints
    │   ├── attendance_calculations.py  # pure duration / half-day rules
    │   ├── attendance_export.py   # Excel workbook rendering
    │   ├── attendance_requests.py # attendance correction requests
    │   ├── weekly_off_planner.py  # pure weekly-off planning rules
    │   ├── essl.py                # ESSL API sync and file import
    │   ├── wfh_biometrics.py      # local face matching + liveness
    │   ├── leave.py               # leave, permissions, approvals, reports
    │   ├── religion_holidays.py   # religion-based holiday calendar lookups
    │   ├── notifications.py       # notifications
    │   ├── payroll.py             # salary profiles, payroll, payslips
    │   └── payslip_pdf.py         # payslip PDF generation
    ├── assets/
    │   ├── fonts/                 # NotoSans-Regular.ttf, NotoSans-Bold.ttf
    │   └── images/                # company logo used on payslips
    ├── scripts/
    │   ├── recalculate_half_day_attendance.py
    │   └── check_wfh_mongodb.py
    └── tests/                     # 23 test modules
```

## Prerequisites

- **Python 3.10 or newer** and `pip`.
- **Node.js and npm** compatible with Vite 8.
- **MongoDB** reachable from the backend (local or hosted), with a connection URL.
- **SMTP account** (needed for account activation emails, password-reset OTP emails and WFH email fallback).
- **For WFH face verification only:** the packages in `requirements-wfh.txt`. `face-recognition` depends on `dlib`, which commonly requires a C++ build toolchain and CMake on platforms without a prebuilt wheel; check wheel availability of `mediapipe` for your Python version.

## Installation and execution

### 1. Backend

```bash
cd backend
python -m venv .venv
# Linux / macOS
source .venv/bin/activate
# Windows PowerShell
# .venv\Scripts\Activate.ps1

pip install -r requirements.txt
# Optional, only if you will use WFH face verification:
pip install -r requirements-wfh.txt
```

Create the environment file and fill in the values (see [Configuration](#configuration)):

```bash
cp .env.example .env        # Windows: copy .env.example .env
```

Run from the `backend/` directory (the `.env` file is loaded from the current working directory):

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- Health check: `GET http://127.0.0.1:8000/health`
- Interactive API docs (FastAPI defaults): `http://127.0.0.1:8000/docs`

The core application starts without the WFH packages; only the WFH face endpoints require them and report a configuration error if they are missing.

On startup the backend creates/updates collections, validators and indexes, seeds the `HR Admin` role, seeds the default leave types (Casual Leave `CL`, Medical Leave `ML`, Earned Leave `EL`, Loss of Pay `LOP`), and starts the holiday-reminder scheduler. The MongoDB connection is created at import time, so `MONGODB_URL` and `MONGODB_DATABASE` must be set before starting.

### 2. First administrator account

In development (`HRMS_ENV=development`, which is also the default when the variable is unset), the backend can create or update a sample login at startup when both `DEMO_USER_EMAIL` and `DEMO_USER_PASSWORD` are set in `.env`. `DEMO_USER_NAME`, `DEMO_USER_MOBILE` and `DEMO_USER_ROLE` (default `HR Admin`) are optional. Use this account to sign in and create other users from **User Management**. The sample-login seeding is skipped when `HRMS_ENV` is anything other than `development`.

### 3. Frontend

```bash
cd frontend
npm ci
npm run dev
```

Open `http://localhost:5173`. In development the Vite server proxies `/api` to `http://127.0.0.1:8000`; set `VITE_BACKEND_PROXY_TARGET` if the backend is elsewhere. Full frontend configuration: [`frontend/README.md`](frontend/README.md#configuration).

### 4. Production build of the frontend

```bash
cd frontend
npm ci
npm run build       # static output in frontend/dist/
```

## Configuration

### Backend environment variables

Defined in `backend/.env` (template: `backend/.env.example`). Variables marked *code default* are read by the code but are not listed in `.env.example`.

| Variable | Purpose | Default / notes |
|---|---|---|
| `MONGODB_URL` | MongoDB connection string | Required |
| `MONGODB_DATABASE` | Database name | Required (`hrms_db` in the template) |
| `ORG_TIMEZONE` | Timezone for holiday validation and reminder schedule | `Asia/Kolkata` |
| `COMPANY_TIMEZONE` | Timezone for attendance and ESSL date logic (*code default*) | `Asia/Kolkata` |
| `COMPANY_START_TIME` | Company start time used by attendance logic (*code default*) | `09:00` |
| `HRMS_ENV` | `development` enables the sample-login seeding | `development` when unset |
| `DEMO_USER_NAME`, `DEMO_USER_EMAIL`, `DEMO_USER_MOBILE`, `DEMO_USER_ROLE`, `DEMO_USER_PASSWORD` | Development sample login | Commented out in the template; email and password are required for seeding |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM` | Outgoing email | Template port `587`; `SMTP_FROM` falls back to `SMTP_USERNAME` |
| `FRONTEND_URL` | Base URL used in emailed links (activation, WFH fallback) | `http://localhost:5173` |
| `CORS_ALLOWED_ORIGINS` | Comma-separated allowed frontend origins | `http://localhost:5173,http://127.0.0.1:5173` |
| `CORS_ALLOWED_ORIGIN_REGEX` | Additional allowed-origin regex (*code default*) | Matches `localhost`, `127.0.0.1` and private IPv4 ranges (10.x, 192.168.x, 172.16–31.x) on port 5173 |
| `WFH_ATTENDANCE_ENABLED` | Enable/disable WFH check-in and check-out | `true` |
| `WFH_TEMPLATE_ENCRYPTION_KEY` | Fernet key that encrypts stored face templates | Required for WFH face registration/verification |
| `WFH_FACE_MATCH_TOLERANCE` | Face-match threshold | `0.48`; may be lowered but not raised above `0.48` |
| `WFH_MAX_LOCATION_ACCURACY_METERS` | Maximum acceptable GPS accuracy | `100`; lower values allowed, higher values are capped |
| `WFH_MAX_LOCATION_AGE_SECONDS` | Maximum age of a browser location fix | `120` |
| `ESSL_CONFIG_ENCRYPTION_KEY` | Fernet key that encrypts the ESSL API credentials and request parameters stored in MongoDB | Required to save/use ESSL API configuration |

Generate a Fernet key for either encryption variable:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Keep both keys private and stable; changing them makes existing encrypted data unreadable.

**SMTP port caution.** The modules that send mail do not all connect the same way: account activation uses `SMTP` + STARTTLS (e.g. port 587); password-reset OTP email uses `SMTP_SSL` (implicit TLS, e.g. port 465, which is also that module's code default); WFH email fallback uses `SMTP_SSL` only when the port is `465` and STARTTLS otherwise. Because `SMTP_PORT` is shared, verify that every email flow you rely on works with the port and provider you configure.

### Frontend environment variables

`VITE_API_BASE_URL` and `VITE_BACKEND_PROXY_TARGET` — see [`frontend/README.md`](frontend/README.md#environment-variables).

## Roles and access

The role stored on a user account drives both UI visibility and backend checks. Role names referenced in the code:

| Stored role | Display / notes |
|---|---|
| `HR Admin` | Full administration: organization, users, employees, attendance, payroll, leave setup. Seeded automatically as a role. |
| `Administrator` | Organization setup and attendance administration (including ESSL and WFH settings); no payroll or user/employee-master administration in the UI |
| `HR Manager` | Displayed as **HOD** (Head of Department); leave approver stage |
| `Manager` | Referenced in organization read permissions |
| `Employee` | Self-service |
| `Super Admin` | Referenced only for reading profile organization details |

Examples of backend enforcement:

- Organization routes (`/api/companies`, `/api/business-units`, `/api/departments`, `/api/teams`, `/api/designations`, `/api/holiday-calendars`, `/api/holidays`, `/api/employee-holiday-calendars`, `/api/organization`) are guarded by middleware in `main.py`: reads for HR Admin, Administrator, Manager and Employee; writes only for HR Admin and Administrator; the HOD role is blocked except for `GET /api/organization/me`.
- Payroll administration endpoints require `HR Admin`.
- ESSL management requires `HR Admin` or `Administrator`.
- Approval of leave requests follows the assigned approver chain (see [Leave](#leave-and-permissions)).

Team views (`Team Members`) are available to any user who is assigned as a reporting manager.

## Authentication and sessions

- `POST /api/auth/login` accepts a work email or employee ID plus password and returns an opaque random token (`secrets.token_urlsafe(32)`) and the user object. The frontend sends it as `Authorization: Bearer <token>`.
- **Sessions are stored in memory** (`app/session.py`, a dict of token → user id). They are lost when the backend restarts and are not shared between multiple server processes. There is no session-expiry mechanism in the code; a token stays valid until logout or restart.
- **Lockout:** after 5 consecutive failed logins an account is locked for 15 minutes. The login response is the same generic "Invalid email or password" for unknown, inactive, locked and wrong-password cases.
- **Activation:** `POST /api/users` without a password creates an inactive account with a hashed activation token; the emailed link expires after 24 hours and opens `/activate` in the frontend.
- **Password reset:** `POST /api/auth/forgot-password` emails a 6-digit OTP (valid 5 minutes, maximum 5 attempts); `verify-reset-otp` and `reset-password` complete the flow. OTPs and reset tokens are stored hashed.
- **Passwords** are hashed with PBKDF2-HMAC-SHA256 (120,000 iterations).

## Database

MongoDB, accessed with PyMongo. The `ensure_*` functions create collections with JSON-schema validators and indexes at startup and do not replace existing data.

| Group | Collections |
|---|---|
| Accounts and access | `users`, `roles`, `permissions`, `audit_logs`, `password_resets` |
| Organization | `companies`, `business_units`, `departments`, `teams`, `designations`, `organization_audit_history` |
| Holidays | `holiday_calendars`, `holidays`, `employee_holiday_calendars`, `holiday_calendar_reminders` |
| Employees | `employees`, `employee_details` (also holds the scheduled working hours; schedule changes are archived in working-schedule history) |
| Attendance | `attendance`, `attendance_modification_history`, `attendance_requests`, `attendance_request_date_claims`, `attendance_remarks` |
| ESSL | `essl_config`, `essl_raw_punches`, `essl_sync_history`, `essl_punch_issues` |
| WFH | `wfh_face_templates`, `wfh_liveness_challenges`, `wfh_email_verification_tokens`, `wfh_email_verification_sessions` |
| Leave | `leave_types`, `leave_policies`, `leave_balances`, `leave_requests`, `permission_requests`, `leave_notifications` |
| Notifications | `notifications`, `notification_recipients`, `notification_sequences` |
| Payroll | `employee_salary_profiles`, `monthly_payroll_records`, `payslip_records`, `payroll_review_audit` |

Notable unique indexes include `users.work_email`, `users.employee_id` (sparse), `users.mobile`, `roles.name`, the ESSL employee mapping on users, and unique keys on ESSL punches and issues.

## API reference

All routes are under `/api` unless stated. Unless noted, calls require `Authorization: Bearer <token>`. Complete, interactive request/response schemas are available at `/docs` while the backend is running. The tables below list the routes defined in the code.

### Health and authentication

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Health check |
| POST | `/api/auth/login` | Login |
| GET | `/api/auth/me` | Current user |
| POST | `/api/auth/logout` | Logout |
| POST | `/api/auth/change-password` | Change own password |
| GET, POST | `/api/auth/activate` | Read activation details / activate account |
| POST | `/api/auth/forgot-password` | Send reset OTP |
| POST | `/api/auth/verify-reset-otp` | Verify reset OTP |
| POST | `/api/auth/reset-password` | Set new password |

### Users and employees

| Method | Path | Description |
|---|---|---|
| POST, GET | `/api/users` | Create (activation flow) / list users |
| GET, PUT, PATCH, DELETE | `/api/users/{user_id}` | Read / update / partial update / deactivate user |
| PATCH | `/api/users/{user_id}/status` | Set active status |
| POST | `/api/users/{user_id}/activate` | Activate a user |
| POST, GET | `/api/employees` | Create / list employees |
| GET, PUT, DELETE | `/api/employees/{employee_id}` | Read / update / deactivate employee (record retained) |
| GET | `/api/employees/{employee_id}/holiday-calendar` | Calendar applicable to an employee |
| GET | `/api/employee-details` | List extended employee details |
| GET, PUT | `/api/employee-details/{user_id}` | Read / update one employee's details |

### Organization

| Method | Path | Description |
|---|---|---|
| POST, GET | `/api/companies`; GET `/api/companies/availability` | Companies / legal entities / branches |
| GET, PUT, DELETE | `/api/companies/{company_id}` | Single company |
| POST, GET | `/api/business-units`; GET, PUT, DELETE `/api/business-units/{id}` | Business units |
| POST, GET | `/api/departments`; GET, PUT, DELETE `/api/departments/{id}` | Departments |
| POST, GET | `/api/teams`; GET, PUT, DELETE `/api/teams/{id}` | Teams |
| POST, GET | `/api/designations`; GET, PUT, DELETE `/api/designations/{id}` | Designations |
| GET | `/api/organization/hierarchy` | Organization hierarchy |
| GET | `/api/organization/me` | The signed-in user's organization details |

### Holidays

| Method | Path | Description |
|---|---|---|
| POST, GET | `/api/holiday-calendars` | Create / list calendars |
| GET | `/api/holiday-calendars/options` | Calendar options |
| GET, PUT, DELETE | `/api/holiday-calendars/{calendar_id}` | Single calendar |
| GET | `/api/holiday-calendars/{calendar_id}/audit` | Calendar audit history |
| POST | `/api/holiday-calendars/{calendar_id}/publish` | Publish a calendar |
| POST, GET | `/api/holiday-calendars/{calendar_id}/holidays` | Add / list holidays |
| PUT, DELETE | `/api/holidays/{holiday_id}` | Update / delete a holiday |
| POST, GET | `/api/employee-holiday-calendars` | Assign / list employee calendar assignments |
| DELETE | `/api/employee-holiday-calendars/{assignment_id}` | Remove an assignment |
| GET | `/api/my-holidays` | Holidays applicable to the signed-in employee |

### Reporting manager (`/api/reporting-manager`)

| Method | Path | Description |
|---|---|---|
| GET | `/me` | Whether the current user is a reporting manager |
| GET | `/options` | Candidate approvers for assignment |
| GET, PUT | `/{user_id}` | Read / set an employee's Reporting Manager, HOD and HR Admin approvers |
| GET | `/team-members/list` | Team members of the current reporting manager |
| GET | `/team-members/{member_id}` | Team member detail |
| GET | `/team-members/{member_id}/attendance` | Team member attendance |

### Attendance (`/api/attendance`)

| Method | Path | Description |
|---|---|---|
| GET, POST | `` (root) | List attendance for a date / save attendance |
| GET | `/my` | Own attendance for a month |
| GET | `/user/{user_id}` | A user's attendance for a month |
| GET | `/user/{user_id}/monthly-details` | Monthly detail |
| GET | `/day/{user_id}` | Single-day breakdown |
| GET, PUT | `/my/remarks` | Own attendance remarks |
| GET | `/employees`, `/departments`, `/department-employees` | Filter lookups |
| GET | `/payroll-locks` | Months locked by payroll |
| GET | `/user/{user_id}/export.xlsx` | Excel export for one user |
| GET | `/export-options` | Export options |
| POST | `/export.xlsx` | Excel export for selected employees (one worksheet, one section per employee) |
| GET | `/historical/user/{user_id}`, `/historical/user/{user_id}/history` | Historical attendance and modification history |
| POST | `/historical/modify` | Modify historical attendance |
| POST | `/historical/weekly-off-preview`, `/historical/weekly-off-approve`, `/historical/generate-weekly-off` | Weekly-off generation workflow |

### Work From Home (`/api/attendance/wfh`)

| Method | Path | Description |
|---|---|---|
| GET | `/me` | Own WFH status/configuration |
| POST | `/face/challenge`, `/face/register` | Liveness challenge; face registration |
| POST | `/location/register`, `/location/verify` | Register / verify location |
| POST | `/check-in`, `/check-out` | WFH check-in / check-out |
| POST | `/break/start`, `/break/end` | WFH breaks |
| POST | `/email-fallback/request`, `/email-fallback/redeem`, `/qr-fallback/request` | One-time email/QR fallback |
| GET | `/admin/employees` | WFH settings for employees (admin) |
| PUT | `/admin/employees/{user_id}` | Update an employee's WFH settings |
| POST, DELETE | `/admin/employees/{user_id}/approved-locations` and `.../approved-locations/{location_id}` | Manage approved locations |
| DELETE | `/admin/employees/{user_id}/location` | Remove registered location |
| GET, DELETE | `/admin/employees/{user_id}/face` | View status / remove registered face |

### ESSL (`/api/attendance/essl`)

| Method | Path | Description |
|---|---|---|
| GET, PUT | `/config` | Read / save API configuration (credentials stored encrypted) |
| POST | `/test-connection` | Test the configured API |
| POST | `/sync` | Sync punches for a date range |
| POST | `/preview-file`, `/import-file` | Preview / import an `.xls`, `.xlsx` or `.csv` export |
| GET | `/employees` | Employees with ESSL mappings |
| PUT | `/employees/{user_id}` | Set an employee's ESSL ID mapping |
| GET | `/sync-history`, `/issues` | Sync runs and punch issues |

### Attendance requests (`/api/attendance-requests`)

| Method | Path | Description |
|---|---|---|
| POST | `` (root) | Create a correction request |
| GET | `` (root) | List requests (HR) |
| GET | `/my` | Own requests |
| GET | `/{request_id}` | Request detail |
| POST | `/{request_id}/cancel` | Cancel a request |
| POST | `/{request_id}/respond` | Approve / reject per date |
| GET | `/eligible-dates` | Dates eligible for correction |
| GET | `/calendar`, `/calendar/user/{user_id}` | Request calendar |

### Leave and permissions (`/api/leave`)

| Method | Path | Description |
|---|---|---|
| GET, POST | `/types`; PUT `/types/{leave_type_id}` | Leave types |
| GET | `/policies/{leave_type_id}`; POST `/policies`; PUT `/policies/{leave_type_id}` | Leave policies |
| GET, PUT | `/company-policy` | Company leave/weekly-off policy |
| GET | `/balance`, `/approval-preview`, `/my-approval-role`, `/dashboard` | Balances, approver preview, approver role, dashboard |
| POST | `/apply` | Apply for leave |
| GET | `/my-requests`, `/pending`, `/requests/{request_id}`, `/requests/{request_id}/attachment` | Requests and attachment |
| PUT | `/requests/{request_id}/approve`, `/reject`, `/cancel` | Decide / cancel |
| PUT | `/requests/{request_id}/cancellation/{decision}` | Decide a cancellation request |
| POST | `/permissions` | Request a permission |
| GET | `/permissions/my-requests`, `/permissions/pending` | Permission lists |
| PUT | `/permissions/{request_id}/approve`, `/reject`, `/cancel` | Decide / cancel permission |
| PUT | `/permissions/{request_id}/cancellation/{decision}` | Decide a permission cancellation |
| GET | `/reports`, `/reports/csv` | Leave reports (JSON / CSV) |
| GET | `/notifications` | Leave notifications |

### Notifications (`/api/notifications`)

| Method | Path | Description |
|---|---|---|
| POST, GET | `` (root) | Create / list notifications |
| GET | `/unread-count`, `/snapshot`, `/lookups` | Counts, snapshot, audience lookups |
| PUT, DELETE | `/{notification_id}` | Update / delete |
| POST | `/{notification_id}/view`, `/acknowledge`, `/star`, `/unstar`, `/hold`, `/resume` | Per-notification actions |
| GET | `/{notification_id}/viewers` | Who viewed it |
| POST | `/leave/{id}/read`, `/leave/{id}/star`, `/leave/{id}/unstar` | Leave-notification actions |
| DELETE | `/leave/{id}` | Delete a leave notification |

### Payroll (`/api/payroll`)

| Method | Path | Description |
|---|---|---|
| GET | `/employees` | Employees for payroll |
| GET | `/salary-profiles` | Salary profiles |
| PUT | `/salary-profiles/{employee_id}` | Create/update a salary profile |
| GET | `/salary-profiles/{employee_id}/history` | Salary profile history |
| GET | `/attendance-summary` | Attendance summary used for calculation |
| POST | `/calculate` | Calculate monthly payroll |
| GET | `/records` | Payroll records |
| POST | `/records/{record_id}/action` | `approve`, `process`, `paid`, `disapprove` |
| GET | `/payslips`, `/payslips/{record_id}`, `/payslips/{record_id}/pdf` | Payslip register / view / PDF (HR Admin) |
| GET | `/my-payslips`, `/my-payslips/{record_id}`, `/my-payslips/{record_id}/pdf` | Own payslips |

## Business rules

### Attendance rules

- **Half-day classification** (`attendance_calculations.py`): each employee has a configured working schedule; the **Half Day Time** is half of the scheduled working duration. Total Duration (check-out minus check-in, breaks included) is compared with it:
  - below **50%** → Absent
  - **50% to 120%** (both inclusive) → Half-Day Present
  - above **120%** → full-day rules apply
  
  Comparisons use integer arithmetic, so the boundaries are exact. Holiday, Weekly Off, Leave, Medical Leave, Permission, Not Applicable and "Before joining" statuses are protected and not overridden by this calculation.
- **Durations** are elapsed wall-clock minutes; a check-out earlier than check-in is treated as an overnight shift. Overlapping working intervals are merged so minutes are not counted twice, and approved permission time is added once.
- **First half / second half:** the scheduled shift is split at its midpoint, and the half containing more of the actual working time determines First Half Present or Second Half Present.
- **Attendance issues** (incomplete or inconsistent punches, e.g. overlapping intervals) are reported separately from the attendance status.
- **Leave integration:** approved leave is written to attendance only for the current day (company timezone) or when a past leave date is read; future leave dates are never pre-created. Cancelling a leave removes the attendance it created. Approved full-day leave locks attendance until cancelled. Leave attendance is always derived from `leave_requests`, never from client-supplied leave data.
- **Calendar window:** attendance calendars look forward up to 89 days (`CALENDAR_FORWARD_DAYS`).
- **Payroll locks:** months whose payroll is Approved, Processed or Paid are locked against attendance changes (`GET /api/attendance/payroll-locks`).
- **Attendance requests:** employees request a status for their own past dates (Present, Half-Day Present, Absent, Holiday, Weekly Off, Medical Leave, Permission); HR Admin approves (optionally changing status/times) or rejects each date individually. Monthly Weekly Off and Medical Leave limits are enforced at every step.
- **Weekly Off generation:** HR Admin previews and approves weekly offs for eligible `Not Marked` dates; at most **4 Weekly Offs per month** (`MAX_WEEKLY_OFFS_PER_MONTH`). Company-configured weekly-off weekdays are preferred, then the most frequent weekday, with ties resolved in the order Sunday, Saturday, Monday–Friday.
- **Excel export:** all selected employees are rendered as separate sections in one worksheet.

### Leave and permissions

- Default leave types: Casual Leave (`CL`), Medical Leave (`ML`), Earned Leave (`EL`) and Loss of Pay (`LOP`, unpaid). Leave types can be created and edited; each can allow half-day and require an attachment.
- **Medical Leave** is limited to **1 day per month**.
- **Approval chain:** a request is routed to the stage assigned to the employee: Reporting Manager → HOD → HR Admin. The approver is assigned on the employee record through the reporting-manager API; the HOD stage requires an account with the `HR Manager` role and the HR Admin stage an `HR Admin` account.
- Approved leave and permissions can be cancelled through a cancellation workflow with approver decisions. Approvers and employees receive leave notifications.
- Permissions are short time-range requests (date, start time, end time, reason) with the same approval and cancellation handling.

### Holiday calendars

- Calendars have a status (`Draft` or `Published`); only Published, active calendars and non-cancelled holidays are official for attendance, leave and payroll.
- Religion-based calendars apply to employees whose Employee Master religion matches and, when organization assignments are set, who belong to those branches; calendars with no assignments apply to all branches.
- A background thread (`holiday-calendar-reminder-scheduler`) runs hourly and emits holiday-calendar reminders; deduplication is enforced by a unique index on reminder target and period.
- The Holiday Calendar screen can import public holidays from the third-party Nager.Date service, called from the user's browser.

### ESSL integration

- **API sync:** configure URL, method (`GET`/`POST`), authentication (`none`, `bearer`, `api_key`, `basic`), request parameters/body/headers, and JSON paths for records, employee ID, timestamp, and optional punch/device ID. Secrets and request data are stored encrypted (`ESSL_CONFIG_ENCRYPTION_KEY`). Responses are capped at 10 MB.
- **File import:** `.csv`, `.xls` and `.xlsx` exports can be previewed and imported; file type is detected from content.
- **Mapping:** each user has an optional ESSL employee ID (unique, normalized). Raw punches are stored with a unique key so repeated syncs are idempotent; problems are recorded as punch issues.

### Work From Home

- Employees must have a registered face and an approved location configured by HR Admin/Administrator.
- Face verification is local: a challenge-response liveness check (blink / head-turn sequences) and face-embedding comparison, with tolerance capped at `0.48`. Face templates are Fernet-encrypted at rest.
- Browser location must be within the allowed accuracy and age limits and inside an approved location.
- A one-time email or QR fallback link (valid 15 minutes, single use) is available; it does not bypass face, liveness, location-accuracy or geofence checks.
- Attendance created by WFH check-in/out is recalculated with the same half-day rules as ESSL punches.

### Payroll

- **Salary profile** (HR Admin): monthly salary, bank details, PAN (format `ABCDE1234F`), currency (default `INR`), effective date, and up to 50 components (earning or deduction; fixed or percentage), plus attendance-deduction settings (enabled flag; basis `calendar_days`, `working_days` or `fixed_amount`; per-day amount).
- **Calculation** uses the attendance records for the month (present, half days, paid/unpaid leave, medical leave, holidays, weekly offs).
- **Workflow:** `Calculated` → `Approved` → `Processed` → `Paid`. Approval is blocked when attendance for the month changed after calculation. Marking a record `Paid` requires a payment reference and makes the payslip available. A `Calculated` record can be disapproved (it is deleted). Approved/Processed/Paid records are not overwritten by recalculation.
- **Payslips:** HR Admin views the payslip register; each employee views and downloads their own payslips. PDFs are generated with ReportLab using the bundled Noto Sans fonts and company logo.

### Notifications

- Audience types: `all`, `roles`, `teams`, `departments`, `users`.
- Validity can be set in minutes, hours, days or weeks; a notification can be `ACTIVE` or on `HOLD`, and can be updated, resumed or deleted.
- Recipients can view, acknowledge and star notifications; senders can see viewers. Leave-related notifications have their own read/star/delete actions.

## Scripts and tests

### Maintenance scripts (run from `backend/`)

| Script | Purpose |
|---|---|
| `python scripts/recalculate_half_day_attendance.py [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--apply]` | Recalculates historical **automatic** (ESSL / WFH) attendance with the current half-day rules. Dry run by default; writes only with `--apply`. Skips manual, HR-edited and attendance-request records and any month whose payroll is Approved, Processed or Paid. |
| `python scripts/check_wfh_mongodb.py` | Read-only MongoDB connectivity and collection check for WFH. Requires `MONGODB_URL` / `MONGODB_DATABASE` in `backend/.env`. |

### Tests

`backend/tests/` contains 23 test modules covering attendance calculations, attendance requests and export, ESSL import and employee recreation, WFH attendance and late check-in, weekly-off generation, working details, holiday models/API/workflow, religion holidays, organization workflow, companies API, company-employee assignment, employee details, users API, leave approval routing and hierarchy, notification scope, payslip view, and a checklist test. The suite mixes `unittest` style and `pytest` style; `pytest` is not listed in `requirements.txt`, so install it separately:

```bash
cd backend
pip install pytest
python -m pytest tests
```

Several tests import application modules, which create the MongoDB client at import time, so keep `MONGODB_URL` and `MONGODB_DATABASE` defined in `.env` when running them. Frontend checks are limited to `npm run lint` and `npm run build`.

## Deployment notes

- Build the frontend (`npm run build`) and serve `frontend/dist/` from a static host or web server with a fallback to `index.html` (several email links open real URL paths such as `/activate` and `/reset-password`).
- Set `VITE_API_BASE_URL` at build time to the public backend URL.
- Run the backend with Uvicorn behind a reverse proxy using HTTPS. Because sessions are in memory, run a single backend process unless the session store is changed.
- Set `CORS_ALLOWED_ORIGINS` to the deployed frontend origin(s), `FRONTEND_URL` to the public frontend URL, and `HRMS_ENV` to a value other than `development` to disable sample-login seeding.
- Provide `ESSL_CONFIG_ENCRYPTION_KEY` and, if WFH is used, `WFH_TEMPLATE_ENCRYPTION_KEY`; store all secrets outside version control.
- WFH requires HTTPS on the frontend so browsers permit camera and location access.

## Known limitations and security notes

These reflect the current code and are listed so they can be addressed before a production rollout:

- **Authorization coverage is uneven.** The `/api/users*` and `/api/employees*` handlers in `core_routes.py` do not declare an authentication or role dependency, so access to them is not enforced by the backend in the reviewed code; the UI restricts these screens by role. Organization routes are protected by middleware, and the payroll, ESSL, leave, notification, attendance-request and reporting-manager routers perform their own session checks. Review and add backend authorization to the user and employee endpoints (and the other routes listed in the API reference as needed) before exposing the service beyond a trusted network.
- **In-memory sessions:** no expiry, lost on restart, not shared across processes.
- **SMTP mode differs by module** (see [Configuration](#configuration)).
- **Frontend session storage:** the token is kept in `sessionStorage`, readable by any script running on the page.
- **Public-holiday import** sends requests from the browser to a third-party service (`date.nager.at`).
- The `NotificationPanel` frontend component exists in the source tree but is not imported anywhere.

## Troubleshooting

**Backend fails at startup with a Mongo or `None` error** — `MONGODB_URL` / `MONGODB_DATABASE` are missing; the client is created at import time. Run from `backend/` so `.env` is found.

**Frontend cannot reach the API** — confirm the backend is on port 8000, check `VITE_BACKEND_PROXY_TARGET` (dev) or `VITE_API_BASE_URL` (production build), and CORS settings.

**Everyone is signed out after a backend restart** — expected; sessions are in memory.

**Account locked** — 5 failed logins lock the account for 15 minutes.

**Activation or reset emails are not received** — check the `SMTP_*` values, the port/TLS mode (see the SMTP caution), and `FRONTEND_URL` for correct link targets.

**WFH face registration fails with a configuration error** — install `requirements-wfh.txt` and set `WFH_TEMPLATE_ENCRYPTION_KEY` to a valid Fernet key; confirm `WFH_ATTENDANCE_ENABLED` is not `false`.

**ESSL configuration cannot be saved** — set `ESSL_CONFIG_ENCRYPTION_KEY` to a valid Fernet key.

**Attendance shows Not Marked instead of Absent** — this is intentional; the backend decides the final status from joining date, holidays, leave, weekly off and the completed-day rules.

**Payroll approval is rejected with an "attendance changed" message** — recalculate the month's payroll so it reflects the latest attendance, then approve again.
