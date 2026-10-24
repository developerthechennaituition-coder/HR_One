# HRMS Project

Human Resource Management System built from scratch using React,
Bootstrap, Python FastAPI, and MongoDB.

## Technology Stack

-   Frontend: React + Vite
-   UI: HTML, CSS, Bootstrap, Bootstrap Icons
-   Backend: Python + FastAPI
-   Database: MongoDB
-   Version Control: Git

## Project Folder Structure

``` text
F:\HRMS-Project
├── .git/
├── .gitignore
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── Header.jsx
│   │   │   ├── MainLayout.jsx
│   │   │   └── Sidebar.jsx
│   │   ├── App.jsx
│   │   ├── main.jsx
│   │   └── index.css
│   ├── package.json
│   └── ...
└── backend/
     ├── venv/
    ├── app/
    │   ├── db/
    │   │   └── mongodb.py
    │   └── main.py
     ├── tests/
     │   └── test_companies_api.py
    ├── .env
    └── requirements.txt
```

## Setup and Run Commands

### Activation Email Configuration

Copy `backend/.env.example` to `backend/.env` and set the SMTP variables when
email delivery is available. Without SMTP credentials, account creation keeps
the user inactive and prints a development-only activation link to the local
backend console; it does not claim that an email was sent.

### Frontend

``` powershell
cd F:\HRMS-Project\frontend
npm install
npm run dev
```

Frontend: `http://localhost:5173/`

If PowerShell blocks `npm.ps1`, use:

``` powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Then run `npm run dev` again.

### Backend

Open a separate terminal:

``` powershell
cd F:\HRMS-Project\backend
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Backend: `http://127.0.0.1:8000/`

Swagger UI: `http://127.0.0.1:8000/docs`

Health API: `http://127.0.0.1:8000/health`

### Work From Home Attendance (Local Face Processing)

WFH check-in and check-out use the common `attendance` collection and the
existing Attendance page/API. WFO records continue to use the ESSL source;
WFH records use the `WFH_FACE_GPS` source and `WFH` attendance type. The
employee WFH Attendance screen performs server-validated geofence and
challenge-response face/liveness checks. HR Admin and Administrator users can
configure employee eligibility and approved home locations from WFH Settings.

The optional local vision dependencies can still be installed into a Python 3.12 environment because
the selected MediaPipe release does not provide a wheel for the repository's
current Python 3.14 environment:

``` powershell
py -3.12 -m venv backend\venv-wfh
backend\venv-wfh\Scripts\python.exe -m pip install -r backend\requirements.txt
backend\venv-wfh\Scripts\python.exe -m pip install -r backend\requirements-wfh.txt
```

On Windows, `face-recognition` may require CMake and the Visual C++ Build Tools
to build its `dlib` dependency. Create a Fernet key for encrypting the stored
face template:

``` powershell
backend\venv-wfh\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copy that key into `backend/.env` as `WFH_TEMPLATE_ENCRYPTION_KEY` and set
`WFH_ATTENDANCE_ENABLED=true`. Keep the key
outside source control and back it up securely; losing it makes enrolled
templates unreadable. WFH location accuracy defaults to 100 meters. You may set
`WFH_MAX_LOCATION_ACCURACY_METERS` to a stricter positive value; the server
caps it at 100 meters. Browser location readings older than
`WFH_MAX_LOCATION_AGE_SECONDS` (default 120 seconds) are rejected. The
`location_timestamp` API field uses the browser's Unix-epoch milliseconds. Set
`CORS_ALLOWED_ORIGINS` to the exact frontend
origin(s). Restart the API using the new environment:

``` powershell
Set-Location backend
.\venv-wfh\Scripts\python.exe -m uvicorn app.main:app --reload
```

Registration and attendance retain only an encrypted face embedding; uploaded
camera frames are processed in memory and discarded. Liveness uses a fresh,
single-use randomized blink/head-turn sequence checked by the backend. The
sequence and face-match tolerance can be made stricter but not weaker than the
implemented defaults. Liveness is still a lightweight heuristic, not a
certified anti-spoofing model: it rejects still images and simple non-responsive
replays but cannot guarantee resistance to sophisticated video replay or
virtual-camera spoofing.
The browser-reported GPS and accuracy are also not hardware-attested and can be
spoofed on compromised devices; the backend independently recalculates the
geofence distance and rejects low reported accuracy.

### Work From Office Attendance (ESSL)

HR Admin or Administrator configures the company ESSL REST/JSON endpoint from
the Attendance page. The integration intentionally does not assume a vendor
payload: configure the endpoint method, authentication, optional query/body
values and date parameter names, response punch-list path, employee-ID path,
timestamp path, and optional punch/device ID paths to match the company API.
Naive timestamps are interpreted in `Asia/Kolkata`; timestamps with offsets are
converted to the company timezone before grouping by attendance date.

Before saving an ESSL configuration, set `ESSL_CONFIG_ENCRYPTION_KEY` in
`backend/.env` to a private Fernet key. Generate one with the command below and
keep it outside source control; the server encrypts API credentials and custom
request values before storing them in MongoDB, and never returns them to the UI:

``` powershell
backend\venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Map each HRMS employee to the ESSL employee ID in the same section. The ID is
stored on the existing user record, is unique after trim/case normalization,
and is never assumed to equal a MongoDB ObjectId. Unmapped and invalid punches
are retained for HR review without creating attendance for another employee.

`Sync ESSL Attendance` supports a custom date range. Every successful response
is grouped by mapped employee and local date. The earliest distinct punch is
Check-In; the latest distinct punch is Check-Out; one distinct punch leaves
Check-Out blank. Daily records are upserted into the existing `attendance`
collection and tagged `attendance_type=OFFICE` and
`attendance_source=ESSL`. A unique user/date index and the existing atomic
upsert prevent duplicate daily attendance. Existing manual/leave attendance is
preserved; manual changes to ESSL attendance are recorded in `audit_logs`.

The API uses JSON over HTTP(S) and must return all punches for the requested
window in one response; pagination is not auto-followed. If the company
endpoint uses XML, pagination, or a non-REST protocol, it needs an explicit
adapter before it can be synchronized. No sample/fake company response is built
into the application.

## MongoDB

Local MongoDB configuration is stored in `backend/.env`:

``` env
MONGODB_URL=mongodb://127.0.0.1:27017
MONGODB_DATABASE=hrms_db
```

MongoDB must be running before testing the backend database connection.

## S1 - F001 Company / Legal Entity Setup

Status: **Initial implementation completed.**

Implemented capabilities:

-   Company / Legal Entity UI in the HRMS shell.
-   Create company flow from the React form.
-   Company listing loaded from MongoDB through FastAPI.
-   Edit and update company flow.
-   Reversible company deactivation flow.
-   FastAPI CRUD/status endpoints.
-   MongoDB persistence in `hrms_db.companies`.
-   React-to-FastAPI-to-MongoDB integration.
-   Basic field validation, loading states, and error messages.
-   Responsive table and form layout for desktop and mobile.
-   Dynamic header title for the active page.

### Initial Company Document

The current implementation uses this initial schema. It is not a final
production schema and may change after business confirmation:

``` json
{
     "company": "...",
     "legal_entity": "...",
     "branch": "...",
     "location": "...",
     "is_active": true,
     "created_at": "...",
     "updated_at": "..."
}
```

The `companies` collection is initialized when the FastAPI application starts.
MongoDB validation requires the four initial business fields:
`company`, `legal_entity`, `branch`, and `location`.

### Company API

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `POST` | `/api/companies` | Create a company |
| `GET` | `/api/companies` | List active companies |
| `GET` | `/api/companies/{id}` | Get one active company |
| `PUT` | `/api/companies/{id}` | Update an active company |
| `DELETE` | `/api/companies/{id}` | Deactivate a company |

Swagger documentation is available at `http://127.0.0.1:8000/docs`.

### Validation and Testing

Backend validation tests are located in
`backend/tests/test_companies_api.py`. They cover valid payloads, blank
fields, create/update behavior, deactivation, invalid IDs, backend errors,
and API route registration.

Run them from the backend directory:

``` powershell
cd F:\HRMS-Project\backend
.\venv\Scripts\python.exe -m unittest discover -s tests -v
```

Manual verification should also confirm that a saved company remains visible
after refreshing the browser and that edited values remain after refresh.

### Unconfirmed Business Rules

-   **Permanent deletion policy: TBD / Business Confirmation Required.**
-   The current `DELETE` endpoint only sets `is_active` to `false` and retains
     the MongoDB document.
-   Restore/reactivation behavior is not defined yet.
-   Final production fields, uniqueness rules, audit requirements, and
     authorization rules require business confirmation.

## Today's Completed Items

## Step 10 - Integration and QA

### QA commands

Run the complete backend suite:

``` powershell
cd F:\HRMS-Project\backend
.\venv\Scripts\python.exe -m unittest discover -s tests -v
```

Run the frontend checks:

``` powershell
cd F:\HRMS-Project\frontend
npm run lint
npm run build
```

With MongoDB, FastAPI, and Vite running, verify:

- `GET http://127.0.0.1:8000/health` returns `status: ok`.
- `GET http://127.0.0.1:8000/openapi.json` is available.
- `http://127.0.0.1:5173/#company-setup` opens F001.
- `http://127.0.0.1:5173/#organization-setup` opens F002.
- `http://127.0.0.1:5173/#holiday-calendar` opens F005.

### Demo flow

1. Open **Company / Legal Entity**, create a company with location `Chennai`, edit it, refresh, and confirm the saved values remain.
2. Open **Organization Structure**, create a business unit, a top-level department, a child department, a team, and a designation. Confirm the hierarchy and company filter show the relationships.
3. Refresh the page and confirm the F002 records reload from MongoDB. Edit a department and deactivate a test team to demonstrate update and soft-deactivation behavior.
4. Open **Holiday Calendar**, create a 2026 calendar for the same company and location, add two holidays, edit one, remove the other, and refresh to confirm persistence.
5. Assign the calendar to two active employees. Confirm the assignment list displays both employees and that duplicate assignment or unknown employee errors are shown clearly.
6. Try a wrong company location, a holiday from a different year, a malformed ID, and a blank required field to demonstrate clean validation errors without corrupt records.

### Organization Setup authorization and completion

Organization endpoints now use the existing bearer session token. HR Admin and
Administrator may create, edit, publish, assign, deactivate, or cancel
organization data. HR Manager and Manager can view the employee hierarchy and
published holidays; employees can view their own profile data and published
eligible holidays. Browser controls mirror these permissions, but the backend
middleware is authoritative.

#### Company / Legal Entity

- One company is allowed. `GET /api/companies/availability` returns
  `{"can_create": true|false}` for the Add Company control.
- Create, update, and delete/deactivate endpoints require HR Admin or
  Administrator. Deactivation is soft and retains company references and
  historical HR records.
- `companies_singleton_unique` enforces the fixed primary-company key. The
  application also rejects company creation after a company document has been
  retained, including after deactivation.

#### Holiday Calendar

- Create one active calendar for each year. Calendar creation enforces the
  active-year constraint both in the API and with the
  `holiday_calendar_active_year_unique` MongoDB index.
- A calendar starts as `Draft`; future/today holiday dates only may be added or
  edited. Publishing makes the calendar and its eligible entries employee
  visible. A published calendar cannot be silently edited or deleted.
- Cancelling a future published holiday requires a reason and retains its
  record as `Cancelled`. Amending a published holiday requires a reason. The
  change is audited and triggers an idempotent, seven-day notification to all
  active HRMS users only after the holiday write succeeds.
- Holidays support `All Employees`, `Selected Religions`, and `Selected
  Employee Groups`. Group eligibility matches the employee's stored
  department, designation, or team; it does not grant leave or change
  attendance.
- Twelve months are shown in the setup page and employee profile. The calendar
  form collects country/state. “Suggest public holidays” uses the public
  Nager.Date API without an API key. Suggestions are review-only and are never
  automatically designated company holidays. Nager.Date may not provide
  state-specific data for every country; the HR Admin must verify regional
  entries and company policy before saving/publishing.
- `ORG_TIMEZONE` controls date validation and reminder dates; it defaults to
  `Asia/Kolkata`. The scheduler is started by FastAPI and persists reminders
  and expiry timestamps in `holiday_calendar_reminders`. Reminders are due on
  Dec 1, Dec 16, Dec 31, and Jan 15, target HR Admin users only, expire after
  15 days, and stop when the target year's calendar is saved.
- Holiday mutation audit records are stored in
  `organization_audit_history`; the calendar also embeds its atomic publish
  audit entry.

#### Organization Structure and Employee Profile

- The existing business unit/department/team/designation setup remains
  available. A separate employee chart is built from employee records and
  `users.reporting_manager_id`, with search, department/designation filters,
  expandable reporting branches, missing-manager warnings, and cycle detection.
- `/api/organization/me` supplies the employee's existing company/employee
  details and the published assigned/current-year holiday calendar. The
  Profile page renders organization details, upcoming holidays, and the
  read-only twelve-month calendar.

#### API examples

All requests use `Authorization: Bearer <session-token>`.

```http
POST /api/holiday-calendars
Content-Type: application/json

{
  "company_id": "<company-id>",
  "location": "Chennai",
  "calendar_name": "Tamil Nadu 2027",
  "calendar_year": 2027,
  "country": "India",
  "state": "Tamil Nadu"
}
```

Add an eligible draft holiday through
`POST /api/holiday-calendars/{calendar_id}/holidays` with
`calendar_id`, `holiday_date` (`YYYY-MM-DD`), `holiday_name`, `holiday_type`,
`is_company_holiday`, `applicability`, `applicable_religions`, and/or
`applicable_employee_groups`. Publish with
`POST /api/holiday-calendars/{calendar_id}/publish`. Published edits use
`PUT /api/holidays/{holiday_id}?reason=<url-encoded-reason>`; cancellation uses
`DELETE /api/holidays/{holiday_id}?reason=<url-encoded-reason>`.

The employee reporting API is
`GET /api/organization/hierarchy?search=<text>&department=<name>&designation=<name>`.
The profile feed is `GET /api/organization/me`.

#### Database migration and verification

Back up MongoDB before deploying. Startup creates unique indexes for the
primary company, one active calendar per year, one active employee calendar
assignment, and duplicate holiday name/date entries within a calendar. If
existing data violates an index, MongoDB will reject index creation and startup
will fail rather than silently deleting or merging company/calendar history.
Before restart, review duplicate active calendars by year, consolidate their
entries and assignments according to HR policy, and retain the old documents
for historical reporting. Legacy calendars without status are migrated as
published to preserve their prior employee visibility; their missing country
and state are recorded as “Not specified”.

Run the backend holiday tests with:

```powershell
cd backend
.\venv312\Scripts\python.exe -m unittest discover -s tests -p "test_holiday*.py"
```

Run the frontend checks with `npm run build` and `npm run lint` from
`frontend`. The reminders and external holiday suggestions require the backend
process to run and the browser to have outbound HTTPS access to `date.nager.at`.

### Integration boundary

F001 remains the parent master context for F002 and F005. F002 references active companies and employees, while F005 references active companies, employees, holiday calendars, and holiday entries. Deletes are soft deactivations, so refreshes show active records while historical documents remain stored.

### Project Foundation

-   Created the HRMS project root folder.
-   Initialized Git repository.
-   Created React + Vite frontend.
-   Installed Bootstrap.
-   Installed Bootstrap Icons.
-   Created Python virtual environment.
-   Installed FastAPI, Uvicorn, PyMongo, and python-dotenv.
-   Created `requirements.txt`.

### Backend Foundation

-   Created FastAPI application.
-   Created `/health` endpoint.
-   Connected FastAPI to MongoDB.
-   Configured database `hrms_db`.
-   Enabled CORS for the local React frontend.
-   Verified FastAPI Swagger UI.
-   Verified the health API.

### Frontend Foundation

-   Created reusable HRMS base UI shell.
-   Created Sidebar component.
-   Created Header component.
-   Created MainLayout component.
-   Added HRMS navigation items.
-   Added Bootstrap Icons.
-   Added responsive CSS.
-   Added basic dashboard summary cards.

### Integration Verification

React successfully communicates with FastAPI.

Verified message:

``` text
Backend: HRMS Backend is running
```

Basic architecture:

``` text
React + Vite
     |
     | HTTP API
     v
FastAPI
     |
     | PyMongo
     v
MongoDB
```

     ### F001 Integration Verification

     The Company Setup screen has been verified through the full flow:

     ``` text
     React company form
          |
          | GET / POST / PUT / DELETE
          v
     FastAPI company endpoints
          |
          v
     MongoDB hrms_db.companies
          |
          v
     Company table after load or refresh
     ```

## Current Development Status

The technical foundation, base UI shell, and the initial F001 Company / Legal
Entity module are complete.

The following business modules are not implemented yet:

-   Employee Master
-   Attendance
-   Leave
-   Payroll
-   Onboarding
-   Separation
-   Reports

These will be developed in later steps.

## Git Commands

``` powershell
cd F:\HRMS-Project
git status
git add .
git commit -m "Initial HRMS project foundation"
```

## Environment and Sensitive Files

Do not commit environment values or generated dependencies. The root
`.gitignore` excludes:

``` text
.env
.venv/
node_modules/
dist/
__pycache__/
*.pyc
```

## Normal Development

Keep two terminals open.

### Terminal 1 --- Backend

``` powershell
cd F:\HRMS-Project\backend
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload
```

### Terminal 2 --- Frontend

``` powershell
cd F:\HRMS-Project\frontend
npm run dev
```

Open `http://localhost:5173/`.
