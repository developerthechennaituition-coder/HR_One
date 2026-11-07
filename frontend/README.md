# HRMS Frontend

The HRMS frontend is a React single-page application (SPA) for employee self-service and HR administration. It talks to the HRMS FastAPI backend over REST and covers authentication, organization setup, employee records, attendance (manual, ESSL biometric and work-from-home), leave and permissions, notifications, and payroll/payslips.

For the full system documentation (architecture, backend, database, business rules, environment variables), see the project-level [`README.md`](../README.md).

## Contents

- [Technology stack](#technology-stack)
- [Prerequisites](#prerequisites)
- [Installation and development](#installation-and-development)
- [Configuration](#configuration)
- [Available scripts](#available-scripts)
- [Project structure](#project-structure)
- [Application architecture](#application-architecture)
- [Pages](#pages)
- [Components](#components)
- [Services and utilities](#services-and-utilities)
- [Role-based access](#role-based-access)
- [Backend API usage](#backend-api-usage)
- [Attendance UI behavior](#attendance-ui-behavior)
- [Styling](#styling)
- [Production build and deployment](#production-build-and-deployment)
- [Development conventions](#development-conventions)
- [Troubleshooting](#troubleshooting)

## Technology stack

Versions below are the ranges declared in `package.json`; `package-lock.json` pins the resolved versions.

| Area | Technology |
|---|---|
| UI framework | React `^19.2.8`, React DOM `^19.2.8` |
| Build tool / dev server | Vite `^8.3.0` with `@vitejs/plugin-react` `^6.1.1` |
| Language | JavaScript (ES modules, JSX); `@types/react` and `@types/react-dom` are installed as dev dependencies |
| UI styling | Bootstrap `^5.3.8`, Bootstrap Icons `^1.13.1`, plus project CSS (`src/styles-01.css` … `styles-08.css`, `index.css`) |
| Phone number handling | `libphonenumber-js` `^1.13.13` (used in User Management) |
| QR codes | `qrcode.react` `^4.2.0` (used in WFH Attendance) |
| PDF rendering | `pdfjs-dist` `^5.7.284` (used by the payslip viewer) |
| Linting | Oxlint `^1.81.0` (`react/rules-of-hooks` as error, `react/only-export-components` as warning) |

There is no client-side routing library, no global state library and no TypeScript source; state is held in React hooks and navigation is hash-based (see [Application architecture](#application-architecture)).

## Prerequisites

- Node.js and npm compatible with Vite 8 (check the engine requirements of the installed Vite version).
- The HRMS backend running and reachable (default `http://127.0.0.1:8000`) with its MongoDB database configured. See the project [`README.md`](../README.md).
- Optional: a local HTTPS certificate pair for HTTPS development (see [HTTPS in development](#https-in-development)).

```bash
node --version
npm --version
```

## Installation and development

From the project root:

```bash
cd frontend
npm ci          # reproducible install from package-lock.json
npm run dev     # start the Vite dev server
```

The dev server is configured in `vite.config.js` to bind to `0.0.0.0` on port `5173` with `strictPort: true`, so it fails instead of silently choosing another port if 5173 is taken. Open:

- `http://localhost:5173/`
- `http://127.0.0.1:5173/`
- `http(s)://<your-LAN-IP>:5173/` from another device on the same network

Start the backend first (or at the same time), otherwise login and every data request will fail. Use `npm install` only when you intentionally change dependencies, and commit the updated `package-lock.json`.

## Configuration

### How the API base URL is resolved

`src/config/api.js` exports `HRONE_API_BASE`, used by all API calls:

| Situation | Resulting base URL |
|---|---|
| `VITE_API_BASE_URL` is set (any mode) | The value of `VITE_API_BASE_URL`, trailing slashes removed |
| Development (`npm run dev`) and `VITE_API_BASE_URL` not set | Empty string, so requests go to the page's own origin as `/api/...` |
| Production build and `VITE_API_BASE_URL` not set | `http://127.0.0.1:8000` |

### Development proxy

In `vite.config.js`, both the dev server and `vite preview` proxy `/api` to the backend:

- Target: `http://127.0.0.1:8000` by default.
- Override with the `VITE_BACKEND_PROXY_TARGET` environment variable when the backend runs elsewhere.
- `changeOrigin: true` is enabled.

Because the browser only talks to the Vite origin in development, the same setup works for `http://localhost:5173` and `https://<LAN-IP>:5173` and avoids mixed-content and certificate problems with the plain-HTTP backend.

### Environment variables

| Variable | Read by | Purpose |
|---|---|---|
| `VITE_API_BASE_URL` | `src/config/api.js` (browser bundle) | Absolute backend URL. Set it when the backend is hosted on a different origin, and always for production builds that do not serve `/api` from the same origin. |
| `VITE_BACKEND_PROXY_TARGET` | `vite.config.js` (Node, dev/preview only) | Backend URL the Vite proxy forwards `/api` to. |

Example `frontend/.env.local` (only needed for non-default setups):

```dotenv
VITE_API_BASE_URL=https://api.example.com
VITE_BACKEND_PROXY_TARGET=http://127.0.0.1:8000
```

Notes:

- Restart Vite after changing environment variables.
- `VITE_*` values are embedded in the browser bundle. Never put secrets (passwords, database URLs, SMTP credentials, encryption keys) in them.
- `*.local` files are git-ignored.
- The backend must allow the frontend origin through its CORS settings (`CORS_ALLOWED_ORIGINS` / `CORS_ALLOWED_ORIGIN_REGEX`; see the project README).

### HTTPS in development

`vite.config.js` looks for these files in a `certs/` directory **next to** the `frontend/` directory (`../certs` relative to `frontend/`):

- `localhost-key.pem`
- `localhost.pem`

If both exist, the dev server starts with HTTPS; otherwise it starts with plain HTTP. HTTPS is useful for features that browsers restrict to secure contexts when testing from another device (the WFH page uses the camera and geolocation).

## Available scripts

Run from `frontend/`:

| Command | Description |
|---|---|
| `npm run dev` | Start the Vite dev server on port 5173 |
| `npm run build` | Create a production build in `dist/` |
| `npm run preview` | Serve the production build locally on port 5173 (with the `/api` proxy) |
| `npm run lint` | Run Oxlint |

Typical pre-commit check:

```bash
npm run lint
npm run build
```

A successful build confirms the app bundles; it does not verify backend connectivity or runtime workflows. The repository contains no frontend test runner.

## Project structure

```text
frontend/
├── README.md
├── index.html                 # Vite entry HTML (title: "HRMS | Human Resource Management")
├── package.json
├── package-lock.json
├── vite.config.js             # React plugin, port 5173, /api proxy, optional HTTPS
├── .oxlintrc.json             # Oxlint configuration
├── .gitignore
├── public/
│   ├── favicon.svg
│   ├── HRMS_title_login.svg
│   └── icons.svg
└── src/
    ├── main.jsx               # Mounts <App/>, loads Bootstrap + Bootstrap Icons + index.css
    ├── App.jsx                # Session handling and public (pathname-based) pages
    ├── App.css
    ├── index.css              # Imports styles-01.css … styles-08.css
    ├── styles-01.css … styles-08.css
    ├── auth.js                # canManageUsers(), readStoredSession()
    ├── logout.js              # Calls /api/auth/logout and clears the session
    ├── config/
    │   └── api.js             # HRONE_API_BASE
    ├── assets/
    │   ├── hero.png
    │   ├── react.svg
    │   ├── vite.svg
    │   └── notification.wav   # Sound played by the notification bell
    ├── components/
    │   ├── AttendanceDayBreakdown.jsx
    │   ├── AttendanceExportModal.jsx
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
    │   ├── PayslipViewer.jsx
    │   └── Sidebar.jsx
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
        ├── attendanceCalendarStatus.js
        ├── attendanceHistoryFormat.js
        ├── attendanceRequestApi.js
        ├── authenticatedFetch.js
        ├── notificationApi.js
        ├── payrollApi.js
        ├── roleLabels.js
        └── weeklyOff.js
```

## Application architecture

### Entry and session

1. `main.jsx` renders `<App />` inside `StrictMode`.
2. `App.jsx` reads the stored session with `readStoredSession()` (the `hrms_session` key in browser **`sessionStorage`**) and decides what to render.
3. A signed-in session renders `MainLayout`; otherwise the `Login` page is shown. `logout.js` calls `POST /api/auth/logout`, removes `hrms_session`, and returns to `/login`.

Because the session lives in `sessionStorage`, it is scoped to the browser tab and is discarded when the tab is closed. Requests send the token as `Authorization: Bearer <token>`.

### Two navigation mechanisms

There is no React Router. The app uses two simple mechanisms:

**1. Pathname-based public pages** (handled in `App.jsx`, usable without signing in):

| Path | Page |
|---|---|
| `/login` | `Login` |
| `/activate` | `ActivateAccount` (account activation link from email) |
| `/forgot-password` | `ForgotPassword` |
| `/verify-reset-otp` | `VerifyResetOTP` |
| `/reset-password` | `ResetPassword` |
| `/wfh-email-verification` | `WfhEmailVerification` (one-time WFH email fallback link) |

Matching uses `pathname === …` or `pathname.endsWith(…)`. When deploying, the web server must rewrite these paths to `index.html` (see [Production build and deployment](#production-build-and-deployment)).

**2. Hash-based pages inside the authenticated shell** (`MainLayout.jsx`): the active page is the URL hash, for example `#attendance` or `#leave`. Unknown or empty hashes fall back to `#dashboard`. The page names accepted by `MainLayout` are:

`dashboard`, `company-setup`, `organization-setup`, `holiday-calendar`, `employees`, `employee-salary-setup`, `payroll-dashboard`, `monthly-payroll`, `payslip-management`, `employee-details`, `users`, `attendance`, `my-attendance`, `wfh-attendance`, `wfh-settings`, `user-attendance`, `essl-setup`, `leave`, `notifications`, `profile`, `change-password`, `team-members`, `team-member-details`, `403`.

`MainLayout` also holds the current user, role-derived permission flags, the selected employee for detail views, the reporting-manager status, and the mobile-sidebar state. Pages that need a selected employee (`employee-details`, `team-member-details`) receive it from `MainLayout` state; opening those hashes directly without a selection shows Access Denied.

## Pages

### Public pages

| File | Purpose |
|---|---|
| `Login.jsx` | Sign in with work email or employee ID and password (`POST /api/auth/login`) |
| `ActivateAccount.jsx` | Set a password from the activation link (`GET`/`POST /api/auth/activate`) |
| `ForgotPassword.jsx` | Request a password-reset OTP |
| `VerifyResetOTP.jsx` | Verify the emailed OTP |
| `ResetPassword.jsx` | Set a new password |
| `WfhEmailVerification.jsx` | Redeem the one-time WFH email-fallback link |

`AuthLayout.jsx` provides the shared layout for Login, Forgot Password, Verify Reset OTP and Reset Password.

### Authenticated pages

| Hash | File | Description |
|---|---|---|
| `#dashboard` | `Dashboard.jsx` | Landing page; loads attendance, employee, user, leave-dashboard and team data from the backend |
| `#company-setup` | `CompanySetup.jsx` | Company / legal-entity / branch records (`/api/companies`) |
| `#organization-setup` | `OrganizationSetup.jsx` | Organization structure (business units, departments, teams, designations) |
| `#holiday-calendar` | `HolidayCalendarSetup.jsx` | Holiday calendars and holidays. Can look up public holidays from the third-party Nager.Date API (`https://date.nager.at`) directly from the browser |
| `#employees` | `EmployeeMaster.jsx` | Employee Master list (HR Admin) |
| `#employee-details` | `EmployeeDetails.jsx` | Detailed employee record; editable when opened from Employee Master, read-only from Profile |
| `#users` | `UserManagement.jsx` | Create/manage user accounts, roles and activation (HR Admin) |
| `#attendance` | `Attendance.jsx` (HR Admin, Administrator) or `EmployeeAttendance.jsx` (other roles) | Daily attendance management, or the employee's own attendance |
| `#my-attendance` | `EmployeeAttendance.jsx` | Own attendance calendar and correction requests (shown separately to HR Admin and Administrator) |
| `#user-attendance` | `UserAttendance.jsx` | Per-employee monthly attendance, department filters and Excel export (HR Admin, Administrator) |
| `#wfh-attendance` | `WfhAttendance.jsx` | Work-from-home check-in/check-out with face verification and location |
| `#wfh-settings` | `WfhAdminSettings.jsx` | WFH configuration per employee: approved locations, face data (HR Admin, Administrator) |
| `#essl-setup` | `EsslSetup.jsx` → `EsslAdminSettings.jsx` | ESSL biometric integration: API config, employee mapping, sync, file import, history and issues (HR Admin, Administrator) |
| `#leave` | `Leave.jsx` | Leave dashboard, my leave, permissions, approvals, setup (for approvers/admins), reports and leave notifications |
| `#notifications` | `NotificationManagement.jsx` | Create and manage notifications |
| `#payroll-dashboard` | `PayrollWorkspace.jsx` (`mode="dashboard"`) | Payroll overview (HR Admin) |
| `#employee-salary-setup` | `EmployeeSalarySetup.jsx` | Salary profiles (HR Admin) |
| `#monthly-payroll` | `PayrollWorkspace.jsx` (`mode="monthly"`) | Monthly payroll calculation and approval workflow (HR Admin) |
| `#payslip-management` | `PayrollWorkspace.jsx` (`mode="payslips"`) for HR Admin; `EmployeePayslips.jsx` for everyone else | Payslip register, or the employee's own payslips |
| `#profile` | `Profile.jsx` | Own profile, organization details, holiday and attendance calendars, leave summary |
| `#change-password` | `ChangePassword.jsx` | Change own password |
| `#team-members` | `TeamMembers.jsx` | Team list for reporting managers |
| `#team-member-details` | `TeamMemberDetails.jsx` | Detail and attendance view of one team member |
| `#403` | `AccessDenied.jsx` | Shown when a page is not permitted |

## Components

| Component | Used by | Purpose |
|---|---|---|
| `MainLayout.jsx` | `App.jsx` | Authenticated shell: permissions, hash navigation, page rendering, header and sidebar |
| `Header.jsx` | `MainLayout` | Top bar; checks reporting-manager status; hosts the notification bell |
| `Sidebar.jsx` | `MainLayout` | Role-aware navigation with collapsible Organization Setup, Attendance and Payroll groups |
| `AuthLayout.jsx` | Login and password-reset pages | Shared layout for unauthenticated pages |
| `NotificationBell.jsx` | `Header` | Unread count and notification access; plays `assets/notification.wav` |
| `NotificationPanel.jsx` | — | Notification panel component present in the source tree; it is not currently imported by any other file |
| `AttendanceRequestInbox.jsx` | `Attendance` | HR review of employee attendance-correction requests |
| `MyAttendanceRequests.jsx` | `EmployeeAttendance`, `AttendanceRequestInbox` | Employee attendance-correction requests |
| `HistoricalAttendanceModal.jsx` | `Attendance` | Historical attendance review and modification |
| `AttendanceExportModal.jsx` | `UserAttendance` | Select employees and month for the Excel export |
| `AttendanceDayBreakdown.jsx` | `Profile`, `TeamMemberDetails`, `EmployeeAttendance`, `UserAttendance` | Per-day attendance detail (times, intervals, issues) |
| `MyAttendanceCalendar.jsx` | `EmployeeAttendance`, `UserAttendance` | Monthly attendance calendar with status legend |
| `MyHolidayCalendar.jsx` | `Profile` | The signed-in employee's applicable holidays (`/api/my-holidays`) |
| `EsslAdminSettings.jsx` | `EsslSetup` | ESSL admin UI |
| `PayslipViewer.jsx` | `PayrollWorkspace`, `EmployeePayslips` | In-app payslip PDF viewing (uses `pdfjs-dist`) |

## Services and utilities

| File | Purpose |
|---|---|
| `config/api.js` | `HRONE_API_BASE`, the shared API base URL |
| `auth.js` | `canManageUsers(user)`; `readStoredSession()` (reads `hrms_session` from `sessionStorage`) |
| `logout.js` | Server logout call and local session cleanup |
| `services/authenticatedFetch.js` | `fetch` wrapper that adds the `Authorization: Bearer` header from the stored session |
| `services/notificationApi.js` | Notification and leave-notification operations (list, unread count, snapshot, lookups, view, acknowledge, star/unstar, hold/resume, delete, viewers) |
| `services/payrollApi.js` | Payroll API operations |
| `services/attendanceRequestApi.js` | Attendance-request API operations |
| `services/attendanceCalendarRange.js` | Shared attendance calendar date-range helpers |
| `services/attendanceCalendarStatus.js` | Shared attendance calendar status helpers |
| `services/attendanceHistoryFormat.js` | Formatting of attendance history entries |
| `services/weeklyOff.js` | Weekly-off helpers (reads `/api/leave/company-policy`) |
| `services/roleLabels.js` | Role display labels; the stored role `HR Manager` is displayed as **HOD** |

Many pages call the backend directly with `fetch` and `HRONE_API_BASE`; the service modules above group the shared operations.

## Role-based access

Role checks are derived from the signed-in user's `role` (case-insensitive) in `MainLayout.jsx` and `Sidebar.jsx`. These checks control **what the UI shows**; the backend enforces authorization independently and is the source of truth.

| Capability | Roles with access in the UI |
|---|---|
| Dashboard | HR Admin, Administrator, HR Manager (HOD), Employee |
| Organization Setup (Company, Holiday Calendar, Organization Structure) | HR Admin, Administrator |
| Employees (Employee Master), User Management | HR Admin |
| Attendance menu | HR Admin, Administrator, HR Manager (HOD), Employee |
| Daily Attendance, Employee Attendance, WFH Settings, ESSL Attendance Setup | HR Admin, Administrator |
| My Attendance (as separate item) | HR Admin, Administrator |
| WFH Attendance | HR Admin, Administrator, HR Manager (HOD), Employee |
| Leave | HR Admin, Administrator, HR Manager (HOD), Employee |
| Payroll Dashboard, Employee Salary Setup, Monthly Payroll, Payslip Management (register) | HR Admin |
| My Payslips | All other roles |
| Team Members / Team Member Details | Any user the backend reports as a reporting manager (`GET /api/reporting-manager/me`) |
| Profile, Change Password, Notifications | All signed-in users |

Non-HR-Admin users who open a payroll hash are redirected to `payslip-management`. A rejected page shows `AccessDenied`.

## Backend API usage

The frontend calls the following backend route groups (all relative to `HRONE_API_BASE`). See the project README for the complete backend API reference.

| Prefix | Used for |
|---|---|
| `/api/auth/*` | Login, logout, activation, current user, change password, forgot/verify/reset password |
| `/api/users`, `/api/employees` | User accounts and employee records |
| `/api/companies`, `/api/organization/me`, `/api/my-holidays` | Organization data and the employee's holidays |
| `/api/employee-details/*` | Extended employee details |
| `/api/reporting-manager/*` | Reporting-manager status, approver assignment options, team members and their attendance |
| `/api/attendance/*` | Daily, monthly, own and per-user attendance; historical edits; Excel export; WFH endpoints |
| `/api/attendance/essl/*` | ESSL configuration, employee mapping, sync, file import, history and issues |
| `/api/attendance-requests/*` | Attendance-correction requests |
| `/api/leave/*` | Leave, permissions, approvals, policies, reports, leave notifications |
| `/api/notifications/*` | Notifications |
| `/api/payroll/*` | Salary profiles, payroll records, payslips |

One request leaves the platform: `HolidayCalendarSetup.jsx` calls the public Nager.Date service (`https://date.nager.at/api/v3/PublicHolidays/{year}/{countryCode}`) from the browser to import public holidays.

## Attendance UI behavior

The UI presents statuses and times computed by the backend; it must not re-implement attendance policy.

- **Statuses shown** include Present, Half-Day Present (with First Half / Second Half indications), Absent, Leave, Medical Leave, Holiday, Weekly Off, Not Marked, and attendance-issue indicators for incomplete or inconsistent punch records. The calendar legend is defined in `MyAttendanceCalendar.jsx`.
- **Not Marked is not Absent.** Empty check-in/check-out does not by itself mean absence; the backend decides the final status using joining date, holidays, leave, weekly off and payroll locks.
- **Daily Attendance editing** (`Attendance.jsx`) is enabled only for the current day and is disabled for locked records and dates before the employee's joining date. Past dates are handled through `HistoricalAttendanceModal` and the employee's correction requests.
- **Half-day thresholds** are computed by the backend from the employee's configured Half Day Time: below 50% is Absent, 50%–120% (inclusive) is Half-Day Present, above 120% is treated as a full day. See the project README section [Attendance rules](../README.md#attendance-rules).
- **Missing times** are shown as unavailable rather than invented.
- **Excel export** is generated by the backend (`POST /api/attendance/export.xlsx` and `GET /api/attendance/user/{user_id}/export.xlsx`); the frontend only sends the selected employees and month.

## Styling

- Bootstrap 5 CSS and Bootstrap Icons are imported in `main.jsx`.
- `src/index.css` imports `styles-01.css` … `styles-08.css`. `index.css` contains a comment noting that `styles-02.css` still holds an older icon-only mobile sidebar rule that later rules override; keep that in mind when changing sidebar styles.
- The layout is responsive, with a mobile sidebar toggle handled in `MainLayout`.
- `index.html` sets the browser theme colour to `#12345a`.

## Production build and deployment

```bash
cd frontend
npm ci
npm run build      # output in frontend/dist/
```

Deploy the contents of `dist/` to a static host or web server. Requirements:

1. **API URL.** Set `VITE_API_BASE_URL` at build time to the public backend URL, unless the web server serves `/api` from the same origin (in which case it must also proxy `/api` to the backend, and you must still set `VITE_API_BASE_URL` to that origin, because a production build without it falls back to `http://127.0.0.1:8000`).
2. **SPA fallback.** The public pages (`/login`, `/activate`, `/forgot-password`, `/verify-reset-otp`, `/reset-password`, `/wfh-email-verification`) are real URL paths that links in emails point to. Configure the server to return `index.html` for these paths.
3. **CORS.** Add the deployed frontend origin to the backend `CORS_ALLOWED_ORIGINS`.
4. **HTTPS.** Serve the frontend over HTTPS; the WFH page needs camera and location access, which browsers restrict to secure contexts. Set the backend `FRONTEND_URL` to the deployed frontend URL so emailed links point to it.
5. Rebuild whenever `VITE_*` values change.

Do not deploy `node_modules/`, `.env*.local` files or other development files.

## Development conventions

- Route-level screens go in `src/pages/`; reusable UI in `src/components/`; shared API/helpers in `src/services/`.
- To add a page: create the component, add its name to `allowedPages` in `MainLayout.jsx`, render it in `MainLayout`'s page switch with the appropriate permission check, and add a `Sidebar.jsx` entry if it needs navigation.
- Reuse `HRONE_API_BASE` and the authenticated-request pattern (`authenticatedFetch`) instead of hardcoding URLs or headers.
- Treat frontend role checks as presentation only; keep authorization in the backend.
- Do not duplicate attendance, leave or payroll rules in the UI; display backend results.
- Do not commit `dist/`, `node_modules/`, local environment files or employee data.

## Troubleshooting

**Login or data requests fail**
- Confirm the backend is running (default port 8000) and MongoDB is reachable from it.
- In development, check that `VITE_BACKEND_PROXY_TARGET` (if set) points at the backend. In production builds, check `VITE_API_BASE_URL`.
- Check backend CORS settings and the browser network tab. Restart Vite after environment changes.

**Port 5173 already in use**
- `strictPort` is enabled. Stop the other process, or change the port in `vite.config.js` and update the backend CORS allowlist (note that the backend's default private-LAN CORS pattern is tied to port 5173).

**A page or menu item is missing**
- Check the signed-in role against [Role-based access](#role-based-access). Team Members requires the backend to report the user as a reporting manager.
- Opening `#employee-details` or `#team-member-details` directly (for example by refreshing) shows Access Denied because the selected employee is held in memory.

**Signed out after closing the tab or restarting the backend**
- The browser session is stored in `sessionStorage`, and the backend keeps login sessions in memory (see the project README), so a backend restart invalidates all tokens.

**WFH page cannot use the camera or location**
- Use HTTPS (or `localhost`) and grant the browser permissions. See [HTTPS in development](#https-in-development).

**Production links such as `/reset-password` return 404**
- Add the SPA fallback to `index.html` on the web server.

**Build fails**
- Run `npm ci` and read the first reported error; confirm the Node.js version suits Vite 8.
