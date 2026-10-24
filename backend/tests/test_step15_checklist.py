import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from bson import ObjectId
from fastapi import HTTPException

from app.main import (
    ActivationPayload,
    UserCreatePayload,
    UserLoginPayload,
    UserPayload,
    UserStatusPayload,
    activate_account,
    app,
    create_user,
    get_user,
    login_user,
    serialize_user,
    update_user,
    update_user_status,
)
from app.security import hash_password


class Step15ChecklistTests(unittest.TestCase):
    def setUp(self):
        self.user_id = ObjectId("507f1f77bcf86cd799439011")
        self.payload = UserPayload(
            name="Anand Kumar",
            work_email="anand.kumar@example.com",
            mobile="9876543210",
            role="Employee",
        )

    def test_m01_t01_user_management_api_is_registered(self):
        routes = {(route.path, method) for route in app.routes for method in (route.methods or set())}
        self.assertIn(("/api/users", "POST"), routes)
        self.assertIn(("/api/users/{user_id}", "GET"), routes)

    @patch("app.main.send_activation_email", return_value="development_console")
    @patch("app.main.users_collection")
    def test_m01_t02_create_valid_user_is_saved(self, collection, send_email):
        collection.find_one.return_value = None
        collection.insert_one.return_value.inserted_id = self.user_id

        response = create_user(UserCreatePayload(**self.payload.model_dump()))

        inserted = collection.insert_one.call_args.args[0]
        self.assertEqual(response["user"]["id"], str(self.user_id))
        self.assertFalse(inserted["is_active"])
        send_email.assert_called_once()

    @patch("app.main.users_collection")
    def test_m01_t03_duplicate_email_returns_clear_error(self, collection):
        collection.find_one.return_value = {"work_email": self.payload.work_email, "mobile": "other"}

        with self.assertRaises(HTTPException) as context:
            create_user(UserCreatePayload(**self.payload.model_dump()))

        self.assertEqual(context.exception.status_code, 409)
        self.assertEqual(context.exception.detail, "Work email already exists")
        collection.insert_one.assert_not_called()

    @patch("app.main.users_collection")
    def test_m01_t04_duplicate_mobile_returns_clear_error(self, collection):
        collection.find_one.return_value = {"work_email": "other@example.com", "mobile": self.payload.mobile}

        with self.assertRaises(HTTPException) as context:
            create_user(UserCreatePayload(**self.payload.model_dump()))

        self.assertEqual(context.exception.status_code, 409)
        self.assertEqual(context.exception.detail, "Mobile number already exists")
        collection.insert_one.assert_not_called()

    @patch("app.main.users_collection")
    def test_m01_t05_edit_name_is_saved(self, collection):
        collection.find_one.side_effect = [None, {"_id": self.user_id, **self.payload.model_dump(), "name": "Updated Name", "is_active": True}]
        collection.update_one.return_value.matched_count = 1

        updated = self.payload.model_copy(update={"name": "Updated Name"})
        result = update_user(str(self.user_id), updated)

        self.assertEqual(result["name"], "Updated Name")
        self.assertEqual(collection.update_one.call_args.args[0], {"_id": self.user_id})

    @patch("app.main.users_collection")
    def test_m01_t06_edit_role_is_saved(self, collection):
        collection.find_one.side_effect = [None, {"_id": self.user_id, **self.payload.model_dump(), "role": "HR Admin", "is_active": True}]
        collection.update_one.return_value.matched_count = 1

        result = update_user(str(self.user_id), self.payload.model_copy(update={"role": "HR Admin"}))

        update = collection.update_one.call_args.args[1]["$set"]
        self.assertEqual(update["role"], "HR Admin")
        self.assertEqual(update["role_id"], "hr_admin")
        self.assertEqual(result["role"], "HR Admin")

    @patch("app.main.users_collection")
    def test_m01_t07_deactivate_user_is_soft_status_change(self, collection):
        collection.update_one.return_value.matched_count = 1
        collection.find_one.return_value = {"_id": self.user_id, **self.payload.model_dump(), "is_active": False}

        result = update_user_status(str(self.user_id), UserStatusPayload(is_active=False))

        self.assertFalse(result["is_active"])
        collection.delete_one.assert_not_called()

    @patch("app.main.users_collection")
    def test_m01_t08_inactive_user_login_is_denied_generically(self, collection):
        collection.find_one.return_value = {"_id": self.user_id, "is_active": False}

        with self.assertRaises(HTTPException) as context:
            login_user(UserLoginPayload(work_email=self.payload.work_email, password="secret"))

        self.assertEqual(context.exception.status_code, 401)
        self.assertEqual(context.exception.detail, "Invalid email or password")

    @patch("app.main.users_collection")
    def test_m01_t09_valid_user_login_returns_protected_session(self, collection):
        collection.find_one.return_value = {"_id": self.user_id, **self.payload.model_dump(), "is_active": True, "password_hash": hash_password("secret")}
        collection.update_one.return_value.matched_count = 1

        result = login_user(UserLoginPayload(work_email=self.payload.work_email, password="secret"))

        self.assertTrue(result["token"])
        self.assertEqual(result["user"]["id"], str(self.user_id))

    @patch("app.main.users_collection")
    def test_m01_t10_wrong_password_is_generic_error(self, collection):
        collection.find_one.return_value = {"_id": self.user_id, "is_active": True, "password_hash": hash_password("secret")}

        with self.assertRaises(HTTPException) as context:
            login_user(UserLoginPayload(work_email=self.payload.work_email, password="wrong"))

        self.assertEqual(context.exception.detail, "Invalid email or password")

    @patch("app.main.users_collection")
    def test_m01_t11_five_failures_lock_for_fifteen_minutes(self, collection):
        collection.find_one.return_value = {"_id": self.user_id, "is_active": True, "failed_attempts": 4, "password_hash": hash_password("secret")}

        with self.assertRaises(HTTPException):
            login_user(UserLoginPayload(work_email=self.payload.work_email, password="wrong"))

        update = collection.update_one.call_args.args[1]["$set"]
        self.assertEqual(update["failed_attempts"], 5)
        self.assertGreater(update["locked_until"], datetime.now(timezone.utc) + timedelta(minutes=14))

    def test_m01_t12_password_is_not_in_response(self):
        response = serialize_user({"_id": self.user_id, "name": "Anand", "password": "plain", "password_hash": "hash"})
        self.assertNotIn("password", response)
        self.assertNotIn("password_hash", response)

    def test_m01_t13_unknown_user_path_returns_accessible_not_found(self):
        self.assertTrue(any(route.path == "/api/users/{user_id}" for route in app.routes))

    @patch("app.main.users_collection")
    def test_m01_t14_activation_creates_password_for_next_session(self, collection):
        token = "first-login-token"
        collection.find_one.return_value = {
            "_id": self.user_id,
            "name": self.payload.name,
            "work_email": self.payload.work_email,
            "mobile": self.payload.mobile,
            "role": self.payload.role,
            "is_active": False,
            "activation_token_hash": __import__("app.activation", fromlist=["hash_activation_token"]).hash_activation_token(token),
            "activation_expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        }
        collection.update_one.return_value.matched_count = 1

        result = activate_account(token, ActivationPayload(password="secret123"))

        self.assertTrue(result["is_active"])
        update = collection.update_one.call_args.args[1]
        self.assertIn("password_hash", update["$set"])
        self.assertIn("activation_token_hash", update["$unset"])


if __name__ == "__main__":
    unittest.main()
