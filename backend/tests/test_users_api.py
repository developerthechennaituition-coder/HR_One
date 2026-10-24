import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from bson import ObjectId
from fastapi import HTTPException
from pydantic import ValidationError
from pymongo.errors import DuplicateKeyError

from app.db.mongodb import ensure_roles_collection
from app.security import hash_password, verify_password
from app.activation import hash_activation_token
from app.main import (
    AuthMessageResponse,
    UserLoginPayload,
    UserPayload,
    UserCreatePayload,
    UserActivationPayload,
    UserStatusPayload,
    create_user,
    login_user,
    update_user,
    update_user_status,
    ActivationPayload,
    activate_account,
    get_activation_details,
    app,
    get_current_user,
    logout_user,
    serialize_user,
    active_sessions,
)


class UserApiTests(unittest.TestCase):
    def setUp(self):
        self.user_id = ObjectId("507f1f77bcf86cd799439011")
        self.payload = UserPayload(
            name="Anand Kumar",
            work_email="anand.kumar@example.com",
            mobile="9876543210",
            role="Employee",
        )

    def test_blank_or_invalid_user_payload_is_rejected(self):
        with self.assertRaises(ValidationError):
            UserPayload(
                name=" ",
                work_email="not-an-email",
                mobile="9876543210",
                role="Employee",
            )

    def test_password_hash_round_trip(self):
        password_hash = hash_password("correct horse battery staple")
        self.assertNotEqual(password_hash, "correct horse battery staple")
        self.assertTrue(verify_password("correct horse battery staple", password_hash))
        self.assertFalse(verify_password("wrong password", password_hash))

    def test_password_is_not_exposed_by_user_response(self):
        from app.main import serialize_user

        serialized = serialize_user({
            "_id": self.user_id,
            "name": self.payload.name,
            "work_email": str(self.payload.work_email),
            "mobile": self.payload.mobile,
            "role": self.payload.role,
            "is_active": True,
            "password_hash": hash_password("secret password"),
        })
        self.assertNotIn("password_hash", serialized)

    @patch("app.main.users_collection")
    def test_create_user_stores_employee_id_and_hashed_password(self, collection):
        collection.find_one.return_value = None
        collection.insert_one.return_value.inserted_id = self.user_id

        user_payload = self.payload.model_dump()
        user_payload.update({
            "employee_id": "EMP-1001",
            "password": "secret123",
            "country": "India",
            "country_iso": "IN",
            "country_code": "+91",
        })
        response = create_user(UserCreatePayload(**user_payload))

        inserted = collection.insert_one.call_args.args[0]
        self.assertEqual(inserted["employee_id"], "EMP-1001")
        self.assertEqual(inserted["country"], "India")
        self.assertEqual(inserted["country_iso"], "IN")
        self.assertEqual(inserted["country_code"], "+91")
        self.assertEqual(inserted["login_count"], 0)
        self.assertTrue(inserted["is_active"])
        self.assertTrue(verify_password("secret123", inserted["password_hash"]))
        self.assertNotIn("password", inserted)
        self.assertEqual(response["delivery"], "direct")

    @patch("app.main.users_collection")
    def test_employee_id_login_returns_protected_session(self, collection):
        collection.find_one.return_value = {
            "_id": self.user_id,
            "employee_id": "EMP-1001",
            "name": self.payload.name,
            "work_email": str(self.payload.work_email),
            "mobile": self.payload.mobile,
            "role": self.payload.role,
            "is_active": True,
            "password_hash": hash_password("secret123"),
        }
        collection.update_one.return_value.matched_count = 1

        response = login_user(UserLoginPayload(employee_id="EMP-1001", password="secret123"))

        self.assertTrue(response["token"])
        self.assertEqual(response["user"]["employee_id"], "EMP-1001")
        self.assertEqual(response["user"]["login_count"], 1)
        update = collection.update_one.call_args.args[1]
        self.assertEqual(update["$inc"]["login_count"], 1)

    def test_auth_me_returns_active_sessions_for_current_user(self):
        naive_utc_login = datetime(2026, 10, 2, 11, 0)
        user = serialize_user({
            "_id": self.user_id,
            "name": "Anand Kumar",
            "work_email": str(self.payload.work_email),
            "mobile": self.payload.mobile,
            "role": self.payload.role,
            "is_active": True,
            "created_at": naive_utc_login,
            "updated_at": naive_utc_login,
            "last_login": naive_utc_login,
        })

        with (
            patch.dict(
                "app.main.active_sessions",
                {
                    "session-one": str(self.user_id),
                    "session-two": str(self.user_id),
                    "other-user-session": "507f1f77bcf86cd799439012",
                },
                clear=True,
            ),
            patch("app.main.get_user", return_value=user),
        ):
            response = get_current_user("Bearer session-one")

        self.assertEqual(response["active_login_count"], 2)
        self.assertEqual(response["last_login"].utcoffset(), timezone.utc.utcoffset(None))

    def test_logout_removes_only_the_current_session(self):
        with patch.dict(
            "app.main.active_sessions",
            {
                "session-one": str(self.user_id),
                "session-two": str(self.user_id),
            },
            clear=True,
        ):
            response = logout_user("Bearer session-one")

            self.assertEqual(response["message"], "Logged out successfully")
            self.assertNotIn("session-one", active_sessions)
            self.assertIn("session-two", active_sessions)

    def test_hr_admin_role_is_accepted(self):
        payload = UserPayload(
            name="HR Admin",
            work_email="hr.admin@example.com",
            mobile="9000000000",
            role="HR Admin",
        )
        self.assertEqual(payload.role, "HR Admin")

    @patch("app.db.mongodb.db")
    def test_default_role_seed_is_idempotent(self, database):
        database.list_collection_names.return_value = ["roles"]
        roles = database.__getitem__.return_value

        ensure_roles_collection()
        ensure_roles_collection()

        self.assertEqual(roles.update_one.call_count, 2)
        first_call = roles.update_one.call_args_list[0]
        self.assertEqual(first_call.args[0], {"name": "HR Admin"})
        self.assertTrue(first_call.kwargs["upsert"])
        self.assertEqual(first_call.args[1]["$setOnInsert"]["status"], "Active")

    @patch("app.main.users_collection")
    def test_create_user_starts_inactive_with_activation_reference(self, collection):
        collection.find_one.return_value = None
        collection.insert_one.return_value.inserted_id = self.user_id

        response = create_user(self.payload)

        inserted = collection.insert_one.call_args.args[0]
        self.assertFalse(inserted["is_active"])
        self.assertEqual(inserted["role_id"], "employee")
        self.assertTrue(inserted["activation_token_hash"])
        self.assertIn("activation_expires_at", inserted)
        self.assertNotIn("password", inserted)
        self.assertEqual(response["user"]["id"], str(self.user_id))

    @patch("app.main.users_collection")
    def test_create_user_does_not_store_a_password_before_activation(self, collection):
        collection.find_one.return_value = None
        collection.insert_one.return_value.inserted_id = self.user_id

        create_user(UserCreatePayload(**self.payload.model_dump()))

        inserted = collection.insert_one.call_args.args[0]
        self.assertNotIn("password", inserted)
        self.assertNotIn("password_hash", inserted)

    @patch("app.main.users_collection")
    def test_activation_sets_password_and_consumes_token(self, collection):
        raw_token = "activation-token"
        collection.find_one.return_value = {
            "_id": self.user_id,
            "name": self.payload.name,
            "work_email": str(self.payload.work_email),
            "mobile": self.payload.mobile,
            "role": self.payload.role,
            "is_active": False,
            "activation_token_hash": hash_activation_token(raw_token),
            "activation_expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        }
        collection.update_one.return_value.matched_count = 1

        response = activate_account(raw_token, ActivationPayload(password="secret123"))

        update = collection.update_one.call_args.args[1]
        self.assertTrue(verify_password("secret123", update["$set"]["password_hash"]))
        self.assertTrue(response["is_active"])
        self.assertIn("activation_token_hash", update["$unset"])

    @patch("app.main.users_collection")
    def test_expired_activation_token_is_rejected(self, collection):
        collection.find_one.return_value = {
            "activation_token_hash": hash_activation_token("expired"),
            "activation_expires_at": datetime.now(timezone.utc) - timedelta(minutes=1),
        }

        with self.assertRaises(HTTPException) as context:
            get_activation_details("expired")

        self.assertEqual(context.exception.status_code, 400)

    @patch("app.main.users_collection")
    def test_activation_hashes_new_password(self, collection):
        collection.update_one.return_value.matched_count = 1
        collection.find_one.return_value = {
            "_id": self.user_id,
            "name": self.payload.name,
            "work_email": str(self.payload.work_email),
            "mobile": self.payload.mobile,
            "role": self.payload.role,
            "is_active": True,
        }

        update_user_status(
            str(self.user_id),
            UserActivationPayload(is_active=True, password="secret123"),
        )

        update = collection.update_one.call_args.args[1]
        self.assertNotIn("password", update["$set"])
        self.assertTrue(verify_password("secret123", update["$set"]["password_hash"]))

    @patch("app.main.users_collection")
    def test_duplicate_email_returns_conflict(self, collection):
        collection.find_one.return_value = {
            "_id": self.user_id,
            "work_email": self.payload.work_email,
            "mobile": "different-mobile",
        }

        with self.assertRaises(HTTPException) as context:
            create_user(self.payload)

        self.assertEqual(context.exception.status_code, 409)
        self.assertEqual(context.exception.detail, "Work email already exists")
        collection.insert_one.assert_not_called()

    @patch("app.main.users_collection")
    def test_duplicate_index_error_returns_mobile_conflict(self, collection):
        collection.find_one.return_value = None
        collection.insert_one.side_effect = DuplicateKeyError("mobile unique index")

        with self.assertRaises(HTTPException) as context:
            create_user(self.payload)

        self.assertEqual(context.exception.status_code, 409)
        self.assertEqual(context.exception.detail, "Mobile number already exists")

    @patch("app.main.users_collection")
    def test_update_allows_current_users_unchanged_unique_fields(self, collection):
        collection.find_one.side_effect = [None, {
            "_id": self.user_id,
            "name": "Updated Name",
            "work_email": self.payload.work_email,
            "mobile": self.payload.mobile,
            "role": "HR Manager",
            "is_active": True,
        }]
        collection.update_one.return_value.matched_count = 1

        response = update_user(str(self.user_id), self.payload)

        collection.update_one.assert_called_once()
        self.assertEqual(response["id"], str(self.user_id))

    @patch("app.main.users_collection")
    def test_update_rejects_another_users_duplicate_email(self, collection):
        collection.find_one.return_value = {
            "_id": ObjectId("507f1f77bcf86cd799439012"),
            "work_email": self.payload.work_email,
            "mobile": "different-mobile",
        }

        with self.assertRaises(HTTPException) as context:
            update_user(str(self.user_id), self.payload)

        self.assertEqual(context.exception.status_code, 409)
        collection.update_one.assert_not_called()

    @patch("app.main.users_collection")
    def test_status_patch_deactivates_without_deleting(self, collection):
        collection.update_one.return_value.matched_count = 1
        collection.find_one.return_value = {
            "_id": self.user_id,
            "name": self.payload.name,
            "work_email": str(self.payload.work_email),
            "mobile": self.payload.mobile,
            "role": self.payload.role,
            "is_active": False,
        }

        response = update_user_status(str(self.user_id), UserStatusPayload(is_active=False))

        collection.update_one.assert_called_once()
        self.assertFalse(response["is_active"])
        self.assertEqual(collection.update_one.call_args.args[0], {"_id": self.user_id})

    @patch("app.main.users_collection")
    def test_login_rejects_inactive_user(self, collection):
        collection.find_one.return_value = {
            "_id": self.user_id,
            "work_email": str(self.payload.work_email),
            "is_active": False,
        }

        with self.assertRaises(HTTPException) as context:
            login_user(UserLoginPayload(work_email=self.payload.work_email, password="secret"))

        self.assertEqual(context.exception.status_code, 401)
        self.assertEqual(context.exception.detail, "Invalid email or password")

    @patch("app.main.users_collection")
    def test_login_returns_active_user(self, collection):
        password_hash = hash_password("secret")
        collection.find_one.return_value = {
            "_id": self.user_id,
            "name": self.payload.name,
            "work_email": str(self.payload.work_email),
            "mobile": self.payload.mobile,
            "role": self.payload.role,
            "is_active": True,
            "password_hash": password_hash,
        }
        collection.update_one.return_value.matched_count = 1

        response = login_user(UserLoginPayload(work_email=self.payload.work_email, password="secret"))

        self.assertIn("token", response)
        self.assertEqual(response["user"]["id"], str(self.user_id))
        self.assertNotIn("password_hash", response["user"])

    @patch("app.main.users_collection")
    def test_fifth_failed_login_locks_user_for_fifteen_minutes(self, collection):
        collection.find_one.return_value = {
            "_id": self.user_id,
            "work_email": str(self.payload.work_email),
            "is_active": True,
            "failed_attempts": 4,
            "password_hash": hash_password("secret"),
        }

        with self.assertRaises(HTTPException) as context:
            login_user(UserLoginPayload(work_email=self.payload.work_email, password="wrong"))

        self.assertEqual(context.exception.status_code, 401)
        self.assertEqual(context.exception.detail, "Invalid email or password")
        update = collection.update_one.call_args.args[1]["$set"]
        self.assertEqual(update["failed_attempts"], 5)
        self.assertIn("locked_until", update)

    def test_step_nine_routes_are_registered(self):
        routes = {(route.path, method) for route in app.routes for method in (route.methods or set())}
        expected = {
            ("/api/users", "GET"),
            ("/api/users/{user_id}", "GET"),
            ("/api/users/{user_id}", "PUT"),
            ("/api/users/{user_id}", "PATCH"),
            ("/api/users/{user_id}/status", "PATCH"),
            ("/api/auth/login", "POST"),
            ("/api/auth/me", "GET"),
            ("/api/auth/logout", "POST"),
        }
        self.assertTrue(expected.issubset(routes))

    @patch("app.main.users_collection")
    def test_list_users_supports_search_and_status(self, collection):
        collection.find.return_value.sort.return_value = []

        from app.main import list_users

        list_users(search="anand", status_filter="active")

        self.assertEqual(collection.find.call_args.args[0], {
            "$or": [
                {"name": {"$regex": "anand", "$options": "i"}},
                {"work_email": {"$regex": "anand", "$options": "i"}},
                {"employee_id": {"$regex": "anand", "$options": "i"}},
            ],
            "is_active": True,
        })

    def test_auth_me_requires_identity_header(self):
        with self.assertRaises(HTTPException) as context:
            get_current_user()
        self.assertEqual(context.exception.status_code, 401)

    def test_logout_returns_consistent_message(self):
        response = logout_user()
        self.assertIsInstance(AuthMessageResponse(**response), AuthMessageResponse)


if __name__ == "__main__":
    unittest.main()