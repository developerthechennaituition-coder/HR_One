# HRMS — Human Resource Management System

A full-stack Human Resource Management System built with a React 19 + Vite frontend, a Python FastAPI backend, and MongoDB. The application brings employee administration, organization setup, attendance, leave and permission workflows, notifications, payroll, and payslip access into one role-aware system.

This README describes the source supplied in the current project archives. Integrations such as ESSL devices, SMTP delivery, and WFH biometric verification require the corresponding external configuration and should be validated in the target environment before production use.

## Contents

- [Features](#features)
- [Technology and architecture](#technology-and-architecture)
- [Repository layout](#repository-layout)
- [Prerequisites](#prerequisites)
- [Configuration](#configuration)
- [Run locally on Windows](#run-locally-on-windows)
- [Run locally on macOS or Linux](#run-locally-on-macos-or-linux)
- [API documentation and health checks](#api-documentation-and-health-checks)
- [Roles and access](#roles-and-access)
- [Functional modules](#functional-modules)
- [Attendance and ESSL integration](#attendance-and-essl-integration)
- [Leave, holidays, and permissions](#leave-holidays-and-permissions)
- [Payroll and payslips](#payroll-and-payslips)
- [Tests and quality checks](#tests-and-quality-checks)
- [Git and environment-file guidance](#git-and-environment-file-guidance)
- [Troubleshooting](#troubleshooting)
- [Production-readiness checklist](#production-readiness-checklist)

## Features

### Authentication and user administration
- Login, logout, session-aware frontend behavior, and password change.
- Account activation and password recovery/OTP routes.
- User administration, account status, roles, and permissions.
- Role-aware navigation and backend authorization checks.

### Organization and employee administration
- Company and organization setup.
- Business units, departments, teams, and designations.
- Employee master records and extended employee details.
- Reporting-manager/HOD assignment and team-member views.
- Religion-based holiday calendar workflows.

### Attendance
- Daily and monthly attendance views, employee attendance details, and Excel export.
- Employee attendance calendar and daily working-time/break details.
- Attendance requests for corrections, with review/approval workflow.
- Work-from-home check-in/check-out and related controls.
- Attendance history and administrative attendance tools present in the supplied frontend/backend source.
- Joining-date and date-window rules are enforced in relevant attendance workflows; confirm exact rules in the current implementation before changing business policy.

### ESSL attendance integration
- ESSL configuration and administration screens.
- Backend endpoints and import/synchronization workflows for attendance data.
- Device connectivity, credentials, network reachability, device model compatibility, and vendor API/SDK access must be configured and tested separately. The presence of integration code does not mean a physical device is already connected.

### Leave, holidays, and permissions
- Leave types, policies, balances, requests, approvals, rejections, and cancellations.
- Holiday calendar management and employee holiday views.
- Permission request and approval workflows.
- Leave and attendance integration implemented in backend business logic.

### Notifications
- In-app notification APIs and frontend notification components.
- Notification list/panel and notification sound asset.
- Email notifications depend on SMTP configuration and the specific workflow.

### Payroll and payslips
- Employee salary setup and payroll workspace.
- Payroll calculation/review APIs and records.
- Payslip PDF generation and employee payslip viewing.
- Payroll results depend on attendance, leave, holiday, and salary data being correctly configured.

## Technology and architecture

| Layer | Technology |
|---|---|
| Frontend | React 19, React DOM, Vite 8, JavaScript |
| UI | Bootstrap 5, Bootstrap Icons, project CSS |
| Frontend utilities | libphonenumber-js, qrcode.react, pdfjs-dist |
| Linting | Oxlint |
| Backend | Python, FastAPI, Pydantic, Uvicorn |
| Database | MongoDB via PyMongo |
| Reports/documents | openpyxl, xlrd, ReportLab |
| Optional WFH biometric packages | face-recognition, MediaPipe (see `requirements-wfh.txt`) |

```text
Web browser
   |
   | HTTP(S), /api requests
   v
React + Vite frontend (default development port 5173)
   |
   v
FastAPI backend (default port 8000)
   |
   v
MongoDB
   |
   +-- Optional SMTP server for configured email workflows
   +-- Optional ESSL device/software/network integration
```

## Repository layout

The Git repository should contain both application folders and the root README:

```text
HRMS-Project/
├── README.md
├── backend/
│   ├── .env.example
│   ├── requirements.txt
│   ├── requirements-wfh.txt
│   ├── app/
│   │   ├── main.py
│   │   ├── core_routes.py
│   │   ├── db/mongodb.py
│   │   ├── attendance.py
│   │   ├── attendance_requests.py
│   │   ├── employee_details.py
│   │   ├── essl.py
│   │   ├── leave.py
│   │   ├── notifications.py
│   │   ├── payroll.py
│   │   ├── payslip_pdf.py
│   │   ├── religion_holidays.py
│   │   ├── reporting_manager.py
│   │   ├── activation.py
│   │   ├── password_reset.py
│   │   ├── security.py
│   │   ├── session.py
│   │   └── wfh_biometrics.py
│   ├── scripts/
│   ├── tests/
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
        ├── components/
        ├── pages/
        ├── services/
        └── config/
```

## Prerequisites

Install the following:

1. Python 3.12 is the version used by the supplied project environment; check compatibility with `backend/requirements.txt` before selecting another Python version.
2. Node.js compatible with Vite 8 and npm.
3. A reachable MongoDB instance (local or hosted).
4. Git.
5. Optional SMTP account for configured email delivery.
6. Optional ESSL device/software/network access for device integration.
7. Optional native build/runtime prerequisites for WFH biometric dependencies.

Verify versions:

```powershell
python --version
node --version
npm --version
git --version
```

## Configuration

### Backend environment

1. Copy `backend/.env.example` to `backend/.env`.
2. Set the MongoDB connection string and database.
3. Configure only the integrations used by your environment.
4. Keep secrets out of Git.

Important variables documented in the supplied `.env.example`:

| Variable | Purpose |
|---|---|
| `MONGODB_URL` | MongoDB connection URI |
| `MONGODB_DATABASE` | Database name; example defaults to `hrms_db` |
| `ORG_TIMEZONE` | Organization timezone; example uses `Asia/Kolkata` |
| `FRONTEND_URL` | Frontend base URL used by configured account/email flows |
| `SMTP_HOST`, `SMTP_PORT` | SMTP server settings |
| `SMTP_USERNAME`, `SMTP_PASSWORD` | SMTP credentials |
| `SMTP_FROM` | Sender address |
| `CORS_ALLOWED_ORIGINS` | Comma-separated allowed frontend origins |
| `WFH_ATTENDANCE_ENABLED` | Enables/disables WFH attendance endpoints where checked |
| `WFH_TEMPLATE_ENCRYPTION_KEY` | Encryption key for biometric templates, if enabled |
| `ESSL_CONFIG_ENCRYPTION_KEY` | Encryption key for stored ESSL configuration, if enabled |

Generate a Fernet key where required:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Use a stable, private key for existing encrypted records. Do not replace an encryption key casually after storing encrypted data.

### Frontend environment

The frontend reads `VITE_API_BASE_URL` from its environment. The source default is `http://127.0.0.1:8000`.

Create `frontend/.env.local`:

```dotenv
VITE_API_BASE_URL=http://127.0.0.1:8000
```

For LAN/mobile testing, use the backend host reachable by the device, and configure backend CORS accordingly. Restart Vite after changing environment variables.

## Run locally on Windows

Open two PowerShell terminals.

### 1. Backend

```powershell
cd F:\HRMS-Project\backend

py -3.12 -m venv venv312
.\venv312\Scripts\Activate.ps1

python -m pip install --upgrade pip
pip install -r requirements.txt

# Create backend\.env from backend\.env.example and configure it first.
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

If PowerShell blocks virtual-environment activation, use the permitted execution policy for your workstation or invoke the environment's Python directly:

```powershell
.\venv312\Scripts\python.exe -m pip install -r requirements.txt
.\venv312\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 2. Frontend

```powershell
cd F:\HRMS-Project\frontend
npm ci
npm run dev
```

Open the URL printed by Vite, normally `http://localhost:5173`.

For an existing checkout where dependencies have changed and no lockfile-compatible install is possible, `npm install` can be used. Prefer `npm ci` for repeatable clean installs.

## Run locally on macOS or Linux

### Backend

```bash
cd HRMS-Project/backend
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
# Create .env from .env.example and configure MongoDB.
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Frontend

```bash
cd HRMS-Project/frontend
npm ci
npm run dev
```

## API documentation and health checks

With the backend running:

- Swagger UI: `http://127.0.0.1:8000/docs`
- OpenAPI schema: `http://127.0.0.1:8000/openapi.json`

Use Swagger UI to inspect the actual registered routes, request schemas, and response models for the checked-out version. Do not assume that an endpoint exists based only on a screen label.

## Roles and access

The application contains role-aware frontend navigation and backend authorization. The supplied project identifies roles including HR Admin, Administrator, HR Manager/HOD, and Employee. The frontend role-label helper may display the stored `HR Manager` role as `HOD`.

Typical responsibility boundaries (subject to the permissions configured in the running database and backend route checks):

| Role | Typical responsibility |
|---|---|
| HR Admin | Organization/employee administration, attendance and leave administration, payroll administration, user/role management |
| Administrator | Administrative access permitted by backend authorization |
| HOD / HR Manager | Department/team oversight and approval responsibilities assigned by the application |
| Employee | Self-service attendance, leave/permission requests, notifications, and payslips |

The backend is the authorization source of truth. Hiding a button in the frontend is not a security control. When adding a permission, verify the role identifier and backend permission check as well as the frontend display condition.

## Functional modules

| Module | Main frontend entry points | Backend modules |
|---|---|---|
| Authentication | `Login.jsx`, `ActivateAccount.jsx`, `ForgotPassword.jsx`, `VerifyResetOTP.jsx`, `ResetPassword.jsx`, `ChangePassword.jsx` | `main.py`, `activation.py`, `password_reset.py`, `security.py`, `session.py` |
| Organization setup | `CompanySetup.jsx`, `OrganizationSetup.jsx`, `HolidayCalendarSetup.jsx` | `core_routes.py`, `religion_holidays.py`, `db/mongodb.py` |
| Employee management | `EmployeeMaster.jsx`, `EmployeeDetails.jsx`, `UserManagement.jsx` | `employee_details.py`, `core_routes.py`, `reporting_manager.py` |
| Attendance | `Attendance.jsx`, `EmployeeAttendance.jsx`, `UserAttendance.jsx`, `WfhAttendance.jsx` | `attendance.py`, `attendance_requests.py` |
| ESSL | `EsslSetup.jsx`, `EsslAdminSettings.jsx` | `essl.py` |
| Leave and permissions | `Leave.jsx` | `leave.py`, `attendance_requests.py` |
| Notifications | `NotificationManagement.jsx`, `NotificationBell.jsx`, `NotificationPanel.jsx` | `notifications.py` |
| Payroll | `PayrollWorkspace.jsx`, `EmployeeSalarySetup.jsx`, `EmployeePayslips.jsx` | `payroll.py`, `payslip_pdf.py` |
| Team views | `TeamMembers.jsx`, `TeamMemberDetails.jsx` | `reporting_manager.py` |

## Attendance and ESSL integration

Attendance data can originate from application attendance workflows and configured device import/synchronization workflows. The ESSL module includes configuration and synchronization code, but successful live device integration depends on the exact device model/firmware, vendor interface or SDK, network configuration, credentials, and mapping of device user identifiers to HRMS employees.

Before enabling production synchronization:

1. Confirm the device's supported integration method with the vendor.
2. Configure network reachability and credentials securely.
3. Map each device user ID to the correct HRMS employee.
4. Test with a small date range and a limited set of employees.
5. Check duplicate-punch handling, timezone interpretation, missing punches, and joining-date boundaries.
6. Reconcile imported records against the source device/software before payroll use.

Do not import a full production history until a backup and reconciliation procedure is in place.

## Leave, holidays, and permissions

Leave rules can depend on leave type, configured entitlement, approval routing, employee data, holidays, and attendance records. Holiday calendars and weekly-off settings can affect working-day calculations.

Before changing leave policy, verify:

- Leave type codes and paid/unpaid classification.
- Monthly versus annual entitlement behavior.
- Accrual and carry-forward rules, if configured.
- Eligibility and probation/work-stage rules, if configured.
- Approval routing and role assignments.
- Holiday and weekly-off calculations.
- LOP/payroll treatment.
- Existing employee balances and historical leave records.

Do not manually edit production leave balances without understanding the corresponding calculation and audit behavior.

## Payroll and payslips

Payroll uses employee salary information and attendance/leave inputs. Review salary components, payable days, unpaid leave/LOP, approved payroll state, and payslip output before finalizing a payroll period.

Recommended payroll cycle:

1. Verify employee master data and salary profiles.
2. Reconcile attendance and approved attendance corrections.
3. Verify approved leave, holidays, and weekly offs.
4. Review payable days and LOP.
5. Calculate and review payroll.
6. Approve/finalize according to the organization's workflow.
7. Verify generated payslips and employee access.

Historical attendance or leave changes may affect payroll reconciliation. Follow the application's approval and recalculation workflow rather than editing payroll records directly in MongoDB.

## Tests and quality checks

The backend archive includes tests under `backend/tests/`. Run them from the backend directory with the project's test dependencies installed:

```powershell
cd backend
python -m pytest -q
```

If pytest is not listed in the runtime requirements, install it in the development environment:

```powershell
python -m pip install pytest
```

Frontend scripts are defined in `frontend/package.json`:

```powershell
cd frontend
npm ci
npm run lint
npm run build
```

A successful build verifies compilation; it does not replace API, database, browser, payroll, or end-to-end testing.

## Git and environment-file guidance

Before committing:

```powershell
git status
git add README.md frontend/README.md
git commit -m "Update project documentation"
git push origin main
```

Commit source code, lockfiles, `.env.example`, and documentation. Do **not** commit:

- `.env`, `.env.local`, production secrets, API keys, SMTP passwords, MongoDB credentials, or encryption keys.
- Python virtual environments (`venv/`, `venv312/`, `.venv/`).
- `node_modules/`, generated `dist/`, caches, or `__pycache__/`.
- Local database dumps or employee personal data unless explicitly approved and securely handled.

Review `.gitignore` before committing. If a secret was previously committed, removing it from the latest commit is not enough; rotate the exposed credential.

## Troubleshooting

### Backend fails during startup

- Activate the intended virtual environment.
- Install `backend/requirements.txt`.
- Read the first exception in the traceback; later ASGI frames are usually propagation.
- Check import names and constants referenced across modules.
- Confirm `.env` exists and MongoDB configuration is valid.

### MongoDB connection or data errors

- Confirm MongoDB is running and reachable.
- Verify `MONGODB_URL` and `MONGODB_DATABASE`.
- Check database user permissions and network allowlists.
- Do not expose connection strings in screenshots or Git commits.

### Frontend cannot reach the API

- Confirm the backend is running on port 8000.
- Check `VITE_API_BASE_URL` in `frontend/.env.local`.
- Check CORS settings and browser developer-console/network errors.
- Restart Vite after changing environment variables.

### Excel export returns HTTP 500

- Read the backend traceback and identify the first application-code frame.
- Confirm report dependencies such as `openpyxl` are installed.
- Fix missing imports or data assumptions in the named endpoint; do not treat every HTTP 500 as a database problem.

### ESSL sync returns no records

- Verify device connectivity and vendor integration settings.
- Confirm employee/device ID mappings and date/timezone settings.
- Inspect sync history and skipped/invalid record details.
- Test a small date range first.

## Production-readiness checklist

- [ ] Production MongoDB URI and least-privilege database user configured.
- [ ] Secrets stored outside Git and rotated if previously exposed.
- [ ] Stable encryption keys backed up securely.
- [ ] HTTPS configured for frontend and backend.
- [ ] CORS restricted to trusted frontend origins.
- [ ] SMTP tested if email workflows are enabled.
- [ ] HR Admin, Administrator, HOD, and Employee permissions tested.
- [ ] Attendance, leave, holidays, and payroll reconciled with business owners.
- [ ] ESSL integration tested against the actual device and vendor interface.
- [ ] Database backups and restore procedure tested.
- [ ] Backend tests, frontend lint/build, and end-to-end workflows passed.
- [ ] Monitoring and error logging configured without exposing sensitive employee data.

---

For frontend-specific setup, page structure, scripts, and development details, see [`frontend/README.md`](frontend/README.md).
