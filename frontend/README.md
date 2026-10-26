# HRMS Frontend

The HRMS frontend is a React single-page application for employee, attendance, leave, organization, notification, and payroll workflows. It communicates with the Python FastAPI backend over HTTP(S).

## Contents

- [Technology stack](#technology-stack)
- [Application structure](#application-structure)
- [Prerequisites](#prerequisites)
- [Install and run locally](#install-and-run-locally)
- [API configuration](#api-configuration)
- [Available scripts](#available-scripts)
- [Application areas](#application-areas)
- [Authentication and API requests](#authentication-and-api-requests)
- [Build and deployment](#build-and-deployment)
- [Development conventions](#development-conventions)
- [Troubleshooting](#troubleshooting)

## Technology stack

- React 19 and React DOM
- Vite 8 development server and production build tool
- JavaScript ES modules
- Bootstrap 5 and Bootstrap Icons
- `libphonenumber-js` for phone-number utilities
- `qrcode.react` for QR-code rendering
- Oxlint for linting

Dependency versions are defined in `package.json` and `package-lock.json`. Use `npm ci` for a reproducible installation.

## Application structure

```text
frontend/
├── README.md
├── package.json
├── package-lock.json
├── vite.config.js
├── index.html
├── .env.local                 # local configuration; do not commit secrets
├── public/
│   ├── favicon.svg
│   ├── icons.svg
│   └── HRMS_title_login.svg
└── src/
    ├── App.jsx                # application routing and page composition
    ├── main.jsx               # React entry point
    ├── App.css
    ├── index.css
    ├── styles-*.css           # shared and feature-specific styles
    ├── auth.js                # stored session and user-management role helper
    ├── logout.js
    ├── config/
    │   └── api.js             # shared API base URL
    ├── components/
    │   ├── Header.jsx
    │   ├── MainLayout.jsx
    │   ├── Sidebar.jsx
    │   ├── NotificationBell.jsx
    │   ├── NotificationPanel.jsx
    │   ├── AttendanceDayBreakdown.jsx
    │   └── EsslAdminSettings.jsx
    ├── pages/
    │   ├── AccessDenied.jsx
    │   ├── ActivateAccount.jsx
    │   ├── Attendance.jsx
    │   ├── ChangePassword.jsx
    │   ├── CompanySetup.jsx
    │   ├── Dashboard.jsx
    │   ├── EmployeeAttendance.jsx
    │   ├── EmployeeDetails.jsx
    │   ├── EmployeeMaster.jsx
    │   ├── EmployeePayslips.jsx
    │   ├── EmployeeSalarySetup.jsx
    │   ├── EsslSetup.jsx
    │   ├── ForgotPassword.jsx
    │   ├── HolidayCalendarSetup.jsx
    │   ├── Leave.jsx
    │   ├── Login.jsx
    │   ├── NotificationManagement.jsx
    │   ├── OrganizationSetup.jsx
    │   ├── PayrollWorkspace.jsx
    │   ├── Profile.jsx
    │   ├── ResetPassword.jsx
    │   ├── TeamMemberDetails.jsx
    │   ├── TeamMembers.jsx
    │   ├── UserAttendance.jsx
    │   ├── UserManagement.jsx
    │   ├── VerifyResetOTP.jsx
    │   ├── WfhAdminSettings.jsx
    │   ├── WfhAttendance.jsx
    │   └── WfhEmailVerification.jsx
    └── services/
        ├── authenticatedFetch.js
        ├── notificationApi.js
        └── payrollApi.js
```

## Prerequisites

- Node.js and npm versions compatible with the Vite version in `package.json`.
- The HRMS FastAPI backend running and reachable from the browser.
- MongoDB configured for the backend.
- Optional HTTPS certificate files for local HTTPS testing.

Check your installation:

```powershell
node --version
npm --version
```

## Install and run locally

From the repository root, open a terminal:

```powershell
cd frontend
npm ci
npm run dev
```

Open the URL shown by Vite, normally:

- <http://localhost:5173/>
- <http://127.0.0.1:5173/>

The Vite development server is configured to bind to `0.0.0.0` on port `5173` with `strictPort: true`. If the port is already occupied, stop the other process or deliberately update `vite.config.js`.

## API configuration

The shared API base URL is defined in `src/config/api.js`. It uses `VITE_API_BASE_URL` if supplied, otherwise it defaults to `http://127.0.0.1:8000`.

Create `frontend/.env.local` for local development:

```dotenv
VITE_API_BASE_URL=http://127.0.0.1:8000
```

Use the real API origin for your environment. Examples:

```dotenv
# Local backend
VITE_API_BASE_URL=http://127.0.0.1:8000

# Example only: replace with the actual backend hostname/IP and port
VITE_API_BASE_URL=http://192.168.1.50:8000
```

After editing `.env.local`, restart Vite. Configure the backend's `CORS_ALLOWED_ORIGINS` to include the exact frontend origin where needed. For a LAN/mobile test, ensure the phone and backend host can reach one another on the network and that firewalls allow the intended traffic.

**Security:** all `VITE_` variables are bundled into client-side code and are visible to users. Never place passwords, tokens, private keys, or other secrets in them.

### Development API proxy

`vite.config.js` proxies requests beginning with `/api` to `http://127.0.0.1:8000` during development. The application also has a shared API-base configuration. If changing hosts or testing from another device, verify that the API helper usage and proxy behavior match the selected setup; the proxy target is not automatically changed by `VITE_API_BASE_URL`.

### Optional local HTTPS

The Vite configuration looks for both files relative to the frontend project:

```text
../certs/localhost.pem
../certs/localhost-key.pem
```

If both exist, Vite enables HTTPS. Otherwise it starts without that certificate configuration. Use a certificate trusted by the test browser and valid for the hostname/IP used to open the frontend. Keep private keys and certificates out of source control. Camera and geolocation APIs generally require a secure context, such as HTTPS or localhost.

## Available scripts

Run from `frontend/`:

| Command | Purpose |
|---|---|
| `npm run dev` | Start Vite development server |
| `npm run build` | Create production assets in `dist/` |
| `npm run preview` | Preview the production build locally |
| `npm run lint` | Run Oxlint |

Recommended validation before committing:

```powershell
npm run lint
npm run build
```

## Application areas

### Authentication and account management
- Login and session handling.
- Account activation.
- Forgot password, reset OTP verification, and password reset.
- Change password.
- Profile and access-denied screen.

### Dashboard and layout
- Dashboard.
- Shared header, sidebar, main layout, and logout handling.
- Role-aware navigation.

### Company, organization, and employees
- Company setup.
- Organization setup.
- Holiday calendar setup.
- Employee master and employee details.
- User management.
- Reporting-manager team list and team-member details.

### Attendance and WFH
- Attendance overview.
- Employee attendance and individual user attendance.
- WFH attendance.
- WFH admin settings.
- WFH email verification.
- ESSL setup and ESSL admin settings.
- Attendance-day breakdown component.

### Leave, notifications, and payroll
- Leave and permission workflows.
- Notification bell and panel.
- Notification management.
- Employee salary setup.
- Payroll workspace.
- Employee payslips.

The frontend is the user interface, not the source of truth for permissions. Backend APIs must validate the current session and the user's role for protected operations.

## Authentication and API requests

- `src/config/api.js` centralizes the API base URL.
- `src/services/authenticatedFetch.js` provides the shared authenticated request helper used by API consumers.
- `src/services/notificationApi.js` contains notification-related API calls.
- `src/services/payrollApi.js` contains payroll-related API calls.
- `src/auth.js` includes session-reading and user-management role helpers.

The current session helper reads `hrms_session` from `sessionStorage`; corrupted or missing stored session data is treated as a signed-out session. Do not treat client-side session storage or a hidden navigation item as authorization. The backend must independently authenticate and authorize every protected request.

When adding a new API call:
1. Reuse the shared API base URL rather than hardcoding a host.
2. Use the existing authenticated request helper where appropriate.
3. Handle non-success HTTP responses and network errors visibly.
4. Avoid logging access tokens, employee personal information, payroll details, or biometric/location data.
5. Confirm the backend endpoint and its expected payload in the running API docs at <http://127.0.0.1:8000/docs>.

## Build and deployment

Create and validate a production build:

```powershell
cd frontend
npm ci
npm run lint
npm run build
```

Vite writes production files to `frontend/dist/`. The build output is generated and should not normally be committed. Deploy it using the organization's approved hosting process and set the correct API origin and HTTPS configuration for that environment.

Before deploying:
- Set the production `VITE_API_BASE_URL` at build time.
- Configure backend CORS for the actual production frontend origin.
- Use HTTPS for frontend and backend.
- Verify deep links/client routing work with the selected hosting provider's SPA fallback configuration.
- Confirm API access, session expiry, role permissions, and error handling.
- Ensure `.env.local`, credentials, private certificates, `node_modules/`, and `dist/` are not committed unless your release process explicitly requires generated assets.

## Development conventions

- Keep shared UI in `src/components/`, page-level screens in `src/pages/`, API services in `src/services/`, and common API configuration in `src/config/`.
- Follow existing component and CSS conventions; this project uses multiple `styles-*.css` files.
- Preserve accessible labels, keyboard navigation, responsive layouts, and clear loading/empty/error states.
- Avoid introducing a second API base URL or duplicate authentication implementation.
- Keep role-based UI aligned with backend permissions, but never rely on frontend checks alone.
- Do not hardcode server IP addresses, credentials, tokens, or environment-specific values.
- When changing dependencies, update `package.json` and `package-lock.json` together.

## Troubleshooting

| Problem | What to check |
|---|---|
| `node` or `npm` not recognized | Install a supported Node.js release, reopen the terminal, and check both version commands. |
| `npm.ps1` execution policy error | In the current PowerShell process only, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`, then retry the command. |
| Dependency install fails | Use the intended Node/npm versions and run `npm ci` from `frontend/`. Review the first dependency error. |
| Port `5173` is already in use | Stop the process using the port or update the Vite config intentionally; `strictPort` prevents automatic port changes. |
| API/network errors | Confirm the backend is running, API base URL is correct, and CORS permits the frontend origin. Inspect browser Network/Console details. |
| Login works but a page is forbidden | Check the signed-in role and backend authorization. Client navigation rules do not grant API access. |
| Camera/geolocation does not work | Use localhost or HTTPS, grant browser permissions, and test with the intended hostname/IP. |
| HTTPS certificate error | Use a trusted certificate containing the exact hostname/IP and ensure both certificate files are present. |
| Lint/build fails | Run `npm ci`, then inspect the first meaningful error from `npm run lint` or `npm run build`. |

## Related documentation

- Full project setup, backend configuration, MongoDB, attendance/ESSL, WFH, payroll, and deployment: [`../README.md`](../README.md)
- FastAPI interactive API reference (backend running): <http://127.0.0.1:8000/docs>
