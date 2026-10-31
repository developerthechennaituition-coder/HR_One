# HRMS Frontend

The HRMS frontend is a React single-page application for employee self-service and HR administration. It communicates with the Python FastAPI backend and uses MongoDB-backed APIs for authentication, organization, employee, attendance, leave, notification, payroll, and related workflows.

For the complete application architecture, backend setup, role descriptions, business workflows, and deployment guidance, see the repository-root [`README.md`](../README.md).

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

| Area | Technology |
|---|---|
| UI framework | React 19, React DOM |
| Build/dev server | Vite 8 |
| Language | JavaScript ES modules, JSX |
| UI styling | Bootstrap 5, Bootstrap Icons, project CSS |
| Phone utilities | libphonenumber-js |
| QR codes | qrcode.react |
| PDF rendering | pdfjs-dist |
| Linting | Oxlint |

Dependency versions are declared in `package.json`; `package-lock.json` records the resolved dependency tree. Use `npm ci` for reproducible installation.

## Application structure

```text
frontend/
├── README.md
├── package.json
├── package-lock.json
├── vite.config.js
├── index.html
├── .gitignore
├── public/
│   ├── favicon.svg
│   ├── HRMS_title_login.svg
│   └── icons.svg
└── src/
    ├── App.jsx
    ├── App.css
    ├── main.jsx
    ├── index.css
    ├── styles-*.css
    ├── auth.js
    ├── logout.js
    ├── config/
    │   └── api.js
    ├── assets/
    │   ├── hero.png
    │   └── notification.wav
    ├── components/
    │   ├── AttendanceDayBreakdown.jsx
    │   ├── AttendanceRequestInbox.jsx
    │   ├── AuthLayout.jsx
    │   ├── EsslAdminSettings.jsx
    │   ├── Header.jsx
    │   ├── HistoricalAttendanceModal.jsx
    │   ├── MainLayout.jsx
    │   ├── MyAttendanceCalendar.jsx
    │   ├── MyAttendanceRequests.jsx
    │   ├── MyHolidayCalendar.jsx
    │   ├── NotificationBell.jsx
    │   ├── NotificationPanel.jsx
    │   └── PayslipViewer.jsx
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
        ├── attendanceCalendarRange.js
        ├── attendanceHistoryFormat.js
        ├── attendanceRequestApi.js
        ├── authenticatedFetch.js
        ├── notificationApi.js
        ├── payrollApi.js
        ├── roleLabels.js
        └── weeklyOff.js
```

The tree highlights the principal modules. Refer to the checked-out source for the complete and current file list.

## Prerequisites

- Node.js and npm versions compatible with the Vite version pinned by `package-lock.json`.
- The HRMS FastAPI backend running and reachable from the browser.
- A configured MongoDB database for backend data.
- Optional HTTPS certificate files for local HTTPS development; the Vite configuration checks the repository-level `certs/` directory.

Verify:

```powershell
node --version
npm --version
```

## Install and run locally

From the repository root:

```powershell
cd frontend
npm ci
npm run dev
```

Vite is configured to bind to `0.0.0.0` on port `5173` with `strictPort: true`. Open the URL shown in the terminal, normally:

- `http://localhost:5173/`
- `http://127.0.0.1:5173/`

If port 5173 is already in use, stop the other process or deliberately change the Vite configuration and corresponding backend CORS settings.

For a dependency update or when the lockfile has intentionally changed, use `npm install` and commit the updated lockfile. For normal clean installs and CI, prefer `npm ci`.

## API configuration

The shared API base URL is defined in `src/config/api.js`:

```javascript
export const HRONE_API_BASE = (
  import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000"
).replace(/\/+$/, "");
```

Create `frontend/.env.local` for local development:

```dotenv
VITE_API_BASE_URL=http://127.0.0.1:8000
```

For a backend running on another machine, use its reachable hostname or LAN IP:

```dotenv
VITE_API_BASE_URL=http://192.168.1.50:8000
```

The address above is an example only. Replace it with the actual backend address. Configure the backend `CORS_ALLOWED_ORIGINS` to include the exact frontend origin.

Important:

- Restart Vite after changing `.env.local`.
- Do not put passwords, database URIs, SMTP credentials, or encryption keys in `VITE_*` variables. Vite exposes frontend environment variables to browser code.
- `vite.config.js` also defines a development proxy for `/api` to `http://127.0.0.1:8000`; application API calls should use the shared API configuration/service patterns already in the project.

## Available scripts

Run these commands from `frontend/`:

| Command | Purpose |
|---|---|
| `npm run dev` | Start Vite development server |
| `npm run build` | Create an optimized production build in `dist/` |
| `npm run preview` | Preview the production build locally |
| `npm run lint` | Run Oxlint |

Typical validation:

```powershell
npm run lint
npm run build
```

A successful Vite build confirms that the frontend bundles. It does not confirm that the backend is reachable or that every workflow behaves correctly at runtime.

## Application areas

| Area | Main page/component files | Purpose |
|---|---|---|
| App shell and navigation | `App.jsx`, `MainLayout.jsx`, `Header.jsx`, `Sidebar.jsx` | Application routing/layout/navigation |
| Authentication | `Login.jsx`, `AuthLayout.jsx`, `ActivateAccount.jsx`, `ForgotPassword.jsx`, `VerifyResetOTP.jsx`, `ResetPassword.jsx`, `ChangePassword.jsx` | Sign-in and account lifecycle |
| Dashboard | `Dashboard.jsx` | HRMS landing/dashboard view |
| Organization | `CompanySetup.jsx`, `OrganizationSetup.jsx`, `HolidayCalendarSetup.jsx` | Company structure and holiday setup |
| Employee administration | `EmployeeMaster.jsx`, `EmployeeDetails.jsx`, `UserManagement.jsx` | Employee and user records |
| Attendance | `Attendance.jsx`, `EmployeeAttendance.jsx`, `UserAttendance.jsx`, `AttendanceDayBreakdown.jsx`, `MyAttendanceCalendar.jsx` | Daily/monthly attendance and employee attendance details |
| Attendance requests | `MyAttendanceRequests.jsx`, `AttendanceRequestInbox.jsx` | Employee requests and HR review |
| Historical attendance | `HistoricalAttendanceModal.jsx` | Historical attendance review/edit interface exposed by the attendance page |
| WFH | `WfhAttendance.jsx`, `WfhAdminSettings.jsx`, `WfhEmailVerification.jsx` | Work-from-home attendance and administration |
| ESSL | `EsslSetup.jsx`, `EsslAdminSettings.jsx` | Device integration setup and administration |
| Leave | `Leave.jsx` | Leave and permission workflows |
| Notifications | `NotificationBell.jsx`, `NotificationPanel.jsx`, `NotificationManagement.jsx` | In-app notification experience |
| Payroll | `PayrollWorkspace.jsx`, `EmployeeSalarySetup.jsx`, `EmployeePayslips.jsx`, `PayslipViewer.jsx` | Salary, payroll and payslip screens |
| Team | `TeamMembers.jsx`, `TeamMemberDetails.jsx` | Team-member views |
| Profile | `Profile.jsx` | Employee profile/self-service |

Feature availability is controlled by backend permissions, role assignments, and business data. A visible frontend control does not replace backend authorization.

## Authentication and API requests

- `src/auth.js` contains frontend authentication/session helpers.
- `src/services/authenticatedFetch.js` provides the shared authenticated request pattern.
- `src/config/api.js` centralizes the API base URL.
- `src/services/notificationApi.js`, `payrollApi.js`, and `attendanceRequestApi.js` group related API operations.
- `src/services/roleLabels.js` contains role display-name helpers.
- `src/services/attendanceCalendarRange.js` and `weeklyOff.js` hold shared attendance/calendar utilities.

When adding an API request:

1. Reuse the shared API base URL.
2. Follow the existing authentication/header pattern.
3. Handle non-2xx responses and expired sessions.
4. Display actionable errors without exposing tokens or sensitive backend details.
5. Keep authorization checks in the backend as well as any frontend visibility checks.

## Build and deployment

Create the production bundle:

```powershell
npm ci
npm run build
```

Vite writes the output to `frontend/dist/`. Deploy the contents of `dist/` to the chosen static hosting service or web server.

Production deployment must also provide:

- A production API URL through the build-time `VITE_API_BASE_URL`.
- HTTPS for the frontend and API.
- Backend CORS configuration for the deployed frontend origin.
- Correct reverse-proxy routing if `/api` is served through the same origin.
- A deployment process that rebuilds the frontend when environment configuration changes.

Do not deploy `.env.local`, `node_modules/`, or development-only files as static site content.

## Development conventions

- Keep shared UI in `src/components/` and route-level screens in `src/pages/`.
- Reuse existing service modules instead of duplicating API URL, authentication, notification, and date-formatting logic.
- Keep responsive behavior in the existing CSS system and test narrow mobile, tablet, and desktop widths.
- Use accessible labels for inputs, clear validation messages, keyboard-operable controls, and visible focus states.
- Avoid hardcoding role names or permission assumptions in multiple components; use the existing role/permission helpers and backend authorization.
- Preserve existing API contracts unless backend and frontend changes are coordinated.
- Do not commit generated build output, dependencies, local secrets, or employee data.

## Troubleshooting

### Vite starts but API requests fail

- Confirm the backend is running on port 8000.
- Check `VITE_API_BASE_URL` in `.env.local`.
- Confirm the browser can reach the backend host.
- Check backend CORS settings and browser developer tools.
- Restart Vite after changing environment variables.

### Port 5173 is already in use

The Vite server uses `strictPort: true`. Stop the process using the port or update `vite.config.js` and the backend CORS allowlist together.

### A page or button does not appear

- Confirm the correct route/component is being rendered.
- Check the logged-in role and permissions returned by the backend.
- Check browser console errors and failed API requests.
- Confirm that the running frontend was rebuilt/restarted after source changes.
- Remember that frontend visibility and backend authorization are separate checks.

### Production build fails

```powershell
npm ci
npm run build
```

Read the first Vite/JavaScript error. Do not use `npm audit fix --force` as a generic build fix; it can upgrade dependencies across breaking versions. Review `npm audit` findings and update dependencies deliberately.

### Styles or assets appear stale

- Hard-refresh the browser.
- Confirm the latest `dist/` output is deployed.
- Clear only the relevant browser/site cache if required.
- Check asset paths and browser network responses.

---

For backend setup, MongoDB configuration, role hierarchy, attendance/leave/payroll business rules, API documentation, and production deployment, see the root [`README.md`](../README.md).
