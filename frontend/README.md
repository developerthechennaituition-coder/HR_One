# HRMS Frontend

The HRMS frontend is a React single-page application (SPA) for employee self-service and HR administration. It talks to the HRMS FastAPI backend through REST endpoints under `/api` and has no server-side code of its own.

For the full system (backend, MongoDB, environment variables, deployment), see the project-level `README.md`.

## Contents

- [Technology stack](#technology-stack)
- [Prerequisites](#prerequisites)
- [Install and run](#install-and-run)
- [Configuration](#configuration)
- [Available scripts](#available-scripts)
- [Project structure](#project-structure)
- [Routing and navigation](#routing-and-navigation)
- [Roles and access](#roles-and-access)
- [Session and API requests](#session-and-api-requests)
- [Production build and deployment](#production-build-and-deployment)
- [Troubleshooting](#troubleshooting)

## Technology stack

| Area | Technology |
|---|---|
| UI | React 19, React DOM 19 |
| Build tool / dev server | Vite 8, `@vitejs/plugin-react` |
| Styling | Bootstrap 5, Bootstrap Icons, project CSS (`index.css`, `styles-01.css` ... `styles-09.css`) |
| Other libraries | `libphonenumber-js` (phone number handling in User Management), `qrcode.react` (QR code for the WFH phone-continuation flow in WFH Attendance), `pdfjs-dist` (PDF rendering in the payslip viewer) |
| Linting | Oxlint (`react` and `oxc` plugins) |
| Language | JavaScript (ES modules and JSX); `@types/react` and `@types/react-dom` are installed for editor support only |

Exact dependency ranges are in `package.json`; resolved versions are in `package-lock.json`.

The page loads the **Inter** font from Google Fonts (`index.html`), so browsers need internet access to show that font; the UI falls back to other fonts otherwise.

## Prerequisites

- Node.js `^20.19.0` or `>=22.12.0` (required by the Vite version in the lockfile).
- npm (installed with Node.js).
- The HRMS FastAPI backend running and connected to MongoDB (see the project-level `README.md`).

```bash
node --version
npm --version
```

## Install and run

From the project directory:

```bash
cd frontend
npm ci
npm run dev
```

Vite listens on `0.0.0.0:5173` with `strictPort: true`, so it fails with an error if port `5173` is already in use instead of choosing another port. Open `http://localhost:5173` on the development machine. To open it from another device on the same network, use `http://<computer-LAN-IP>:5173` and allow the port through the firewall.

Start the backend before using any page that loads or saves data. By default the Vite `/api` proxy forwards to `http://127.0.0.1:8000`.

## Configuration

The API base URL is defined in `src/config/api.js`. No `.env` file is shipped with the frontend; create `frontend/.env.local` only if you need to override the defaults.

| Variable | Read by | Purpose |
|---|---|---|
| `VITE_API_BASE_URL` | Browser bundle (`src/config/api.js`) | Optional absolute API base URL (trailing slashes are removed). If unset: development (`npm run dev`) uses same-origin `/api` requests; a production build uses `http://127.0.0.1:8000`. |
| `VITE_BACKEND_PROXY_TARGET` | `vite.config.js` (dev server and `vite preview`) | Backend target for the `/api` proxy. Default `http://127.0.0.1:8000`. |

Example `frontend/.env.local`:

```dotenv
VITE_BACKEND_PROXY_TARGET=http://127.0.0.1:8000
# Only when the API is intentionally hosted on a different origin:
# VITE_API_BASE_URL=https://api.example.com
```

Restart Vite after changing environment variables. Every `VITE_*` value is embedded in the browser bundle, so never put passwords, database credentials, SMTP credentials, or encryption keys in them.

### HTTPS for local network testing

`vite.config.js` looks for `../certs/localhost-key.pem` and `../certs/localhost.pem` (a `certs` directory next to `frontend/`). If **both** files exist, the dev server uses HTTPS; otherwise it serves HTTP. HTTPS (or `localhost`) is needed for browser camera and geolocation access, which the Work From Home (WFH) attendance pages use from other devices. The browser talks only to the Vite origin and Vite proxies `/api` to the plain-HTTP backend, which avoids mixed-content errors.

## Available scripts

Run from `frontend/`:

| Command | Purpose |
|---|---|
| `npm run dev` | Start the Vite development server on port 5173. |
| `npm run build` | Build the production bundle into `dist/`. |
| `npm run preview` | Serve the production build on port 5173 (`/api` is proxied using the configured backend target). |
| `npm run lint` | Run Oxlint. |

Recommended checks before delivering changes:

```bash
npm run lint
npm run build
```

`package.json` defines no automated frontend test command.

## Project structure

```text
frontend/
├── README.md
├── package.json
├── package-lock.json
├── vite.config.js
├── .oxlintrc.json
├── .gitignore
├── index.html
├── public/                      # favicon.svg, HRMS_title_login.svg, icons.svg
└── src/
    ├── main.jsx                 # Entry point: Bootstrap CSS, Bootstrap Icons, index.css, styles-09.css, <App />
    ├── App.jsx                  # Chooses a public auth screen or the signed-in layout
    ├── index.css                # Imports styles-01.css ... styles-08.css
    ├── styles-01.css ... styles-09.css
    ├── App.css                  # Present but not imported by the current entry point
    ├── auth.js                  # Session reader and user-management permission helper
    ├── logout.js                # Calls the backend logout endpoint and clears the session
    ├── config/
    │   └── api.js               # API base URL (HRONE_API_BASE)
    ├── components/              # Layout, attendance, notification and payslip components
    ├── pages/                   # One file per application page
    ├── services/                # API helpers and attendance/notification utilities
    └── assets/                  # notification.wav (used), plus hero.png, react.svg, vite.svg
```

`styles-09.css` is imported last in `main.jsx` on purpose: it is the design-system layer that must override the earlier stylesheets.

### `src/components/`

| File | Purpose |
|---|---|
| `MainLayout.jsx` | Signed-in shell: hash-based page switching, role checks, header and sidebar. |
| `Header.jsx`, `Sidebar.jsx` | Top bar (notifications, profile menu, Team Members link for reporting managers) and the role-aware navigation menu. |
| `AuthLayout.jsx` | Shared layout for login/activation/password screens. |
| `Attendance*.jsx`, `MyAttendanceCalendar.jsx`, `MyAttendanceRequests.jsx`, `HistoricalAttendanceModal.jsx` | Attendance day breakdown, status summary, monthly calendar, attendance request inbox/submission, and HR historical attendance editing. |
| `AttendanceExportModal.jsx` | Excel export options for attendance. |
| `EsslAdminSettings.jsx` | ESSL biometric configuration, employee mapping, import and sync controls. |
| `MyHolidayCalendar.jsx` | The signed-in employee's holiday calendar. |
| `NotificationBell.jsx`, `NotificationPanel.jsx`, `NotificationTargetSelect.jsx` | Notification bell (refreshes every 3 seconds and plays `notification.wav`), notification list, and audience selector. |
| `PayslipViewer.jsx` | On-screen payslip viewer. |
| `Pagination.jsx` | Shared pagination control. |

### `src/pages/`

| Group | Pages |
|---|---|
| Public / authentication | `Login`, `ActivateAccount`, `ForgotPassword`, `VerifyResetOTP`, `ResetPassword`, `WfhEmailVerification`, `AccessDenied` |
| Home and profile | `Dashboard`, `Profile`, `ChangePassword`, `NotificationManagement` |
| Organization and people | `CompanySetup`, `HolidayCalendarSetup`, `EmployeeMaster`, `EmployeeDetails`, `UserManagement`, `TeamMembers`, `TeamMemberDetails` |
| Attendance | `Attendance` (Daily Attendance, HR Admin), `EmployeeAttendance` (My Attendance for the signed-in user), `UserAttendance` (Employee Attendance: HR Admin views other employees), `WfhAttendance`, `WfhAdminSettings`, `EsslSetup` |
| Leave | `Leave` (leave and permission requests, approvals, policies) |
| Payroll | `EmployeeSalarySetup`, `PayrollWorkspace` (payroll dashboard and monthly payroll), `EmployeePayslips` |

### `src/services/`

| File | Purpose |
|---|---|
| `authenticatedFetch.js` | `fetch` wrapper that adds the session token as a Bearer token. |
| `attendanceRequestApi.js` | Attendance request API calls. |
| `notificationApi.js` | Notification API calls. |
| `payrollApi.js` | Payroll and payslip API calls. |
| `attendanceCalendarRange.js`, `attendanceCalendarStatus.js`, `attendanceHistoryFormat.js` | Calendar range, status mapping/counting, and history formatting helpers. |
| `weeklyOff.js` | Weekly-off helpers. |
| `roleLabels.js` | Maps stored role values to the labels shown in the UI. |

## Routing and navigation

The application does **not** use React Router.

- **Public screens** are chosen from `window.location.pathname` in `App.jsx`: `/login`, `/activate`, `/forgot-password`, `/verify-reset-otp`, `/reset-password`, and `/wfh-email-verification`. Any other path shows the login page when signed out, or the application when signed in.
- **Signed-in pages** are chosen from the URL hash in `MainLayout.jsx`. Valid hashes: `#dashboard`, `#company-setup`, `#holiday-calendar`, `#employees`, `#employee-details`, `#users`, `#attendance`, `#my-attendance`, `#user-attendance`, `#wfh-attendance`, `#wfh-settings`, `#essl-setup`, `#leave`, `#employee-salary-setup`, `#payroll-dashboard`, `#monthly-payroll`, `#payslip-management`, `#notifications`, `#profile`, `#change-password`, `#team-members`, `#team-member-details`, and `#403`. An empty or unknown hash opens `#dashboard`.

## Roles and access

The UI shows three role labels: **HR Admin**, **HOD**, and **Employee**. `src/services/roleLabels.js` maps the stored role value to the label (for example `HR Manager` and `Manager` are shown as `HOD`; `Administrator` and `Super Admin` are shown as `HR Admin`). The stored value is unchanged, and the menu and page checks in `MainLayout.jsx` and `Sidebar.jsx` use the stored value.

Current menu visibility:

| Area | HR Admin | Employee / HOD (`HR Manager`) |
|---|---|---|
| Dashboard | Yes | Yes |
| Organization Setup (Company / Legal Entity, Holiday Calendar) | Yes | No |
| Employees, User Management | Yes | No |
| Attendance menu | Daily Attendance, My Attendance, Employee Attendance, WFH Attendance, WFH Settings, ESSL Attendance Setup | My Attendance, WFH Attendance |
| Leave | Yes | Yes |
| Payroll | Payroll Dashboard, Employee Salary Setup, Monthly Payroll, Payslip Management | My Payslips only |
| Team Members | If the user is a reporting manager | If the user is a reporting manager |

Notes:

- A legacy stored role `Administrator` sees Organization Setup, the attendance admin pages and Leave, but not Employees, User Management or the payroll admin menu.
- Team Members is reached from the profile menu in the header and is only available to users the backend reports as reporting managers (`GET /api/reporting-manager/me`); other users are redirected to `#403`.
- Hiding a menu item is not a security boundary. The backend enforces authorization on every API call.

## Session and API requests

After login the session object returned by `POST /api/auth/login` is stored in browser `sessionStorage` under the key `hrms_session`, so it is cleared when the tab or browser session ends. Authenticated requests send the token in an `Authorization: Bearer <token>` header. Logging out calls the backend logout endpoint and clears the stored session. Do not edit stored session data manually to bypass authorization.

## Production build and deployment

```bash
cd frontend
npm ci
npm run build
```

The deployable static files are written to `frontend/dist/`. The web server must:

1. Serve the contents of `dist/`.
2. Fall back to `index.html` for application paths (for example `/activate` and `/reset-password`, which are opened from emailed links).
3. Forward `/api` to the FastAPI backend.

If the API is on a different origin than the frontend, set `VITE_API_BASE_URL` **before** building and add the frontend origin to the backend `CORS_ALLOWED_ORIGINS`. Without `VITE_API_BASE_URL`, a production build calls `http://127.0.0.1:8000`, which only works when that address is reachable from the user's browser. Use HTTPS in production, because WFH camera and location access require a secure context.

## Troubleshooting

| Symptom | What to check |
|---|---|
| API requests fail | Backend running on port 8000 and MongoDB reachable; `VITE_BACKEND_PROXY_TARGET` or `VITE_API_BASE_URL` correct; browser network tab. |
| CORS errors | Add the exact frontend origin to the backend `CORS_ALLOWED_ORIGINS`, then restart the backend. |
| `Port 5173 is already in use` | Stop the other process; Vite uses `strictPort`. |
| WFH camera or location unavailable | Use `localhost` or HTTPS, grant browser permissions, and check the backend WFH settings. |
| `.env.local` changes have no effect | Restart Vite; confirm the variable name starts with `VITE_`. |
| Refresh or emailed link returns 404 after deployment | Configure the web server's SPA fallback to `index.html`. |
| Redirected to the Access Denied page (`#403`) | The signed-in user's role or reporting-manager status does not allow that page. |
