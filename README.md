# HRMS — Human Resource Management System

HRMS is a full-stack Human Resource Management System with a **React 19 + Vite** frontend and a **Python FastAPI** backend that stores its data in **MongoDB**.

This guide covers the whole project: modules, setup, configuration, running, testing, and deployment. Frontend-specific details are in [`frontend/README.md`](frontend/README.md).

## Contents

- [System overview](#system-overview)
- [Technology stack](#technology-stack)
- [HRMS modules](#hrms-modules)
- [Roles](#roles)
- [Repository layout](#repository-layout)
- [Prerequisites](#prerequisites)
- [MongoDB setup](#mongodb-setup)
- [Backend setup](#backend-setup)
- [Frontend setup](#frontend-setup)
- [Environment configuration](#environment-configuration)
- [First sign-in](#first-sign-in)
- [Run the application](#run-the-application)
- [Background jobs](#background-jobs)
- [API documentation](#api-documentation)
- [Testing and quality checks](#testing-and-quality-checks)
- [Utility scripts](#utility-scripts)
- [Deployment notes](#deployment-notes)
- [Security and operational notes](#security-and-operational-notes)
- [Troubleshooting](#troubleshooting)

## System overview

```text
Browser (React + Vite, port 5173)
        |
        |  REST requests under /api  (Bearer session token)
        v
FastAPI application (Uvicorn, port 8000)
        |
        v
MongoDB
```

In development, Vite proxies `/api` requests to the FastAPI server. The frontend keeps the signed-in session in browser `sessionStorage` and sends the session token as a Bearer token. The backend owns authorization, business rules and all MongoDB access.

## Technology stack

| Layer | Technologies |
|---|---|
| Frontend | React 19, React DOM 19, Vite 8, JavaScript/JSX |
| UI | Bootstrap 5, Bootstrap Icons, project CSS |
| Frontend libraries | `libphonenumber-js`, `qrcode.react`, `pdfjs-dist`; Oxlint for linting |
| Backend | Python, FastAPI, Pydantic 2, Starlette, Uvicorn |
| Database | MongoDB via PyMongo (`dnspython` included for `mongodb+srv://` URIs) |
| Backend libraries | `python-dotenv`, `cryptography` (Fernet encryption), `reportlab` (payslip PDF), `openpyxl` and `xlrd` (Excel export / import), `python-multipart` (file upload), `email-validator` |
| Optional (WFH face verification) | `face-recognition`, `mediapipe` (see [Optional WFH dependencies](#optional-wfh-face-verification-dependencies)) |
| Backend tests | `unittest`-style test modules in `backend/tests/`, run with pytest |

Exact versions are in `frontend/package.json`, `frontend/package-lock.json`, `backend/requirements.txt` and `backend/requirements-wfh.txt`.

## HRMS modules

Each module below exists in the source; API prefixes refer to the FastAPI routers.

### Authentication and users (`/api/auth`, `/api/users`)
- Login with a server-side session token (kept in backend memory), logout, change password, and current-user lookup.
- Account activation by emailed link (`/activate`); the link expires after 24 hours.
- Forgot password with an emailed OTP, OTP verification, and password reset.
- Account lockout: 5 consecutive failed logins lock the account for 15 minutes.
- User management (HR Admin): create, update, change status, delete, and re-issue activation for users.

### Organization setup
- Company / legal entity (`/api/companies`), business units, departments, teams and designations.
- Organization hierarchy and the signed-in user's organization details (`/api/organization/...`).
- Holiday calendars and holidays by year and religion, with draft/publish workflow, audit history, assignment of calendars to employees, an employee "my holidays" view, and automatic holiday-calendar reminder notifications.
- Organization setup is writable only by HR Admin / Administrator; the backend rejects HOD access to these routes.

### Employees
- Employee Master records (`/api/employees`).
- Employee details and working details (`/api/employee-details`), including work type, scheduled work start/end time, work duration and half-day duration, kept with an effective-date schedule history so past attendance is evaluated against the schedule that applied on that date.
- Reporting-manager assignment and Team Members views (`/api/reporting-manager`): managers see their direct reports' details and attendance.

### Attendance (`/api/attendance`, `/api/attendance-requests`)
- Daily attendance for HR Admin, My Attendance for employees, Employee Attendance history, and monthly attendance calendars with status summaries.
- Half-day, early/late going-by, permission and break calculations.
- **Attendance Issue**: a day with a check-in but no check-out becomes an Attendance Issue only after the employee's scheduled work end time has passed (the following day's end time for overnight shifts). The current day is not marked while the shift is still open. Payroll-approved months are not changed by the marking job.
- Attendance requests: employees request corrections for eligible dates; approvers respond; requests can be cancelled.
- HR historical attendance modification (limited to the current date and eligible earlier dates; payroll-locked periods are read-only), weekly-off preview/approval/generation, and payroll-lock lookup.
- Excel export (`.xlsx`) for single and multiple employees.
- Personal attendance remarks.

### Work From Home attendance (`/api/attendance/wfh/...`)
- WFH check-in/check-out with browser location verification (accuracy and age limits) against registered home/approved locations.
- Local face registration and verification with challenge-based liveness checks (requires the optional WFH dependencies and an encryption key).
- Break start/end, late check-in requests, email and QR fallback verification (`/wfh-email-verification`).
- HR Admin WFH settings per employee: enable/disable, permitted dates/days/times, approved locations, face-template management.
- Automatic WFH absence generation (see [Background jobs](#background-jobs)).
- Can be switched off with `WFH_ATTENDANCE_ENABLED=false`.

### ESSL biometric attendance (`/api/attendance/essl`)
- Configurable HTTP API connection (GET/POST; none, bearer, API key or basic authentication; JSON path mapping for employee ID, timestamp and optional punch/device ID). Credentials and request parameters are stored encrypted in MongoDB.
- Connection test, manual sync, sync history, employee-to-device mapping, and a punch-issue list.
- Import from `.csv`, `.xls` or `.xlsx` files with a preview step.

### Leave and permissions (`/api/leave`)
- Leave types, policies and a company policy (including weekly-off weekdays); balances, half-day leave, optional attachments.
- Apply, approve, reject, cancel, and cancellation-approval workflows with approval-chain routing.
- Permission (short absence) requests with their own approval flow.
- Leave dashboard, reports and CSV report export, and leave notifications.

### Notifications (`/api/notifications`)
- Targeted notifications with view/acknowledge tracking, star/unstar, viewer lists, edit, hold/resume and delete, plus leave-related notifications. The frontend bell refreshes every 3 seconds.

### Payroll and payslips (`/api/payroll`)
- Employee salary profiles with change history.
- Monthly payroll calculation from attendance, leave and holiday data (Attendance Issue dates count as loss of pay until resolved).
- Record workflow: Calculated → Approved → Processed → Paid, with disapproval; approved periods lock attendance edits.
- Payslips (on-screen and PDF) are available for Paid records; employees can view their own payslips.

## Roles

The frontend shows three role labels: **HR Admin**, **HOD** and **Employee**. Stored role values include legacy identifiers (for example `HR Manager`, `Manager`, `Administrator`, `Super Admin`) that the frontend maps to these labels (`frontend/src/services/roleLabels.js`), and some backend authorization checks still refer to the legacy values. Changing a label does not migrate stored roles or backend permissions; review both frontend menus and backend checks before any role migration. See [`frontend/README.md`](frontend/README.md#roles-and-access) for current menu visibility.

## Repository layout

Extract the two archives into one project directory:

```text
HRMS-Project/
├── README.md                   # This guide
├── frontend/
│   ├── README.md
│   ├── package.json
│   ├── package-lock.json
│   ├── vite.config.js
│   ├── .oxlintrc.json
│   ├── index.html
│   ├── public/
│   └── src/                    # components/, pages/, services/, config/, assets/
└── backend/
    ├── .env.example            # Copy to .env (never commit .env)
    ├── requirements.txt
    ├── requirements-wfh.txt    # Optional WFH face-verification packages
    ├── app/
    │   ├── main.py             # FastAPI app, middleware, CORS, background scheduler
    │   ├── core_routes.py      # Auth, users, organization, holidays, employees, /health
    │   ├── attendance.py, attendance_requests.py, attendance_calculations.py, attendance_export.py
    │   ├── essl.py, wfh_biometrics.py, weekly_off_planner.py
    │   ├── leave.py, notifications.py, payroll.py, payslip_pdf.py
    │   ├── employee_details.py, working_details.py, reporting_manager.py, religion_holidays.py
    │   ├── activation.py, password_reset.py, security.py, session.py
    │   └── db/mongodb.py       # MongoDB client, validators, index/collection setup
    ├── assets/                 # NotoSans fonts and a default company logo image used by payslip PDFs
    ├── scripts/                # check_wfh_mongodb.py, recalculate_half_day_attendance.py
    └── tests/
```

`backend/.env` and `frontend/.env.local` are created locally and are not part of the source.

## Prerequisites

- **Node.js** `^20.19.0` or `>=22.12.0` and npm (required by the locked Vite 8 release).
- **Python 3.12** is recommended (the bundled bytecode caches were produced with CPython 3.12); use a Python 3 version compatible with the pinned `requirements.txt`.
- **MongoDB**: a reachable server or MongoDB Atlas cluster, and a database user with read/write access to the HRMS database.
- Network access between the browser, frontend, backend and MongoDB.

## MongoDB setup

1. Start MongoDB locally or create an Atlas deployment, and create a database user.
2. Copy `backend/.env.example` to `backend/.env`.
3. Set `MONGODB_URL` and `MONGODB_DATABASE` (the template uses `hrms_db`). **Both are required**: the backend creates its MongoDB client when it starts and fails to import without a database name.

```dotenv
MONGODB_URL=mongodb://127.0.0.1:27017/
MONGODB_DATABASE=hrms_db
```

For Atlas, use the connection string Atlas provides and URL-encode special characters in the credentials. You do not need to create collections by hand: on startup the backend creates or updates its collections, JSON-schema validators and indexes, and fills in a few defaults (for example the `HR Admin` role).

## Backend setup

```bash
cd backend
python -m venv .venv
```

Activate the environment:

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

```bash
# macOS / Linux
source .venv/bin/activate
```

Install dependencies and create the environment file:

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env            # Windows PowerShell: Copy-Item .env.example .env
```

Edit `.env` (see [Environment configuration](#environment-configuration)), then start the API **from the `backend/` directory**:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

`--reload` is for development only. The API listens on port `8000`.

### Optional WFH face-verification dependencies

WFH face registration and verification need `face-recognition` and `mediapipe`, which are **not** in `requirements.txt`:

```bash
pip install -r requirements-wfh.txt
```

These packages may need native build tools on the host; test them on the target operating system. Without them the rest of HRMS works, but WFH face registration/verification returns an error saying local face verification is not installed. You also need `WFH_TEMPLATE_ENCRYPTION_KEY` set.

## Frontend setup

```bash
cd frontend
npm ci
npm run dev
```

Vite serves the app on port `5173` (bound to `0.0.0.0`, with `strictPort`, so it fails if the port is taken). Open `http://localhost:5173`. The `/api` proxy targets `http://127.0.0.1:8000` unless `VITE_BACKEND_PROXY_TARGET` is set. If `../certs/localhost-key.pem` and `../certs/localhost.pem` both exist, Vite serves HTTPS. See [`frontend/README.md`](frontend/README.md).

## Environment configuration

### Backend (`backend/.env`, template: `backend/.env.example`)

| Variable | Purpose |
|---|---|
| `MONGODB_URL`, `MONGODB_DATABASE` | MongoDB connection URI and database name (`hrms_db` in the template). Required. |
| `ORG_TIMEZONE` | Time zone for organization/holiday logic and reminders. Default `Asia/Kolkata`. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM` | Outgoing email (activation links, password-reset OTP, WFH email fallback, HR emails). `SMTP_FROM` defaults to `SMTP_USERNAME`. See the SMTP note below. |
| `FRONTEND_URL` | Base URL used to build links in emails. Default `http://localhost:5173`. |
| `WFH_ATTENDANCE_ENABLED` | `true`/`false` switch for the WFH check-in/out endpoints. Default `true`. |
| `WFH_TEMPLATE_ENCRYPTION_KEY` | Fernet key that encrypts stored WFH face templates. Required for face registration/verification. |
| `WFH_FACE_MATCH_TOLERANCE` | Face-match threshold, greater than 0 and at most `0.48`. Other values stop the backend at startup. Default `0.48`. |
| `WFH_MAX_LOCATION_ACCURACY_METERS` | Maximum accepted GPS accuracy; values above `100` are capped at `100`. |
| `WFH_MAX_LOCATION_AGE_SECONDS` | Maximum age of a browser location reading. Default `120`. |
| `CORS_ALLOWED_ORIGINS` | Comma-separated exact frontend origins. Default `http://localhost:5173,http://127.0.0.1:5173`. |
| `CORS_ALLOWED_ORIGIN_REGEX` | Optional override of the built-in pattern, which also allows `localhost`, `127.0.0.1` and private-network IPv4 addresses (10.x, 172.16-31.x, 192.168.x) on port `5173`. |
| `ESSL_CONFIG_ENCRYPTION_KEY` | Fernet key that encrypts ESSL API credentials and request parameters in MongoDB. Keep it stable. |
| `HRMS_ENV`, `DEMO_USER_NAME`, `DEMO_USER_EMAIL`, `DEMO_USER_MOBILE`, `DEMO_USER_ROLE`, `DEMO_USER_PASSWORD` | Development sign-in account; see [First sign-in](#first-sign-in). |
| `COMPANY_TIMEZONE` | Optional, not in the template. Time zone used by attendance calculations and ESSL timestamps. Default `Asia/Kolkata`. |
| `COMPANY_START_TIME` | Optional, not in the template. Company default start time (`HH:MM`) used by attendance/leave code. Default `09:00`. |

Generate a Fernet key (use separate keys for WFH and ESSL):

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**SMTP note.** The code uses different connection modes depending on the feature: activation emails use STARTTLS (the template port `587`), the password-reset OTP email always uses an implicit-TLS (`SMTP_SSL`) connection, and the WFH email fallback uses implicit TLS when the port is `465` and STARTTLS otherwise. Because all features read the same `SMTP_PORT`, confirm that activation and password-reset emails both work with your mail server, and adjust the setup if one of them fails. If `SMTP_HOST` or `SMTP_FROM` is empty, the activation link is printed in the backend console (`[DEV ONLY] Activation link ...`) instead of being emailed.

### Frontend (`frontend/.env.local`, optional)

| Variable | Purpose |
|---|---|
| `VITE_BACKEND_PROXY_TARGET` | Target of the Vite `/api` proxy. Default `http://127.0.0.1:8000`. |
| `VITE_API_BASE_URL` | Absolute API base URL embedded in the bundle. Unset: same-origin `/api` in development and `http://127.0.0.1:8000` in a production build. |

Never put secrets in `VITE_*` variables; they are visible to browsers. Never commit `.env` files, credentials or encryption keys. If an encryption key is lost, data encrypted with it (ESSL configuration, WFH face templates) cannot be recovered.

## First sign-in

The source has no built-in default password. The backend can create or refresh one sign-in account at startup for **development**:

1. In `backend/.env`, set `DEMO_USER_EMAIL` and `DEMO_USER_PASSWORD` (optionally `DEMO_USER_NAME`, `DEMO_USER_MOBILE`, `DEMO_USER_ROLE`; the role defaults to `HR Admin`). `HRMS_ENV` defaults to `development` when unset.
2. Start the backend. If `HRMS_ENV` is `development` and both values are set, the account is created or updated (including resetting its password to the configured value) on every startup.
3. Sign in with that email and password at `http://localhost:5173`, then create the organization setup and other users from the application. New users receive an activation link (see the SMTP note).

Set `HRMS_ENV` to any other value (for example `production`) and remove the `DEMO_USER_*` entries for real deployments; the account is then not created. Use a strong, unique password and do not reuse the template values.

## Run the application

1. Make sure MongoDB is running and `backend/.env` is configured.
2. Terminal 1:

   ```bash
   cd backend
   # activate .venv first
   uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
   ```

3. Terminal 2:

   ```bash
   cd frontend
   npm run dev
   ```

4. Open `http://localhost:5173` and sign in.

Quick backend check: `GET http://localhost:8000/health` pings MongoDB and returns a status message.

## Background jobs

The backend starts these jobs in its own process:

| Job | Schedule | What it does |
|---|---|---|
| WFH absence + Attendance Issue sweep | Every 60 seconds | Creates automatic WFH absences that are due, and marks Attendance Issues for check-ins with no check-out once the scheduled work end time has passed. |
| Holiday calendar reminders | Every hour | Emits holiday-calendar reminder notifications. |

Login sessions are held in backend memory (`app/session.py`) and do not expire on their own: restarting the backend signs everyone out, and running several Uvicorn worker processes would not share sessions. Run a single worker unless sessions are moved to shared storage.

## API documentation

While the backend runs, FastAPI serves interactive documentation:

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- OpenAPI schema: `http://localhost:8000/openapi.json`

Router prefixes:

| Prefix | Area |
|---|---|
| `/api/auth`, `/api/users` | Login, activation, password change/reset, user management |
| `/api/companies`, `/api/business-units`, `/api/departments`, `/api/teams`, `/api/designations`, `/api/organization` | Organization setup |
| `/api/holiday-calendars`, `/api/holidays`, `/api/employee-holiday-calendars`, `/api/my-holidays` | Holiday calendars |
| `/api/employees`, `/api/employee-details`, `/api/reporting-manager` | Employees, working details, reporting managers |
| `/api/attendance` (including `/wfh/...`), `/api/attendance/essl`, `/api/attendance-requests` | Attendance, WFH, ESSL, attendance requests |
| `/api/leave` | Leave and permissions |
| `/api/notifications` | Notifications |
| `/api/payroll` | Salary profiles, payroll, payslips |
| `/health` | Health check (no `/api` prefix) |

Treat the live OpenAPI schema as the authoritative reference for endpoints and payloads.

## Testing and quality checks

**Frontend** (from `frontend/`):

```bash
npm run lint
npm run build
```

**Backend** (from `backend/`): the test modules are `unittest`-style and run with pytest, which is not in `requirements.txt`.

```bash
python -m pip install pytest
python -m pytest -q
```

The tests import the application modules, so `MONGODB_URL` and `MONGODB_DATABASE` must be set (for example in `backend/.env`). Many tests replace database calls with mocks, but some may still need a reachable MongoDB; use a dedicated test database and never point tests at production data.

## Utility scripts

Run from `backend/` with the virtual environment active and `.env` configured:

| Script | Purpose |
|---|---|
| `python scripts/check_wfh_mongodb.py` | Read-only MongoDB connectivity check; confirms the `users` and `attendance` collections exist. |
| `python scripts/recalculate_half_day_attendance.py [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--apply]` | Recalculates half-day results for automatic (ESSL / WFH) attendance records. Without `--apply` it only reports how many records are eligible; with `--apply` it writes. Payroll-locked months and manual/admin-edited records are skipped. Back up the database before using `--apply`. |

## Deployment notes

- Build the frontend with `npm run build` and serve `frontend/dist/` from a static web server with SPA fallback to `index.html` (emailed links such as `/activate` and `/reset-password` are application paths).
- Reverse-proxy `/api` (and `/health` if needed) to FastAPI. If the API is on a different origin, set `VITE_API_BASE_URL` before building and put the exact frontend origin in `CORS_ALLOWED_ORIGINS`. The built-in CORS pattern only covers localhost and private-network addresses on port 5173; add your deployed HTTPS origin explicitly.
- Set `FRONTEND_URL` to the deployed frontend address so emailed links are correct.
- Run Uvicorn without `--reload` under a process manager, as a single worker (see [Background jobs](#background-jobs)).
- Use HTTPS; WFH camera and geolocation require a secure context.
- Set `HRMS_ENV=production` and remove `DEMO_USER_*` values.
- Back up MongoDB regularly and test restores. Keep encryption keys backed up separately from the database.

## Security and operational notes

- Keep `.env` files, credentials and encryption keys out of source control.
- Use strong, unique MongoDB credentials and restrict network access to the database.
- Frontend menu hiding is not a security boundary; backend authorization is authoritative.
- Attendance and payroll data affects pay. Test changes on a non-production database, and check how payroll approval locks attendance before operational use.
- Payroll-approved/processed/paid periods are protected from attendance modification; HR Admin should review Attendance Issue dates before running payroll because they are treated as loss of pay until resolved.

## Troubleshooting

| Symptom | Checks |
|---|---|
| Backend fails at startup with a database-name error | `MONGODB_DATABASE` (and `MONGODB_URL`) must be set in `backend/.env`; run uvicorn from `backend/`. |
| Backend fails with a `WFH_FACE_MATCH_TOLERANCE` error | Use a value greater than 0 and no more than `0.48`. |
| Frontend cannot reach the API | Backend on port 8000; `VITE_BACKEND_PROXY_TARGET` / `VITE_API_BASE_URL`; browser network tab; `GET /health`. |
| MongoDB connection fails | Credentials, network allowlist, DNS, and that MongoDB is running. |
| CORS errors | Add the exact frontend origin to `CORS_ALLOWED_ORIGINS` and restart the backend. |
| Cannot sign in on a fresh database | See [First sign-in](#first-sign-in): set `DEMO_USER_EMAIL` and `DEMO_USER_PASSWORD`, with `HRMS_ENV` unset or `development`. |
| Account locked | 5 failed logins lock an account for 15 minutes. |
| Everyone was signed out | Sessions are in memory; a backend restart clears them. |
| Activation email not received | SMTP settings, sender, `FRONTEND_URL`; without SMTP the link is printed in the backend console. |
| Password-reset OTP email fails | The OTP email always uses implicit TLS (`SMTP_SSL`); check `SMTP_HOST`, `SMTP_PORT` (for example 465) and credentials. |
| WFH face verification errors | Install `requirements-wfh.txt`, set `WFH_TEMPLATE_ENCRYPTION_KEY`, use HTTPS or `localhost`, grant camera/location permissions. |
| ESSL settings cannot be read | `ESSL_CONFIG_ENCRYPTION_KEY` must match the key used when the configuration was saved. |
| Page refresh returns 404 after deployment | Configure SPA fallback to `index.html`; proxy `/api` separately. |
