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
