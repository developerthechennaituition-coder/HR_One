# HRMS — Human Resource Management System

A full-stack HRMS application with a React/Vite frontend, a Python FastAPI backend, and MongoDB persistence. The repository contains employee and user administration, organization setup, attendance, WFH attendance controls, ESSL attendance integration workflows, leave and permission management, notifications, payroll, and payslips.

> **Repository layout:** this documentation assumes the backend files are in `backend/` and the frontend files are in `frontend/`. The supplied backend ZIP contains the backend source at its archive root, so if you extract both ZIPs into one repository, place the backend ZIP contents inside `backend/` and the frontend ZIP contents inside `frontend/`.

## Contents

- [Features](#features)
- [Architecture](#architecture)
- [Technology stack](#technology-stack)
- [Prerequisites](#prerequisites)
- [Repository layout](#repository-layout)
- [Configuration](#configuration)
- [Run locally on Windows](#run-locally-on-windows)
- [Run locally on macOS or Linux](#run-locally-on-macos-or-linux)
- [Database initialization and health checks](#database-initialization-and-health-checks)
- [Frontend workflows](#frontend-workflows)
- [Backend API documentation](#backend-api-documentation)
- [Attendance and ESSL device integration](#attendance-and-essl-device-integration)
- [WFH attendance and biometrics](#wfh-attendance-and-biometrics)
- [Payroll workflow](#payroll-workflow)
- [Security and deployment](#security-and-deployment)
- [Troubleshooting](#troubleshooting)
- [Validation checklist](#validation-checklist)

## Features

### Authentication and account lifecycle
- User login, session-backed frontend state, current-user lookup, logout, and change password.
- Account activation.
- Password recovery with OTP verification and password reset (email delivery requires SMTP configuration).
- Role-aware navigation and access-denied handling. Backend authorization remains the security boundary.

### Organization and employee administration
- Company and organization structure, including business units, departments, teams, and designations.
- Holiday calendars, holiday entries, employee calendar assignments, publish/audit workflows.
- Employee master and employee details.
- User management, status updates, and account activation.
- Reporting-manager assignments and team-member views.

### Attendance
- Attendance views, employee/day and monthly attendance details, remarks, and spreadsheet export.
- Work-from-home check-in/check-out, breaks, location registration/verification, and configurable WFH controls.
- ESSL attendance configuration, file import, connection test, synchronization, sync history, and issue review routes.

### Leave, permissions, notifications, and payroll
- Leave types, policies, balances, requests, approvals/rejections/cancellations, reports, and CSV export.
- Permission requests and approval workflows.
- In-app notifications and notification management.
- Employee salary profiles, attendance summaries, payroll calculation and review actions, payroll records, and payslip PDF endpoints.
- Employee self-service payslip views and PDF downloads.

The exact options available to a user depend on the role and backend authorization rules. Availability of a screen does not imply that a particular third-party device or external service is already connected.

## Architecture

```text
Browser
  └── React 19 + Vite frontend (development default: http://localhost:5173)
        └── HTTP(S) requests
              └── FastAPI backend (default: http://127.0.0.1:8000)
                    └── MongoDB database
                    ├── SMTP server (activation/password-reset/WFH email flows, if configured)
                    └── ESSL attendance integration endpoints and import/sync workflows
```

- **Frontend:** React, React DOM, Vite, Bootstrap, Bootstrap Icons, and JavaScript.
- **Backend:** FastAPI, Pydantic, Uvicorn, PyMongo, and Python utility libraries.
- **Database:** MongoDB; the database name and connection URI are configured by environment variables.
- **API reference:** FastAPI serves Swagger UI at `/docs` and OpenAPI JSON at `/openapi.json` while the backend is running.

## Technology stack

| Area | Technology |
|---|---|
| Frontend | React 19, React DOM, Vite 8 |
| UI | Bootstrap 5, Bootstrap Icons |
| Frontend utilities | `libphonenumber-js`, `qrcode.react` |
| Linting | Oxlint |
| Backend API | Python, FastAPI, Pydantic |
| Database driver | PyMongo |
| Database | MongoDB |
| Documents/reports | ReportLab, openpyxl, xlrd, python-multipart |
| Optional WFH biometrics | face-recognition and MediaPipe, installed separately using `requirements-wfh.txt` |

Use the exact pinned Python packages in `backend/requirements.txt` and the committed `frontend/package-lock.json` when setting up the project.

## Prerequisites

Install and verify the following before starting:

1. **Python** compatible with the packages pinned in `backend/requirements.txt`.
2. **Node.js and npm** compatible with Vite 8.
3. **MongoDB** reachable from the backend (local MongoDB or an authorized hosted MongoDB deployment).
4. Optional: SMTP credentials for email-based account activation and password recovery.
5. Optional: the ESSL device/software/network configuration needed by your actual attendance deployment.
6. Optional: native/system dependencies required by `face-recognition` and MediaPipe if installing the WFH extras.

Check versions:

```powershell
python --version
py --version
node --version
npm --version
```

## Repository layout

Expected combined repository layout:

```text
HRMS-Project/
├── README.md
├── backend/
│   ├── requirements.txt
│   ├── requirements-wfh.txt
│   ├── app/
│   │   ├── main.py
│   │   ├── core_routes.py
│   │   ├── db/mongodb.py
│   │   ├── attendance.py
│   │   ├── essl.py
│   │   ├── leave.py
│   │   ├── payroll.py
│   │   ├── notifications.py
│   │   ├── employee_details.py
│   │   ├── reporting_manager.py
│   │   ├── activation.py
│   │   ├── password_reset.py
│   │   ├── security.py
│   │   ├── session.py
│   │   └── payslip_pdf.py
│   ├── scripts/check_wfh_mongodb.py
│   └── assets/
└── frontend/
    ├── README.md
    ├── package.json
    ├── package-lock.json
    ├── vite.config.js
    ├── public/
    └── src/
        ├── App.jsx
        ├── main.jsx
        ├── auth.js
        ├── config/api.js
        ├── components/
        ├── pages/
        ├── services/
        └── styles-*.css
```

If your current Git repository uses a different directory layout, adjust the commands and relative README links accordingly. Keep backend and frontend dependencies separate.

## Configuration

### 1. Backend environment file

Create `backend/.env` (do not commit it). At minimum, configure the MongoDB connection and database name:

```dotenv
MONGODB_URL=mongodb://127.0.0.1:27017
MONGODB_DATABASE=hrms
```

Use the URI supplied by your database administrator for hosted MongoDB, including credentials and TLS options where required. The database name is application-specific; choose the database used by your environment.

Optional settings recognized by the source include:

```dotenv
# Browser origins allowed to call the API; comma-separated list
CORS_ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173

# Organization/business timezone
ORG_TIMEZONE=Asia/Kolkata
COMPANY_TIMEZONE=Asia/Kolkata

# Optional company workday configuration (use the format expected by the attendance code)
COMPANY_START_TIME=09:00
WFH_ATTENDANCE_ENABLED=true

# Optional limits used by WFH verification
WFH_MAX_LOCATION_ACCURACY_METERS=100
WFH_MAX_LOCATION_AGE_SECONDS=300
WFH_FACE_MATCH_TOLERANCE=0.6

# Frontend URL used when generating links in email workflows
FRONTEND_URL=http://localhost:5173

# SMTP settings for email flows; use values provided by your mail administrator
SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM=

# Optional environment/seed behavior
HRMS_ENV=development
DEMO_USER_NAME=
DEMO_USER_EMAIL=
DEMO_USER_MOBILE=
DEMO_USER_ROLE=
DEMO_USER_PASSWORD=

# Optional encryption keys: supply securely generated, persistent keys
ESSL_CONFIG_ENCRYPTION_KEY=
WFH_TEMPLATE_ENCRYPTION_KEY=
```

**Important configuration notes**
- Do not commit `.env`, passwords, SMTP credentials, database URIs, API keys, encryption keys, private certificates, or real employee data.
- Use strong, random, persistent encryption keys in environments where encrypted ESSL settings or WFH biometric templates are stored. Changing a key can make previously encrypted values unreadable.
- Review the source code for the accepted time and boolean formats before changing values; examples above are illustrative defaults, not a substitute for checking your deployment requirements.
- `CORS_ALLOWED_ORIGINS` should contain the exact browser origins used by the frontend. Do not broadly allow untrusted origins in production.
- Never use demo credentials in production. If your deployment seeds a demo account, set a strong password and follow your internal account-creation process.

### 2. Frontend environment file

Create `frontend/.env.local` (do not commit secrets):

```dotenv
VITE_API_BASE_URL=http://127.0.0.1:8000
```

The frontend's shared API configuration defaults to `http://127.0.0.1:8000`. Vite also proxies `/api` to that backend during development. `VITE_` variables are compiled into browser-accessible code, so they must not contain secrets.

For a LAN test, configure `VITE_API_BASE_URL` to a backend address reachable from the testing device, then add the frontend origin to the backend CORS configuration. Restart Vite after editing environment variables.

## Run locally on Windows

Open two PowerShell terminals from the repository root.

### Terminal 1 — backend

# Used Python 3.12 in project

```powershell
cd backend

# Create a virtual environment
py -m venv .venv

# Activate it
.\.venv\Scripts\Activate.ps1

# If PowerShell blocks activation, for this terminal only:
# Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
# Then run the Activate.ps1 command again.

python -m pip install --upgrade pip
pip install -r requirements.txt
```

Create and fill in `backend/.env` as described above, then start the API:

```powershell
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Keep this terminal running. API documentation: <http://127.0.0.1:8000/docs>.

### Terminal 2 — frontend

```powershell
cd frontend
npm ci
npm run dev
```

Open <http://localhost:5173/>. Vite is configured for port `5173` and fails rather than silently switching ports if that port is already occupied.

## Run locally on macOS or Linux

### Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Create `backend/.env`, then run:

```bash
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### Frontend

In a separate terminal:

```bash
cd frontend
npm ci
npm run dev
```

Open <http://localhost:5173/>.

## Database initialization and health checks

The MongoDB module defines validation rules and idempotent collection-initialization helpers, and application startup uses these helpers. Confirm that the configured MongoDB service is reachable before starting the backend. Never point a local test run at production data unless explicitly authorized and protected by your organization's change process.

A read-only MongoDB connectivity smoke check is included:

```powershell
cd backend
python scripts/check_wfh_mongodb.py
```

The script reads `MONGODB_URL` and `MONGODB_DATABASE` from `backend/.env`, attempts a MongoDB ping, and verifies that the `users` and `attendance` collections exist. It does **not** create or modify data and is not a complete end-to-end API test.

After the backend starts, check:

- <http://127.0.0.1:8000/health> — backend health endpoint.
- <http://127.0.0.1:8000/docs> — interactive API documentation.
- <http://127.0.0.1:8000/openapi.json> — OpenAPI schema.

If startup fails, read the first traceback and verify environment variables, MongoDB connectivity, Python version, and installed dependencies.

## Frontend workflows

The frontend contains pages and shared components for:

- Authentication: login, activation, forgot password, OTP verification, password reset, and change password.
- Home: dashboard, profile, header, sidebar, and role-aware layout.
- Organization: company setup, organization setup, holiday calendar setup.
- People: employee master, employee details, user management, reporting-manager team members, and member details.
- Attendance: attendance views, employee attendance, user attendance, WFH attendance, WFH admin settings, ESSL setup, and ESSL admin settings.
- Leave: leave and permission workflows.
- Payroll: employee salary setup, payroll workspace, employee payslips.
- Notifications: notification bell/panel and notification management.
- WFH email verification and access-denied handling.

For component/page details, see [`frontend/README.md`](frontend/README.md).

## Backend API documentation

The FastAPI application registers routers for authentication and user administration, organization and employee data, attendance, ESSL attendance, WFH, reporting managers, notifications, leave/permissions, and payroll. The running OpenAPI document is the source of truth for the exact paths, parameters, schemas, and authorization requirements.

Important route groups visible in the source include:

| Area | Route prefix / examples |
|---|---|
| Health | `GET /health` |
| Authentication and users | `/api/auth/*`, `/api/users*` |
| Organization | `/api/organization/*`, `/api/companies*`, `/api/business-units*`, `/api/departments*`, `/api/teams*`, `/api/designations*` |
| Employees | `/api/employees*`, `/api/employee-details/*` |
| Attendance | `/api/attendance/*` |
| ESSL attendance | `/api/attendance/essl/*` |
| Reporting manager | `/api/reporting-manager/*` |
| Notifications | `/api/notifications/*` |
| Leave and permissions | `/api/leave/*` |
| Payroll and payslips | `/api/payroll/*` |
| Password recovery | `/api/auth/*` (including recovery/OTP/reset routes) |

Do not assume all endpoints are public. Consult `/docs` and the route implementation for the required authentication headers, roles, request bodies, and response formats.

## Attendance and ESSL device integration

The backend includes an ESSL attendance router at `/api/attendance/essl` with endpoints for:
- Reading and updating ESSL integration configuration.
- Testing a connection.
- Importing an attendance file.
- Triggering synchronization.
- Viewing synchronization history and issues.
- Managing employee mappings used by the integration.

This confirms that the application has an ESSL integration workflow in code. It does **not**, by itself, prove that every ESSL model can communicate directly with this backend or that real-time push/pull from a particular device is configured.

For an eSSL AiFace Magnum deployment, verify the device firmware/protocol, network reachability, vendor SDK/API access, and whether the installed eTimeTrackLite service/database is the supported integration source. Test against a staging device and inspect `/docs` plus `app/essl.py` before treating it as a live real-time integration. The current code also supports file import, which can be used where direct device communication is not available. Do not expose the device or database directly to the public internet; prefer a secured LAN connector/service with authentication and logging.

## WFH attendance and biometrics

WFH attendance endpoints are implemented under `/api/attendance`. The source includes check-in/check-out, breaks, home-location registration/verification, approved locations, face challenge/registration, email/QR fallback flows, and admin configuration.

- Use HTTPS in real deployments, particularly for browser camera and geolocation features.
- Configure the browser's camera/location permissions and test with the exact hostname/IP users will access.
- The optional biometric requirements are separate from the base backend requirements and may need OS-specific native build dependencies.
- Obtain employee consent and follow applicable privacy, retention, access-control, and employment policies before collecting location or biometric data.
- Validate the anti-spoofing, location-accuracy, and face-match behavior in controlled tests before relying on it for payroll decisions.

## Payroll workflow

The payroll backend exposes salary-profile, attendance-summary, payroll-record, calculation/action, payslip, and salary-history routes. The frontend provides employee salary setup, payroll workspace, and employee payslips.

Recommended operational flow:
1. Confirm employee records and salary profiles are correct.
2. Verify attendance and leave data for the payroll period.
3. Review the attendance summary and configured salary components.
4. Calculate payroll in a test period and inspect totals and exceptions.
5. Review/approve records through the available payroll actions.
6. Verify generated payslips and access permissions before distributing them.

Payroll rules are organization-specific. Validate statutory deductions, rounding, overtime, unpaid leave, and tax treatment with the organization's payroll owner before using results for actual salary payments.

## Security and deployment

- Serve production frontend and backend over HTTPS.
- Keep secrets in deployment environment variables or a secrets manager; never put secrets in frontend variables.
- Restrict MongoDB network access and use a least-privilege database account.
- Restrict CORS to trusted frontend origins.
- Ensure server-side authorization is enforced for every protected operation; frontend route hiding is only a usability feature.
- Use strong session/password controls and rotate exposed credentials immediately.
- Protect exported attendance spreadsheets, payslip PDFs, logs, and backups as confidential employee data.
- Back up MongoDB and test restoration procedures.
- Configure SMTP with authenticated TLS according to your provider's requirements.
- Do not commit `.env`, `.venv/`, `node_modules/`, `dist/`, private keys/certificates, or real employee exports.
- Before deployment, review debug/reload settings, logging, error responses, demo/seed behavior, database validators/indexes, and encryption-key management.

## Troubleshooting

| Symptom | Checks |
|---|---|
| Backend cannot start | Confirm Python version, activate `.venv`, reinstall `requirements.txt`, and read the first traceback. |
| MongoDB server selection timeout | Check service status, `MONGODB_URL`, DNS/firewall rules, credentials, TLS, and hosted database IP allowlist. |
| Missing `users` or `attendance` collections | Verify `MONGODB_DATABASE` points to the intended database and complete normal app initialization. Run the read-only smoke check again. |
| Frontend cannot call API | Check backend is running on port `8000`, `VITE_API_BASE_URL`, browser network errors, and CORS origins. |
| Port `5173` is occupied | Stop the process using it or intentionally update the Vite configuration and documentation together. |
| Login works but a route returns 403 | Inspect the signed-in role and backend authorization logic; client navigation permissions are not authoritative. |
| Activation/reset email is not received | Verify SMTP host/port/user/password/from address, provider TLS requirements, spam folder, and `FRONTEND_URL`. |
| WFH camera/location fails | Use HTTPS with a valid certificate and grant browser permissions; verify device capabilities and configured accuracy limits. |
| ESSL sync/import fails | Inspect ESSL configuration, network/vendor service availability, employee mapping, file format, sync history, and issues. Check `/docs` and backend logs. |
| Payslip or spreadsheet generation fails | Check required Python packages, input records, file permissions, and backend traceback. |
| Frontend build fails | Run `npm ci`, then `npm run lint` and `npm run build`; fix the first relevant error before retrying. |

## Validation checklist

Before merging or releasing:

```powershell
# Backend terminal (with virtual environment active)
python -m compileall app
python scripts/check_wfh_mongodb.py

# Frontend terminal
npm ci
npm run lint
npm run build
```

The MongoDB smoke check requires a configured, reachable database. `compileall` checks Python syntax but does not replace tests or integration testing. The supplied project does not include a comprehensive automated test suite in the provided ZIPs, so validate important workflows manually in a non-production environment.

Suggested end-to-end checks:
- [ ] Backend health endpoint responds.
- [ ] Login, logout, activation, password recovery, and change-password flows work.
- [ ] Role permissions are enforced by backend APIs.
- [ ] Employee and organization records can be read/managed by authorized roles.
- [ ] Attendance data imports/synchronizes correctly and duplicate/error cases are reviewed.
- [ ] WFH check-in/out and fallback flows are tested with approved devices and permissions.
- [ ] Leave request, approval, rejection, and balance updates are verified.
- [ ] Notifications appear for the intended recipients.
- [ ] Payroll calculation is reconciled against a known test case.
- [ ] Payslip PDF and attendance export access is restricted appropriately.
- [ ] Frontend lint and production build pass.

## Related documentation

- Frontend setup, components, workflows, and build commands: [`frontend/README.md`](frontend/README.md)
- Live API reference: <http://127.0.0.1:8000/docs>
