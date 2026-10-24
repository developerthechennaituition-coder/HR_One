# HRMS Frontend

React-based user interface for the HRMS application. The frontend is built with **React 19**, **Vite 8**, JavaScript, Bootstrap, and Bootstrap Icons. It communicates with the Python FastAPI backend over HTTP(S).

## Technology stack

- React 19 and React DOM
- Vite development server and production build
- Bootstrap 5 and Bootstrap Icons
- `libphonenumber-js` for phone-number utilities
- `qrcode.react` for QR-code rendering
- Oxlint for linting

## Main source structure

```text
frontend/
├── README.md
├── package.json
├── package-lock.json
├── vite.config.js
├── index.html
├── public/
│   ├── favicon.svg
│   ├── icons.svg
│   └── HRMS_title_login.svg
└── src/
    ├── App.jsx
    ├── main.jsx
    ├── auth.js
    ├── config/
    │   └── api.js
    ├── components/
    │   ├── Header.jsx
    │   ├── MainLayout.jsx
    │   ├── Sidebar.jsx
    │   ├── NotificationBell.jsx
    │   ├── NotificationPanel.jsx
    │   ├── AttendanceDayBreakdown.jsx
    │   └── EsslAdminSettings.jsx
    ├── services/
    │   ├── authenticatedFetch.js
    │   ├── notificationApi.js
    │   └── payrollApi.js
    ├── pages/
    │   ├── Dashboard.jsx
    │   ├── Login.jsx
    │   ├── ActivateAccount.jsx
    │   ├── ForgotPassword.jsx
    │   ├── VerifyResetOTP.jsx
    │   ├── ResetPassword.jsx
    │   ├── CompanySetup.jsx
    │   ├── OrganizationSetup.jsx
    │   ├── HolidayCalendarSetup.jsx
    │   ├── EmployeeMaster.jsx
    │   ├── EmployeeDetails.jsx
    │   ├── UserManagement.jsx
    │   ├── Attendance.jsx
    │   ├── EmployeeAttendance.jsx
    │   ├── UserAttendance.jsx
    │   ├── WfhAttendance.jsx
    │   ├── WfhAdminSettings.jsx
    │   ├── EsslSetup.jsx
    │   ├── Leave.jsx
    │   ├── EmployeeSalarySetup.jsx
    │   ├── PayrollWorkspace.jsx
    │   ├── EmployeePayslips.jsx
    │   ├── NotificationManagement.jsx
    │   ├── Profile.jsx
    │   ├── TeamMembers.jsx
    │   └── TeamMemberDetails.jsx
    └── styles-*.css
```

## Prerequisites

- Node.js and npm versions compatible with the Vite version specified in `package.json`
- The HRMS FastAPI backend running locally or at a configured host
- MongoDB running for backend functionality

## Install dependencies

From the repository root:

```powershell
cd frontend
npm ci
```

Use `npm ci` to install the exact dependency versions represented by `package-lock.json`. If you intentionally change dependencies, use npm to update the lock file and commit it with `package.json`.

## Configure the backend URL

The shared API configuration is in `src/config/api.js`. By default, it uses:

```text
http://127.0.0.1:8000
```

To point the frontend at another backend, create `frontend/.env.local`:

```dotenv
VITE_API_BASE_URL=http://127.0.0.1:8000
```

Replace the example URL with the actual API origin, including scheme and port. For LAN or mobile testing, use a reachable host/IP and configure the backend CORS allowlist accordingly. Restart Vite after changing environment variables.

`VITE_` variables are bundled into client-side code and must never contain secrets.

## Start the development server

```powershell
cd frontend
npm run dev
```

Vite is configured to bind to `0.0.0.0` on port `5173` and uses `strictPort`. Open the exact URL printed in the terminal, normally:

- <http://localhost:5173/>
- <http://127.0.0.1:5173/>

### API proxy

The Vite configuration proxies `/api` requests to `http://127.0.0.1:8000` during development. The frontend's shared API client also supports `VITE_API_BASE_URL`; when using a remote/LAN backend, verify that direct API requests and the proxy are configured for the intended environment.

### Optional local HTTPS

The Vite configuration checks for these files relative to the project root:

```text
../certs/localhost.pem
../certs/localhost-key.pem
```

When both exist, Vite uses them for HTTPS. Otherwise, it starts without the HTTPS certificate configuration. Use a certificate valid for the exact hostname/IP being opened. Keep the private key and local certificates out of source control.

If your environment requires HTTPS for browser camera, geolocation, or other secure-context APIs, configure the certificate and the frontend/API origins consistently.

## Application areas

The current frontend source includes pages/components for:

- Login, account activation, password recovery, OTP verification, and password reset
- Dashboard and profile
- Company / Legal Entity and organization structure
- Holiday calendar setup
- Employee master, employee details, and user management
- Daily attendance, employee attendance, personal attendance, and WFH attendance
- WFH settings and employee location/face-verification flows
- ESSL attendance configuration and synchronization screens
- Leave and permission workflows
- Notification bell, notification panel, and notification management
- Employee salary setup, payroll workspace, and payslips
- Reporting-manager team members and team-member details
- Role-aware navigation and access-denied page

The backend is authoritative for protected operations. Hiding a frontend navigation item is not a substitute for server-side authorization.

## Available npm scripts

Run these from `frontend/`:

| Command | Purpose |
|---|---|
| `npm run dev` | Start the Vite development server |
| `npm run build` | Build the production frontend into `dist/` |
| `npm run preview` | Preview the production build locally |
| `npm run lint` | Run Oxlint |

Before committing significant changes, run:

```powershell
npm run lint
npm run build
```

## Build for production

```powershell
cd frontend
npm ci
npm run lint
npm run build
```

The generated files are placed in `frontend/dist/`. The build output is generated and should not normally be committed. Deploy it only through the company's approved hosting/release process, with the correct API URL and security configuration for that environment.

## Development conventions

- Reuse the shared API base from `src/config/api.js` when practical.
- Use the authenticated API helper for endpoints requiring a session token.
- Keep API request/response handling and user-facing error states consistent with existing service modules.
- Preserve role-based navigation and verify matching backend authorization.
- Avoid hardcoding server IP addresses, credentials, tokens, or environment-specific values in source code.
- Keep reusable UI in `src/components/`, page-level features in `src/pages/`, API helpers in `src/services/`, and shared API configuration in `src/config/`.
- Keep CSS changes aligned with the existing global and feature stylesheets; test responsive layouts after UI changes.

## Troubleshooting

| Problem | What to check |
|---|---|
| `npm` or `node` not recognized | Install Node.js, reopen the terminal, and check `node --version` and `npm --version`. |
| `npm.ps1` execution policy error | In the current PowerShell process only, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`, then retry. |
| Port `5173` is already in use | Stop the other Vite process; `strictPort` prevents silently switching ports. |
| API/network errors | Verify the backend is running, `VITE_API_BASE_URL` is correct, and CORS allows the frontend origin. |
| Login works but a page is forbidden | Check the signed-in user's role and the backend's authorization rules. |
| Camera/geolocation does not work | Check browser permissions and secure-context requirements; test with the intended HTTPS hostname/IP. |
| HTTPS certificate error | Regenerate/install a trusted local certificate whose names include the exact hostname/IP, then restart Vite. |
| Build or lint fails | Read the first relevant error, fix the source issue, and rerun the command. |

## Related documentation

- Project-wide setup, backend, database, security, and integration notes: [`../README.md`](../README.md)
- Backend API docs while FastAPI is running: <http://127.0.0.1:8000/docs>
