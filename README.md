# HRMS — Human Resource Management System

Company HRMS web application built with **React + Vite**, **Python + FastAPI**, and **MongoDB**. The repository contains the frontend and backend source code for employee, organization, attendance, leave, notification, and payroll workflows.

> **Project status:** Active development. Review the implemented application and confirm company policies, environment configuration, authorization, and operational requirements before production deployment.

## Contents

- [Technology stack](#technology-stack)
- [Repository structure](#repository-structure)
- [Features currently represented in the codebase](#features-currently-represented-in-the-codebase)
- [Prerequisites](#prerequisites)
- [Local development setup](#local-development-setup)
- [Environment configuration](#environment-configuration)
- [Run the application](#run-the-application)
- [Tests and quality checks](#tests-and-quality-checks)
- [Attendance integrations](#attendance-integrations)
- [Security and data handling](#security-and-data-handling)
- [Git workflow](#git-workflow)
- [Troubleshooting](#troubleshooting)

## Technology stack

| Area | Technology |
|---|---|
| Frontend | React 19, Vite 8, JavaScript |
| UI | HTML, CSS, Bootstrap 5, Bootstrap Icons |
| Frontend checks | Oxlint |
| Backend | Python, FastAPI, Uvicorn, Pydantic |
| Database | MongoDB, PyMongo |
| Authentication | Application session token sent as a Bearer token |
| Reports and files | ReportLab, openpyxl, xlrd, python-multipart |
| Optional WFH face processing | `face-recognition`, MediaPipe (see `backend/requirements-wfh.txt`) |
| Source control | Git, GitHub |

## Repository structure

```text
HRMS-Project/
├── README.md
├── .gitignore
├── frontend/
│   ├── README.md
│   ├── package.json
│   ├── package-lock.json
│   ├── vite.config.js
│   ├── public/
│   └── src/
│       ├── components/
│       ├── config/
│       ├── pages/
│       ├── services/
│       ├── App.jsx
│       └── main.jsx
└── backend/
    ├── requirements.txt
    ├── requirements-wfh.txt
    ├── app/
    │   ├── main.py
    │   ├── attendance.py
    │   ├── essl.py
    │   ├── leave.py
    │   ├── notifications.py
    │   ├── payroll.py
    │   ├── core_routes.py
    │   ├── employee_details.py
    │   ├── reporting_manager.py
    │   ├── password_reset.py
    │   ├── activation.py
    │   ├── wfh_biometrics.py
    │   └── db/mongodb.py
    ├── assets/
    ├── scripts/
    └── tests/                  # present when test files are included
```

Do not commit Python virtual environments, `node_modules`, local `.env` files, database exports, employee records, or private keys.

## Features currently represented in the codebase

The following feature areas have frontend pages and/or backend API modules in the supplied project source. The exact workflow and access depend on the implementation and the signed-in user's role.

- **Authentication and account access:** login, account activation, password change, forgot-password flow, OTP verification, and password reset.
- **Company / Legal Entity:** create, list, view, update, and deactivate company records. The current backend includes a single-primary-company rule.
- **Organization setup:** business units, departments, teams, designations, employee reporting hierarchy, and employee profile organization information.
- **Holiday calendar:** calendar setup, holiday entries, draft/publish and cancellation/amendment workflows, employee calendar assignment, and related audit/reminder logic.
- **Employee and user administration:** employee master/details, user management, account activation/status, and role-aware UI.
- **Attendance:** daily attendance views, employee attendance, attendance summaries, remarks, and attendance export endpoints.
- **Work From Home (WFH):** employee location registration/verification, approved locations, optional face-template registration and challenge/liveness checks, check-in/check-out, breaks, and HR-admin settings. Optional face-processing dependencies are maintained separately.
- **ESSL attendance integration:** configurable REST/JSON endpoint, connection test, employee ID mapping, file import, date-range sync, sync history, and issue review. This implementation expects an HTTP JSON endpoint that returns the configured punch data; it is not, by itself, proof of direct device-SDK or guaranteed real-time device connectivity.
- **Leave and permissions:** leave types/policies, leave balance and requests, approvals/rejections/cancellations, permission requests, dashboards, reports, CSV export, and leave-related notifications.
- **Notifications:** notification listing, unread count/snapshot/lookups, view/read/acknowledge actions, starring, hold/resume, and leave-notification actions.
- **Payroll:** employee salary profiles, salary history, payroll calculation and review actions, attendance summary, payslip listing, PDF view/download, and employee self-service payslips.
- **Reporting manager workflows:** team-member listing and details, plus team attendance endpoints.

These are codebase-level descriptions, not a statement that every workflow has been accepted by the business or is production-ready. Verify permissions, edge cases, and company policy before release.

## Prerequisites

Install the following on your Windows development computer:

1. **Git for Windows**
2. **Node.js**, with npm (use a version compatible with the Vite version in `frontend/package.json`)
3. **Python** compatible with the backend dependencies. Python 3.12 is a practical option for the optional WFH face-processing packages.
4. **MongoDB** running locally or at an authorized reachable database host.
5. A code editor such as Visual Studio Code.

## Local development setup

Run commands from PowerShell unless otherwise stated.

### 1. Get the project

If cloning from GitHub:

```powershell
git clone https://github.com/developerthechennaituition-coder/HR_One.git
cd HR_One
```

If you already have the project at `F:\HRMS-Project`, open PowerShell in that directory instead.

### 2. Configure the backend environment

Create a virtual environment and install the main dependencies:

```powershell
cd backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Create `backend/.env` locally. Do not commit it. Start from the names below and adjust values to your own local environment and the options used by your code:

```dotenv
MONGODB_URL=mongodb://127.0.0.1:27017
MONGODB_DATABASE=hrms_db
CORS_ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
ORG_TIMEZONE=Asia/Kolkata
COMPANY_TIMEZONE=Asia/Kolkata
FRONTEND_URL=http://localhost:5173
```

Other workflows may require additional variables, including SMTP settings, `ESSL_CONFIG_ENCRYPTION_KEY`, `WFH_TEMPLATE_ENCRYPTION_KEY`, WFH feature settings, and company schedule settings. Check the relevant backend module before enabling those features. Use private, securely generated keys and credentials; never paste real values into documentation or source control.

Start MongoDB before starting the API.

### 3. Configure the frontend

Open a second terminal:

```powershell
cd frontend
npm ci
```

The API base URL defaults to `http://127.0.0.1:8000`. To use a different backend host, create `frontend/.env.local`:

```dotenv
VITE_API_BASE_URL=http://127.0.0.1:8000
```

Only use `VITE_` variables for values that are safe to expose to browser clients. Never put passwords, private keys, database credentials, or server secrets in frontend environment variables.

### 4. Optional local HTTPS

The Vite configuration checks for certificate files in a root-level `certs/` directory:

```text
HRMS-Project/
└── certs/
    ├── localhost.pem
    └── localhost-key.pem
```

If both files exist, Vite enables HTTPS. The private key and local certificates should remain out of GitHub. If the files are absent, the Vite development server uses HTTP. Use a certificate whose subject alternative names match the hostname or IP address used by the browser.

## Run the application

### Terminal 1 — Backend

From the repository root:

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload
```

Backend base URL: <http://127.0.0.1:8000>

- API documentation: <http://127.0.0.1:8000/docs>
- OpenAPI schema: <http://127.0.0.1:8000/openapi.json>
- Health check: <http://127.0.0.1:8000/health>

### Terminal 2 — Frontend

```powershell
cd frontend
npm run dev
```

Open <http://localhost:5173/> (or the exact URL printed by Vite). Keep both terminals running during local development.

If PowerShell blocks `npm.ps1`, use a process-scoped policy for the current terminal only:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
npm run dev
```

## Environment configuration

Configuration values are local and environment-specific. Common variables referenced by the backend include:

| Variable | Purpose |
|---|---|
| `MONGODB_URL` | MongoDB connection URL |
| `MONGODB_DATABASE` | Database name (local default used in project docs: `hrms_db`) |
| `CORS_ALLOWED_ORIGINS` | Comma-separated allowed frontend origins |
| `CORS_ALLOWED_ORIGIN_REGEX` | Optional CORS origin regex override |
| `ORG_TIMEZONE` | Organization/holiday timezone; code defaults to `Asia/Kolkata` |
| `COMPANY_TIMEZONE` | Company attendance timezone |
| `FRONTEND_URL` | Frontend URL used by account/attendance email flows |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM` | SMTP mail configuration |
| `ESSL_CONFIG_ENCRYPTION_KEY` | Private Fernet key for protecting stored ESSL configuration credentials |
| `WFH_TEMPLATE_ENCRYPTION_KEY` | Private Fernet key for stored WFH face templates |
| `WFH_ATTENDANCE_ENABLED` | Enables/disables the WFH attendance feature |
| `WFH_MAX_LOCATION_ACCURACY_METERS`, `WFH_MAX_LOCATION_AGE_SECONDS` | WFH location validation controls |
| `COMPANY_START_TIME` | Company schedule setting used by attendance logic |
| `HRMS_ENV` | Environment mode used by selected backend workflows |

Not every variable is required to start every feature. Check the source modules and provide values appropriate to the environment. Keep production credentials in a secure secret store or server-side environment configuration.

## Tests and quality checks

### Frontend

```powershell
cd frontend
npm run lint
npm run build
```

### Backend

The repository's backend package includes unittest-based tests where test files are present. Run the suite from `backend`:

```powershell
python -m unittest discover -s tests -v
```

If your current checkout does not contain a `tests` directory, no tests will be discovered; add or restore the project test files before relying on this command as a verification gate.

### Optional WFH dependencies

The additional biometric packages are in `backend/requirements-wfh.txt`. They are not installed by the main requirements file:

```powershell
py -3.12 -m venv .venv-wfh
.\.venv-wfh\Scripts\python.exe -m pip install -r requirements.txt
.\.venv-wfh\Scripts\python.exe -m pip install -r requirements-wfh.txt
```

These packages can require native build tools on Windows. Install and test this optional environment separately; do not commit it.

## Attendance integrations

### Work From Home

WFH uses the existing attendance API and common attendance collection. The backend validates the reported location against configured approved locations and applies face/liveness checks when the optional feature is enabled. Browser-reported GPS data is not hardware-attested and can be spoofed on compromised devices. The lightweight liveness checks are not a certified anti-spoofing system.

### Work From Office — ESSL

The current ESSL module supports configuring an ESSL-compatible **REST/JSON endpoint** and mapping HRMS employees to the employee identifiers returned by that endpoint. Administrators can test the connection, synchronize a date range, inspect sync history, and review unmapped/invalid punch issues. The endpoint must return the required punch data in the configured response shape; XML, automatic pagination, proprietary protocols, or direct SDK/DLL communication require a separate adapter or integration.

Before enabling ESSL configuration, set a private `ESSL_CONFIG_ENCRYPTION_KEY`. Do not assume that the current REST configuration alone provides real-time device push events. Confirm the actual vendor API, device model support, license, polling/webhook behavior, and security requirements with the vendor.

## Security and data handling

- Keep the repository private unless the company explicitly approves public source release.
- Never commit `.env`, real credentials, access tokens, private certificates/keys, employee personal data, biometric data, database dumps, or production logs.
- Keep `backend/.env` and `frontend/.env.local` on the local machine; use example files containing placeholders if documentation is needed.
- Rotate any credential that was accidentally committed. Removing it from the latest file does not remove it from Git history.
- Restrict database/network access and use backups before schema or index changes.
- Configure CORS to the exact frontend origin(s) used in each environment.
- Review backend authorization and role restrictions; frontend-only navigation restrictions are not a security boundary.
- WFH face templates and ESSL credentials use encryption keys. Back up keys securely; losing them may make encrypted values unreadable.
- Validate security, privacy, retention, consent, and company-policy requirements before production use.

## Git workflow

Check status and review changes before committing:

```powershell
git status
git diff
git add -A
git diff --cached
git commit -m "Describe the change"
git push
```

The `.gitignore` should exclude local environments, dependencies, build outputs, secrets, and private keys. Before each push, review the staged file list and ensure no sensitive or oversized generated files are included.

## Troubleshooting

| Problem | Checks |
|---|---|
| Frontend cannot reach backend | Confirm Uvicorn is running, `VITE_API_BASE_URL` is correct, CORS allows the frontend origin, and the browser console shows no network errors. |
| API cannot connect to MongoDB | Confirm MongoDB is running and `MONGODB_URL` / `MONGODB_DATABASE` are correct. |
| Login or protected API returns 401/403 | Sign in again, check the stored session/token, and verify the account role and backend authorization rules. |
| HTTPS/certificate warning | Ensure the certificate covers the exact hostname/IP used, and that both certificate and key exist in the expected local `certs/` directory. |
| WFH biometric package install fails | Use a compatible Python version (Python 3.12 is documented for the optional MediaPipe environment) and review native build prerequisites for `dlib`. |
| GitHub rejects a push due to large files | Exclude virtual environments and generated dependencies with `.gitignore`; if already committed, remove them from Git history before pushing again. |

## Documentation

- Frontend setup and scripts: [`frontend/README.md`](frontend/README.md)
- FastAPI interactive API documentation while the backend is running: <http://127.0.0.1:8000/docs>
