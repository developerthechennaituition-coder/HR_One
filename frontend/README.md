# HRMS Frontend

The HRMS frontend is a React single-page application for employee self-service and HR administration. It communicates with the FastAPI backend through `/api` REST endpoints.

## Technology stack

| Area | Technology |
|---|---|
| UI | React 19, React DOM 19 |
| Build tool / development server | Vite 8, `@vitejs/plugin-react` |
| Styling | Bootstrap 5, Bootstrap Icons, project CSS stylesheets |
| Other libraries | `libphonenumber-js`, `qrcode.react`, `pdfjs-dist` |
| Linting | Oxlint |
| Language | JavaScript (ES modules and JSX) |

Exact dependency ranges and lockfile-resolved versions are recorded in `package.json` and `package-lock.json`.

## Prerequisites

- Node.js `^20.19.0` or `>=22.12.0` (required by the Vite version in the lockfile).
- npm.
- The HRMS FastAPI backend running and connected to MongoDB.

Check the installed versions:

```bash
node --version
npm --version
```

## Install and run

From the HRMS project directory:

```bash
cd frontend
npm ci
npm run dev
```

Vite binds to `0.0.0.0:5173` and uses `strictPort`, so it reports an error if port `5173` is already occupied rather than silently selecting another port. Open `http://localhost:5173` on the development computer. To access it from another device on the same network, use the computer's LAN IP and allow the port through the local firewall.

Start the backend before using features that load or save HRMS data. By default, the Vite `/api` proxy forwards requests to `http://127.0.0.1:8000`.

## Configuration

The shared API base URL is defined in `src/config/api.js`.

| Variable | Used by | Purpose |
|---|---|---|
| `VITE_API_BASE_URL` | Frontend browser bundle | Optional absolute API base URL. If unset, development uses same-origin `/api` requests; a production build defaults to `http://127.0.0.1:8000`. |
| `VITE_BACKEND_PROXY_TARGET` | Vite config (development and preview) | Overrides the backend target for the `/api` proxy. Defaults to `http://127.0.0.1:8000`. |

Example `frontend/.env.local` for a non-default setup:

```dotenv
VITE_BACKEND_PROXY_TARGET=http://127.0.0.1:8000
# Set only when the API is intentionally hosted at a different origin:
# VITE_API_BASE_URL=https://api.example.com
```

Restart Vite after changing environment variables. Values prefixed with `VITE_` are exposed to browser code; never put passwords, database credentials, SMTP credentials, or encryption keys in frontend environment variables.

### HTTPS for local network testing

`vite.config.js` checks for both `../certs/localhost-key.pem` and `../certs/localhost.pem` (a `certs` directory alongside the `frontend` directory). If both files exist, Vite enables HTTPS; otherwise it serves HTTP. Use a certificate trusted by the test device when testing camera and geolocation features such as WFH attendance. The browser should call the Vite origin; the Vite proxy forwards `/api` to the HTTP backend and avoids mixed-content requests.

## Available scripts

Run these commands from `frontend/`:

| Command | Purpose |
|---|---|
| `npm run dev` | Start the Vite development server. |
| `npm run build` | Generate the production bundle in `dist/`. |
| `npm run preview` | Preview the production build on port `5173`; `/api` is proxied using the configured backend target. |
| `npm run lint` | Run Oxlint. |

Recommended local checks:

```bash
npm run lint
npm run build
```

The project package scripts do not define a frontend automated test command.

## Project structure

```text
frontend/
├── README.md
├── package.json
├── package-lock.json
├── vite.config.js
├── index.html
├── public/                 # Public SVG assets and icons
└── src/
    ├── main.jsx            # React entry point; loads Bootstrap and global styles
    ├── App.jsx             # Session-aware public/authenticated entry point
    ├── App.css
    ├── index.css
    ├── styles-01.css ... styles-09.css
    ├── auth.js              # Session and user-management permission helpers
    ├── logout.js
    ├── config/api.js        # API base URL
    ├── components/          # Shared layout, attendance, notification, and UI components
    ├── pages/               # HRMS application pages
    ├── services/            # API helpers and attendance/notification utilities
    └── assets/               # Images, icons, and notification sound
```

### Main application areas

The frontend includes dashboards; company and organization setup; employee master and employee details; user management; attendance and attendance requests; My Attendance; WFH attendance and administration; ESSL biometric setup/import; holiday calendars; leave and permissions; notifications; salary setup; payroll; payslips; team member views; profile and password management.

The authenticated application is organized by `MainLayout.jsx`. Navigation uses URL hashes (for example, `#dashboard`, `#attendance`, `#my-attendance`, and `#leave`); public authentication and verification screens use URL paths such as `/login`, `/activate`, `/forgot-password`, `/verify-reset-otp`, `/reset-password`, and `/wfh-email-verification`. The application does not use React Router.

### Session and API requests

The signed-in session is stored in browser `sessionStorage` under `hrms_session`. Authenticated requests use the session token as a Bearer token. Logging out calls the backend logout endpoint and clears the stored session. Do not manually edit session data to bypass authorization.

The user-facing role labels are HR Admin, HOD, and Employee. `src/services/roleLabels.js` maps several legacy stored role identifiers to these display labels; the underlying stored value may therefore differ from the label shown in the interface. Actual access must be enforced by the backend as well as the UI.

## Production build and deployment

Create the static production bundle:

```bash
cd frontend
npm ci
npm run build
```

The deployable files are written to `frontend/dist/`. Configure the production web server to serve the contents of `dist/`, provide SPA fallback to `index.html` for application paths, and route `/api` to the FastAPI backend. If the API is hosted on a separate origin rather than behind the same web server, set `VITE_API_BASE_URL` before building and configure backend CORS for the deployed frontend origin.

The default production API fallback in `src/config/api.js` is `http://127.0.0.1:8000`; this is appropriate only when that address is reachable from the browser. For a real deployment, configure the API base URL or same-origin reverse proxy deliberately.

## Troubleshooting

- **API requests fail:** Confirm the backend is running, MongoDB is reachable, and the Vite proxy target or `VITE_API_BASE_URL` is correct.
- **CORS errors:** Add the exact frontend origin to the backend's `CORS_ALLOWED_ORIGINS` configuration. See the project-level README.
- **Port 5173 is busy:** Stop the process using the port; Vite is configured with `strictPort`.
- **WFH camera or location is unavailable:** Test from localhost or a trusted HTTPS origin and grant browser camera/location permissions.
- **Changes to `.env.local` have no effect:** Restart the Vite server and verify the variable name and value.
- **Production refresh returns 404:** Configure the hosting server's SPA fallback to `index.html` and proxy `/api` separately.

For backend setup, MongoDB configuration, environment variables, and full-system operation, see the project-level `README.md`.
