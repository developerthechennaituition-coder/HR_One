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
    ├── .venv/
    ├── app/
    │   ├── db/
    │   │   └── mongodb.py
    │   └── main.py
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
.\.venv\Scripts\Activate.ps1
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

## Current Development Status

The technical foundation and base UI shell are complete.

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
