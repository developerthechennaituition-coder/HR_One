"""Read-only MongoDB connectivity/collection smoke check for HR One WFH.

Run from backend/ after configuring MONGODB_URL and MONGODB_DATABASE in .env.
This script does not create, update, or delete database records.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import PyMongoError

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
uri = os.getenv("MONGODB_URL")
database_name = os.getenv("MONGODB_DATABASE")
if not uri or not database_name:
    print("FAIL: Set MONGODB_URL and MONGODB_DATABASE in backend/.env before running this check.")
    sys.exit(2)

try:
    with MongoClient(uri, serverSelectionTimeoutMS=5000) as client:
        client.admin.command("ping")
        db = client[database_name]
        existing = set(db.list_collection_names())
        required = {"users", "attendance"}
        missing = sorted(required - existing)
        if missing:
            print("FAIL: MongoDB connection succeeded, but required collections are missing: " + ", ".join(missing))
            print("Confirm the database name and run the normal HR One database initialization before retrying.")
            sys.exit(1)
        print("PASS: MongoDB connection succeeded.")
        print(f"PASS: Database '{database_name}' contains required collections: users, attendance.")
        print("NOTE: This is a read-only connectivity check, not a full authenticated attendance API test.")
except PyMongoError as exc:
    print(f"FAIL: MongoDB smoke check failed ({type(exc).__name__}). Check the server, credentials, network access, and database name.")
    sys.exit(1)
