# HRMS — Human Resource Management System

HRMS is a full-stack Human Resource Management System. The supplied source is organized as a React 19 + Vite frontend and a Python FastAPI backend using MongoDB for persistence.

This document describes the project files supplied for the frontend and backend, their setup, configuration, and local execution. Confirm deployment-specific values (database URL, email service, certificates, and network addresses) with the system administrator before running in a shared or production environment.

## Contents

- [System overview](#system-overview)
- [Technology stack](#technology-stack)
- [Application features](#application-features)
- [Repository layout](#repository-layout)
- [Prerequisites](#prerequisites)
- [MongoDB setup](#mongodb-setup)
- [Backend setup](#backend-setup)
- [Frontend setup](#frontend-setup)
- [Environment configuration](#environment-configuration)
- [Run the application](#run-the-application)
- [API documentation](#api-documentation)
- [Testing and quality checks](#testing-and-quality-checks)
- [Deployment notes](#deployment-notes)
- [Security and operational notes](#security-and-operational-notes)
- [Troubleshooting](#troubleshooting)

## System overview

```text
Browser (React + Vite)
        |
        | REST requests under /api
        v
FastAPI application (Uvicorn)
        |
        v
MongoDB
```

During local frontend development, Vite proxies `/api` requests to the FastAPI server. The frontend stores the signed-in session in browser `sessionStorage` and sends the session token as a Bearer token for authenticated API calls. The backend owns API handling, authorization checks, business rules, and MongoDB access.

## Technology stack

| Layer | Technologies found in the supplied source |
|---|---|
| Frontend | React 19, React DOM 19, Vite 8, JavaScript/JSX |
| UI | Bootstrap 5, Bootstrap Icons, project CSS |
| Frontend utilities | `libphonenumber-js`, `qrcode.react`, `pdfjs-dist` |
| Backend | Python, FastAPI, Pydantic 2, Uvicorn, Starlette |
| Database | MongoDB through PyMongo; `dnspython` is included for DNS-based MongoDB connection strings |
| Backend utilities | `python-dotenv`, `cryptography`, `reportlab`, `openpyxl`, `xlrd`, `python-multipart` |
| Frontend lint | Oxlint |
| Backend tests | pytest-style test modules are included in `backend/tests/` |

Dependency versions/ranges are defined in `frontend/package.json`, `frontend/package-lock.json`, and `backend/requirements.txt`.

## Application features

The supplied frontend and backend source covers the following functional areas:

- Login, account activation, forgot-password, OTP verification, password reset, and session logout.
- Company/legal entity and branch setup; organization units, departments, teams, designations, and holiday calendars.
- Employee Master, employee details, working details, team members, and reporting-manager assignment.
- User account management and role-based access.
- Daily attendance, employee attendance history, monthly attendance calendars, attendance requests, attendance corrections, and attendance exports.
- Work-from-home attendance with face verification and location checks, plus WFH administration settings.
- ESSL biometric attendance configuration, employee mapping, synchronization/import, and attendance issue handling.
- Leave and permission workflows, approvals, and leave-related notifications.
- General notifications and notification management.
- Employee salary setup, monthly payroll processing, payroll review/approval, payslip generation, and payslip viewing.

The user-facing role labels in the frontend are **HR Admin**, **HOD**, and **Employee**. The frontend role-label helper maps legacy stored role identifiers to these labels. Because legacy identifiers are still referenced in some authorization and compatibility logic in the supplied source, do not assume that changing a displayed label alone migrates stored user roles or backend permissions. Review backend role records and authorization rules before any role migration.

## Repository layout

The supplied files are two separate archives. For the commands below, extract them into one project directory using this layout:

```text
HRMS-Project/
├── README.md                 # This full-project guide
├── frontend/
│   ├── README.md             # Frontend-specific guide
│   ├── package.json
│   ├── package-lock.json
│   ├── vite.config.js
│   ├── index.html
│   ├── public/
│   └── src/
└── backend/
    ├── .env                  # Create locally; do not commit
    ├── .env.example
    ├── requirements.txt
    ├── requirements-wfh.txt
    ├── app/
    │   ├── main.py
    │   ├── db/mongodb.py
    │   └── feature modules (attendance, leave, payroll, etc.)
    ├── scripts/
    └── tests/
```

The archives provided for analysis contain the frontend README and backend `.env.example`; they do not contain a project-root README or a backend README. This document is intended to be saved as `HRMS-Project/README.md`. Save the frontend guide as `HRMS-Project/frontend/README.md`.

## Prerequisites

- **Node.js:** `^20.19.0` or `>=22.12.0`, as required by the locked Vite 8 release.
- **npm:** A version compatible with the installed Node.js release.
- **Python:** Python 3.12 is a suitable environment for the supplied project; use a supported Python 3 version compatible with the pinned backend dependencies.
- **MongoDB:** A reachable MongoDB server or MongoDB Atlas cluster and a database user with appropriate access.
- Network access between the browser, frontend host, backend host, and MongoDB server as applicable.

## MongoDB setup

1. Install/start MongoDB locally or create a MongoDB Atlas deployment.
2. Create a database user with permissions for the HRMS database.
3. Copy `backend/.env.example` to `backend/.env`.
4. Set `MONGODB_URL` to the MongoDB connection URI and `MONGODB_DATABASE` to the intended database name (the example uses `hrms_db`).
5. Ensure the backend machine can reach the MongoDB host and that the database user's credentials and network allowlist are correct.

Example values (replace them with your own; do not commit real credentials):

```dotenv
MONGODB_URL=mongodb://127.0.0.1:27017/
MONGODB_DATABASE=hrms_db
```

For Atlas, use the connection URI supplied by Atlas and URL-encode special characters in credentials where required. Keep database credentials private. The backend reads environment variables from `backend/.env` through `python-dotenv` in `app/db/mongodb.py`.

## Backend setup

Open a terminal at the project root:

```bash
cd backend
python -m venv .venv
```

Activate the virtual environment:

**Windows PowerShell**

```powershell
.\.venv\Scripts\Activate.ps1
```

**macOS/Linux**

```bash
source .venv/bin/activate
```

Install the backend dependencies:

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Create and configure the environment file before starting the service:

```bash
# Windows PowerShell
Copy-Item .env.example .env

# macOS/Linux
cp .env.example .env
```

Edit `.env` and provide at least a valid `MONGODB_URL` and `MONGODB_DATABASE`. Configure email, encryption, CORS, and WFH settings as required (see [Environment configuration](#environment-configuration)).

Start the API from the `backend/` directory:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

The backend listens on port `8000`. `--reload` is intended for local development; do not use it as the production process configuration.

### Optional WFH face-verification dependencies

The base `requirements.txt` installs the core backend dependencies. `requirements-wfh.txt` lists additional face-verification packages (`face-recognition` and `mediapipe`) alongside packages also present in the base requirements. Install these only if the deployment needs the optional local face-verification capability and the host can satisfy their native/system build requirements:

```bash
pip install -r requirements-wfh.txt
```

The WFH feature also requires its environment settings and browser camera/location permissions. Review compatibility and test the optional packages on the target operating system before deployment.

## Frontend setup

Open a second terminal at the project root:

```bash
cd frontend
npm ci
npm run dev
```

Vite serves the frontend on port `5173` and binds to `0.0.0.0` for local-network testing. Open:

- `http://localhost:5173` on the development machine.
- `http://<frontend-host-LAN-IP>:5173` from another device on the same network, if firewall and network rules allow it.

The Vite configuration proxies `/api` to `http://127.0.0.1:8000` by default. If the backend runs on another host or port, set `VITE_BACKEND_PROXY_TARGET` in the frontend environment before starting Vite. See `frontend/README.md` for details, including local HTTPS certificate handling.

## Environment configuration

The backend template is `backend/.env.example`. Configure only the settings needed by the deployment.

| Variable | Purpose |
|---|---|
| `MONGODB_URL` | MongoDB connection URI. Required for database connectivity. |
| `MONGODB_DATABASE` | MongoDB database name; the template uses `hrms_db`. |
| `ORG_TIMEZONE` | Organization timezone used for organization-local date/time behavior; template default is `Asia/Kolkata`. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM` | SMTP server and sender configuration for activation/password-related email workflows. |
| `FRONTEND_URL` | Frontend base URL used when constructing activation links; template default is `http://localhost:5173`. |
| `WFH_ATTENDANCE_ENABLED` | Enables/disables WFH check-in/check-out endpoints; template default is `true`. |
| `WFH_TEMPLATE_ENCRYPTION_KEY` | Encryption key for locally stored WFH face templates. Generate and protect a stable key before enabling this feature. |
| `WFH_FACE_MATCH_TOLERANCE` | Face-match threshold; template default is `0.48`, and the code comments state it cannot be relaxed above that value. |
| `WFH_MAX_LOCATION_ACCURACY_METERS` | Maximum accepted location accuracy; template default is `100` meters. |
| `WFH_MAX_LOCATION_AGE_SECONDS` | Maximum age of browser location timestamps; template default is `120` seconds. |
| `CORS_ALLOWED_ORIGINS` | Comma-separated exact frontend origins allowed by FastAPI; template includes localhost origins on port `5173`. |
| `CORS_ALLOWED_ORIGIN_REGEX` | Optional regex override for allowed origins. The code has a private-network Vite-origin default. |
| `ESSL_CONFIG_ENCRYPTION_KEY` | Stable encryption key for ESSL API credentials and request parameters stored in MongoDB. |
| `HRMS_ENV`, `DEMO_USER_NAME`, `DEMO_USER_EMAIL`, `DEMO_USER_MOBILE`, `DEMO_USER_ROLE`, `DEMO_USER_PASSWORD` | Commented local-development/demo settings in the template; use only if the corresponding development workflow is intentionally enabled. |

Generate Fernet-compatible encryption keys using the documented helper command in `.env.example`:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Use separate, securely stored values for WFH and ESSL encryption keys where both features are enabled. Do not commit `.env`, real credentials, encryption keys, or production secrets.

### Frontend environment variables

| Variable | Purpose |
|---|---|
| `VITE_BACKEND_PROXY_TARGET` | Backend target for Vite's `/api` proxy in development and preview; default `http://127.0.0.1:8000`. |
| `VITE_API_BASE_URL` | Optional absolute API base URL embedded in the frontend bundle. When unset, development uses same-origin `/api`; production defaults to `http://127.0.0.1:8000`. |

Never put secrets in `VITE_*` variables because they are available to browser code.

## Run the application

1. Ensure MongoDB is running and the backend `.env` is configured.
2. Start the backend in terminal 1:

   ```bash
   cd backend
   # Activate .venv first
   uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
   ```

3. Start the frontend in terminal 2:

   ```bash
   cd frontend
   npm run dev
   ```

4. Open `http://localhost:5173` and sign in with an account provisioned through the application's supported user setup/activation process.

The supplied source does not document a universal default administrator username/password. Do not assume demo credentials; use the organization's configured account creation or activation procedure.

## API documentation

FastAPI provides interactive API documentation while the backend is running:

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- OpenAPI schema: `http://localhost:8000/openapi.json`

API modules in the supplied backend include core/company/organization/user routes, employee details, reporting-manager assignment, attendance, attendance requests, ESSL attendance, leave, payroll, notifications, activation, and password reset. Use the live OpenAPI schema as the authoritative endpoint and request/response reference for the running version.

## Testing and quality checks

### Frontend

From `frontend/`:

```bash
npm run lint
npm run build
```

### Backend

The backend archive includes pytest test modules under `backend/tests/`. From `backend/`, after installing the dependencies and configuring a test database/environment as needed:

```bash
python -m pip install pytest
python -m pytest -q
```

Some tests may require a reachable MongoDB instance or specific environment configuration. Run tests against a dedicated test database, never against production data. The backend archive also includes utility scripts under `backend/scripts/`; inspect each script and its arguments before running it because some scripts can inspect or modify attendance data.

## Deployment notes

- Build the frontend with `npm run build`; deploy the contents of `frontend/dist/` using a static web server.
- Configure the web server to serve the SPA entry point for frontend paths and forward `/api` requests to the FastAPI service.
- Set `VITE_API_BASE_URL` at build time only when not using a same-origin `/api` reverse proxy. The default production fallback `http://127.0.0.1:8000` is not suitable for most remote browsers unless that address is intentionally reachable from them.
- Configure FastAPI CORS with the exact deployed frontend origin(s). Avoid broad wildcard origins when credentials are enabled.
- Use HTTPS for deployed applications, particularly for WFH camera and geolocation features. For local HTTPS development, Vite checks for the certificate files described in `frontend/README.md`.
- Run Uvicorn under a managed production process (for example, a service manager or container orchestration) with reload disabled. Protect the backend and MongoDB network access.
- Back up MongoDB regularly and test restoration procedures before production use.

## Security and operational notes

- Keep backend `.env` files and all secrets outside source control.
- Use strong, unique MongoDB credentials and restrict database network access.
- Protect and back up encryption keys; encrypted ESSL/WFH data may become unrecoverable if the corresponding key is lost.
- Use HTTPS and secure browser permissions for camera/location workflows.
- Frontend visibility or disabled controls are not a security boundary. Backend authorization must remain authoritative.
- Attendance and payroll records can affect business and employee outcomes. Test changes with a non-production database and verify payroll approval/locking behavior before operational use.

## Troubleshooting

| Symptom | Checks |
|---|---|
| Frontend cannot fetch API data | Confirm FastAPI is running on port `8000`; check `VITE_BACKEND_PROXY_TARGET`, `VITE_API_BASE_URL`, and browser network errors. |
| MongoDB connection fails | Verify `MONGODB_URL`, database credentials, network allowlist, DNS, and MongoDB availability. |
| Browser reports CORS errors | Add the exact frontend origin to `CORS_ALLOWED_ORIGINS` or configure the intended regex; restart the backend after changes. |
| HTTPS page calls HTTP API directly | Prefer the Vite `/api` proxy during development; do not configure an insecure absolute API URL for an HTTPS frontend. |
| WFH camera/location does not work | Use localhost or a trusted HTTPS origin, grant browser permissions, and verify WFH backend settings and optional dependencies. |
| Activation or password email is not received | Verify SMTP host, port, credentials, sender, network access, and `FRONTEND_URL`. |
| ESSL integration cannot decrypt saved configuration | Verify the stable `ESSL_CONFIG_ENCRYPTION_KEY` matches the key used when the configuration was saved. |
| Frontend refresh returns 404 after deployment | Configure SPA fallback to `index.html`; separately reverse-proxy `/api` to FastAPI. |

For frontend-specific structure, scripts, API configuration, and responsive UI assets, see [`frontend/README.md`](frontend/README.md).
