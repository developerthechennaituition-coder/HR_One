from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db.mongodb import client, MONGODB_DATABASE


app = FastAPI(title="HRMS Backend")


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health_check():
    client.admin.command("ping")

    return {
        "status": "ok",
        "message": "HRMS Backend is running",
        "database": MONGODB_DATABASE
    }