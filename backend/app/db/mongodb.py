"""MongoDB connection, validation schemas, indexes, and seed data.

This module owns the database setup used by the FastAPI application.  The
``ensure_*`` helpers are intentionally idempotent so they can run during
application startup without replacing existing data.
"""

import os

from dotenv import load_dotenv
from pymongo import MongoClient


# Read the local ``backend/.env`` file before creating the MongoDB client.
load_dotenv()

MONGODB_URL = os.getenv("MONGODB_URL")
MONGODB_DATABASE = os.getenv("MONGODB_DATABASE")

# The client and database are shared by all API handlers in ``main.py``.
client = MongoClient(MONGODB_URL)

db = client[MONGODB_DATABASE]


COMPANIES_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["company", "legal_entity", "branch", "location"],
        "properties": {
            "company": {"bsonType": "string"},
            "legal_entity": {"bsonType": "string"},
            "branch": {"bsonType": "string"},
            "location": {"bsonType": "string"},
        },
    }
}

EMPLOYEES_VALIDATOR = {
	"$jsonSchema": {
		"bsonType": "object",
		"required": [
			"employee_id",
			"employee_name",
			"email",
			"phone",
			"department",
			"designation",
			"status",
		],
		"properties": {
			"employee_id": {"bsonType": "string"},
			"employee_name": {"bsonType": "string"},
			"email": {"bsonType": "string"},
			"phone": {"bsonType": "string"},
			"department": {"bsonType": "string"},
			"designation": {"bsonType": "string"},
			"status": {"bsonType": "string"},
		},
	}
}

USERS_VALIDATOR = {
	"$jsonSchema": {
		"bsonType": "object",
		"required": ["name", "work_email", "mobile", "role"],
		"properties": {
			"employee_id": {"bsonType": "string"},
			"name": {"bsonType": "string"},
			"work_email": {"bsonType": "string"},
			"country": {"bsonType": "string"},
			"country_iso": {"bsonType": ["string", "null"]},
			"country_code": {"bsonType": "string"},
			"mobile": {"bsonType": "string"},
			"role": {"bsonType": "string"},
			"is_wfh_enabled": {"bsonType": "bool"},
			"home_latitude": {"bsonType": ["double", "int", "long", "null"]},
			"home_longitude": {"bsonType": ["double", "int", "long", "null"]},
			"allowed_radius": {"bsonType": ["double", "int", "long"]},
			"home_location_accuracy_meters": {"bsonType": ["double", "int", "long", "null"]},
			"home_location_timestamp": {"bsonType": ["double", "int", "long", "null"]},
			"home_location_verification_method": {"bsonType": ["string", "null"]},
			"home_location_manually_verified": {"bsonType": "bool"},
			"home_location_updated_by": {"bsonType": ["string", "null"]},
			"home_location_updated_at": {"bsonType": ["date", "null"]},
			"role_id": {"bsonType": "string"},
			"password_hash": {"bsonType": "string"},
			"is_active": {"bsonType": "bool"},
			"failed_attempts": {"bsonType": "int"},
			"locked_until": {"bsonType": ["date", "null"]},
			"last_login": {"bsonType": ["date", "null"]},
			"login_count": {"bsonType": "int"},
			"created_at": {"bsonType": "date"},
			"updated_at": {"bsonType": "date"},
		}
	}
}

ROLES_VALIDATOR = {
	"$jsonSchema": {
		"bsonType": "object",
		"required": ["name", "status", "created_at", "updated_at"],
		"properties": {
			"name": {"bsonType": "string"},
			"status": {"bsonType": "string"},
			"created_at": {"bsonType": "date"},
			"updated_at": {"bsonType": "date"},
		}
	}
}

PERMISSIONS_VALIDATOR = {
	"$jsonSchema": {
		"bsonType": "object",
		"required": ["role_id", "permission"],
		"properties": {
			"role_id": {"bsonType": "string"},
			"permission": {"bsonType": "string"},
		}
	}
}

AUDIT_LOGS_VALIDATOR = {
	"$jsonSchema": {
		"bsonType": "object",
		"required": ["user_id", "action", "timestamp"],
		"properties": {
			"user_id": {"bsonType": "string"},
			"action": {"bsonType": "string"},
			"old_value": {},
			"new_value": {},
			"ip": {"bsonType": "string"},
			"timestamp": {"bsonType": "date"},
		}
	}
}

BUSINESS_UNITS_VALIDATOR = {
	"$jsonSchema": {
		"bsonType": "object",
		"required": ["company_id", "business_unit_name"],
		"properties": {
			"company_id": {"bsonType": "string"},
			"business_unit_name": {"bsonType": "string"},
			"manager_employee_id": {"bsonType": ["string", "null"]},
			"is_active": {"bsonType": "bool"},
		},
	}
}

DEPARTMENTS_VALIDATOR = {
	"$jsonSchema": {
		"bsonType": "object",
		"required": ["company_id", "department_name"],
		"properties": {
			"company_id": {"bsonType": "string"},
			"department_name": {"bsonType": "string"},
			"parent_department_id": {"bsonType": ["string", "null"]},
			"manager_employee_id": {"bsonType": ["string", "null"]},
			"is_active": {"bsonType": "bool"},
		},
	}
}

TEAMS_VALIDATOR = {
	"$jsonSchema": {
		"bsonType": "object",
		"required": ["company_id", "department_id", "team_name"],
		"properties": {
			"company_id": {"bsonType": "string"},
			"department_id": {"bsonType": "string"},
			"team_name": {"bsonType": "string"},
			"manager_employee_id": {"bsonType": ["string", "null"]},
			"is_active": {"bsonType": "bool"},
		},
	}
}

DESIGNATIONS_VALIDATOR = {
	"$jsonSchema": {
		"bsonType": "object",
		"required": ["company_id", "designation_name"],
		"properties": {
			"company_id": {"bsonType": "string"},
			"designation_name": {"bsonType": "string"},
			"description": {"bsonType": ["string", "null"]},
			"is_active": {"bsonType": "bool"},
		},
	}
}

HOLIDAY_CALENDARS_VALIDATOR = {
	"$jsonSchema": {
		"bsonType": "object",
		"required": ["company_id", "location", "calendar_name", "calendar_year"],
		"properties": {
			"company_id": {"bsonType": "string"},
			"location": {"bsonType": "string"},
			"calendar_name": {"bsonType": "string"},
			"calendar_year": {"bsonType": "int"},
			"description": {"bsonType": ["string", "null"]},
			"is_active": {"bsonType": "bool"},
		}
	}
}

HOLIDAYS_VALIDATOR = {
	"$jsonSchema": {
		"bsonType": "object",
		"required": ["calendar_id", "holiday_date", "holiday_name"],
		"properties": {
			"calendar_id": {"bsonType": "string"},
			"holiday_date": {"bsonType": "date"},
			"holiday_name": {"bsonType": "string"},
			"holiday_type": {"bsonType": "string"},
			"description": {"bsonType": ["string", "null"]},
			"is_optional": {"bsonType": "bool"},
			"is_active": {"bsonType": "bool"},
		}
	}
}


EMPLOYEE_HOLIDAY_CALENDARS_VALIDATOR = {
	"$jsonSchema": {
		"bsonType": "object",
		"required": ["employee_id", "calendar_id"],
		"properties": {
			"employee_id": {"bsonType": "string"},
			"calendar_id": {"bsonType": "string"},
			"assigned_at": {"bsonType": "date"},
			"is_active": {"bsonType": "bool"},
		}
	}
}

# ============================================================
# LEAVE COLLECTION VALIDATORS
# ============================================================

LEAVE_TYPES_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": [
            "code",
            "name",
            "is_paid",
            "allow_half_day",
            "is_active",
        ],
        "properties": {
            "code": {
                "bsonType": "string",
            },
            "name": {
                "bsonType": "string",
            },
            "description": {
                "bsonType": [
                    "string",
                    "null",
                ],
            },
            "is_paid": {
                "bsonType": "bool",
            },
            "allow_half_day": {
                "bsonType": "bool",
            },
            "requires_attachment": {
                "bsonType": "bool",
            },
            "is_active": {
                "bsonType": "bool",
            },
        },
    }
}


LEAVE_POLICIES_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": [
            "leave_type_id",
            "annual_entitlement",
            "carry_forward_allowed",
            "is_active",
        ],
        "properties": {
            "leave_type_id": {
                "bsonType": "objectId",
            },
            "annual_entitlement": {
                "bsonType": "int",
            },
            "carry_forward_allowed": {
                "bsonType": "bool",
            },
            "max_carry_forward": {
                "bsonType": "int",
            },
            "accrual_enabled": {
                "bsonType": "bool",
            },
            "accrual_frequency": {
                "bsonType": "string",
            },
            "is_active": {
                "bsonType": "bool",
            },
        },
    }
}


LEAVE_BALANCES_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": [
            "user_id",
            "leave_type_id",
            "year",
            "entitled",
            "used",
            "pending",
        ],
        "properties": {
            "user_id": {
                "bsonType": "objectId",
            },
            "leave_type_id": {
                "bsonType": "objectId",
            },
            "year": {
                "bsonType": "int",
            },
            "entitled": {
                "bsonType": "int",
            },
            "used": {
                "bsonType": "int",
            },
            "pending": {
                "bsonType": "int",
            },
        },
    }
}


LEAVE_REQUESTS_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": [
            "user_id",
            "leave_type_id",
            "from_date",
            "to_date",
            "days",
            "reason",
            "status",
            "created_at",
        ],
        "properties": {
            "user_id": {
                "bsonType": "objectId",
            },
            "leave_type_id": {
                "bsonType": "objectId",
            },
            "from_date": {
                "bsonType": "date",
            },
            "to_date": {
                "bsonType": "date",
            },
            "days": {
                "bsonType": "int",
            },
            "reason": {
                "bsonType": "string",
            },
            "status": {
                "bsonType": "string",
            },
            "approver_id": {
                "bsonType": [
                    "objectId",
                    "null",
                ],
            },
            "approval_reason": {
                "bsonType": [
                    "string",
                    "null",
                ],
            },
            "created_at": {
                "bsonType": "date",
            },
            "updated_at": {
                "bsonType": "date",
            },
        },
    }
}

def ensure_companies_collection():
	if "companies" in db.list_collection_names():
		db.command(
			"collMod",
			"companies",
			validator=COMPANIES_VALIDATOR,
			validationLevel="moderate",
			validationAction="error",
		)
		return

	db.create_collection(
		"companies",
		validator=COMPANIES_VALIDATOR,
		validationLevel="moderate",
		validationAction="error",
	)


def ensure_employees_collection():
	if "employees" in db.list_collection_names():
		db.command(
			"collMod",
			"employees",
			validator=EMPLOYEES_VALIDATOR,
			validationLevel="moderate",
			validationAction="error",
		)
		return

	db.create_collection(
		"employees",
		validator=EMPLOYEES_VALIDATOR,
		validationLevel="moderate",
		validationAction="error",
	)


def ensure_business_units_collection():
	if "business_units" in db.list_collection_names():
		db.command(
			"collMod",
			"business_units",
			validator=BUSINESS_UNITS_VALIDATOR,
			validationLevel="moderate",
			validationAction="error",
		)
		return

	db.create_collection(
		"business_units",
		validator=BUSINESS_UNITS_VALIDATOR,
		validationLevel="moderate",
		validationAction="error",
	)


def ensure_departments_collection():
	if "departments" in db.list_collection_names():
		db.command(
			"collMod",
			"departments",
			validator=DEPARTMENTS_VALIDATOR,
			validationLevel="moderate",
			validationAction="error",
		)
		return

	db.create_collection(
		"departments",
		validator=DEPARTMENTS_VALIDATOR,
		validationLevel="moderate",
		validationAction="error",
	)


def ensure_teams_collection():
	if "teams" in db.list_collection_names():
		db.command(
			"collMod",
			"teams",
			validator=TEAMS_VALIDATOR,
			validationLevel="moderate",
			validationAction="error",
		)
		return

	db.create_collection(
		"teams",
		validator=TEAMS_VALIDATOR,
		validationLevel="moderate",
		validationAction="error",
	)


def ensure_designations_collection():
	if "designations" in db.list_collection_names():
		db.command(
			"collMod",
			"designations",
			validator=DESIGNATIONS_VALIDATOR,
			validationLevel="moderate",
			validationAction="error",
		)
		return

	db.create_collection(
		"designations",
		validator=DESIGNATIONS_VALIDATOR,
		validationLevel="moderate",
		validationAction="error",
	)


def ensure_holiday_calendars_collection():
	if "holiday_calendars" in db.list_collection_names():
		db.command(
			"collMod",
			"holiday_calendars",
			validator=HOLIDAY_CALENDARS_VALIDATOR,
			validationLevel="moderate",
			validationAction="error",
		)
		return

	db.create_collection(
		"holiday_calendars",
		validator=HOLIDAY_CALENDARS_VALIDATOR,
		validationLevel="moderate",
		validationAction="error",
	)


def ensure_holidays_collection():
	if "holidays" in db.list_collection_names():
		db.command(
			"collMod",
			"holidays",
			validator=HOLIDAYS_VALIDATOR,
			validationLevel="moderate",
			validationAction="error",
		)
		return

	db.create_collection(
		"holidays",
		validator=HOLIDAYS_VALIDATOR,
		validationLevel="moderate",
		validationAction="error",
	)


def ensure_employee_holiday_calendars_collection():
	if "employee_holiday_calendars" in db.list_collection_names():
		db.command(
			"collMod",
			"employee_holiday_calendars",
			validator=EMPLOYEE_HOLIDAY_CALENDARS_VALIDATOR,
			validationLevel="moderate",
			validationAction="error",
		)
		return

	db.create_collection(
		"employee_holiday_calendars",
		validator=EMPLOYEE_HOLIDAY_CALENDARS_VALIDATOR,
		validationLevel="moderate",
		validationAction="error",
	)


def ensure_users_collection():
	if "users" in db.list_collection_names():
		db.command(
			"collMod",
			"users",
			validator=USERS_VALIDATOR,
			validationLevel="moderate",
			validationAction="error",
		)
		db["users"].create_index("work_email", unique=True, name="users_work_email_unique")
		db["users"].create_index("employee_id", unique=True, sparse=True, name="users_employee_id_unique")
		db["users"].create_index("mobile", unique=True, name="users_mobile_unique")
		return

	db.create_collection(
		"users",
		validator=USERS_VALIDATOR,
		validationLevel="moderate",
		validationAction="error",
	)
	db["users"].create_index("work_email", unique=True, name="users_work_email_unique")
	db["users"].create_index("employee_id", unique=True, sparse=True, name="users_employee_id_unique")
	db["users"].create_index("mobile", unique=True, name="users_mobile_unique")


def ensure_roles_collection():
	if "roles" in db.list_collection_names():
		db.command("collMod", "roles", validator=ROLES_VALIDATOR, validationLevel="moderate", validationAction="error")
	else:
		db.create_collection("roles", validator=ROLES_VALIDATOR, validationLevel="moderate", validationAction="error")
	roles_collection = db["roles"]
	roles_collection.create_index("name", unique=True, name="roles_name_unique")
	now = datetime.now(timezone.utc)
	roles_collection.update_one(
		{"name": "HR Admin"},
		{
			"$setOnInsert": {
				"name": "HR Admin",
				"status": "Active",
				"created_at": now,
				"updated_at": now,
			}
		},
		upsert=True,
	)
from datetime import datetime, timezone


def ensure_permissions_collection():
	if "permissions" in db.list_collection_names():
		db.command("collMod", "permissions", validator=PERMISSIONS_VALIDATOR, validationLevel="moderate", validationAction="error")
	else:
		db.create_collection("permissions", validator=PERMISSIONS_VALIDATOR, validationLevel="moderate", validationAction="error")
	db["permissions"].create_index(
		[("role_id", 1), ("permission", 1)],
		unique=True,
		name="permissions_role_permission_unique",
	)


def ensure_audit_logs_collection():
	if "audit_logs" in db.list_collection_names():
		db.command("collMod", "audit_logs", validator=AUDIT_LOGS_VALIDATOR, validationLevel="moderate", validationAction="error")
	else:
		db.create_collection("audit_logs", validator=AUDIT_LOGS_VALIDATOR, validationLevel="moderate", validationAction="error")
	db["audit_logs"].create_index([("user_id", 1), ("timestamp", -1)], name="audit_logs_user_timestamp")

# ============================================================
# LEAVE COLLECTION INITIALIZATION
# ============================================================

def ensure_leave_types_collection():

    collection_name = "leave_types"

    if collection_name not in db.list_collection_names():

        db.create_collection(
            collection_name,
            validator=LEAVE_TYPES_VALIDATOR,
            validationLevel="moderate",
            validationAction="error",
        )

    collection = db[collection_name]

    collection.create_index(
        [("code", 1)],
        unique=True,
    )

    collection.create_index(
        [("is_active", 1)]
    )

    return collection


def ensure_leave_policies_collection():

    collection_name = "leave_policies"

    if collection_name not in db.list_collection_names():

        db.create_collection(
            collection_name,
            validator=LEAVE_POLICIES_VALIDATOR,
            validationLevel="moderate",
            validationAction="error",
        )

    collection = db[collection_name]

    collection.create_index(
        [("leave_type_id", 1)],
        unique=True,
    )

    collection.create_index(
        [("is_active", 1)]
    )

    return collection


def ensure_leave_balances_collection():

    collection_name = "leave_balances"

    if collection_name not in db.list_collection_names():

        db.create_collection(
            collection_name,
            validator=LEAVE_BALANCES_VALIDATOR,
            validationLevel="moderate",
            validationAction="error",
        )

    collection = db[collection_name]

    collection.create_index(
        [
            ("user_id", 1),
            ("leave_type_id", 1),
            ("year", 1),
        ],
        unique=True,
    )

    collection.create_index(
        [
            ("user_id", 1),
            ("year", 1),
        ]
    )

    return collection


def ensure_leave_requests_collection():

    collection_name = "leave_requests"

    if collection_name not in db.list_collection_names():

        db.create_collection(
            collection_name,
            validator=LEAVE_REQUESTS_VALIDATOR,
            validationLevel="moderate",
            validationAction="error",
        )

    collection = db[collection_name]

    collection.create_index(
        [
            ("user_id", 1),
            ("created_at", -1),
        ]
    )

    collection.create_index(
        [
            ("status", 1),
            ("created_at", -1),
        ]
    )

    collection.create_index(
        [
            ("from_date", 1),
            ("to_date", 1),
        ]
    )

    return collection