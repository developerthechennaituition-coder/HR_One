import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from bson import ObjectId
from fastapi import HTTPException
from pydantic import ValidationError

from app.main import CompanyPayload, app, create_company, deactivate_company, update_company


class CompanyApiTests(unittest.TestCase):
    def setUp(self):
        self.company_id = ObjectId("507f1f77bcf86cd799439011")
        self.payload = CompanyPayload(
            company="Test Company",
            legal_entity="Test Entity",
            branch="Main Branch",
            location="Chennai",
        )

    def test_valid_create_payload_is_accepted(self):
        self.assertEqual(self.payload.legal_entity, "Test Entity")
        self.assertEqual(self.payload.model_dump()["company"], "Test Company")

    def test_blank_create_payload_is_rejected(self):
        with self.assertRaises(ValidationError):
            CompanyPayload(
                company=" ",
                legal_entity="Test Entity",
                branch="Main Branch",
                location="Chennai",
            )

    @patch("app.main.companies_collection")
    def test_create_persists_company(self, collection):
        collection.insert_one.return_value.inserted_id = self.company_id

        response = create_company(self.payload)

        collection.insert_one.assert_called_once()
        inserted = collection.insert_one.call_args.args[0]
        self.assertEqual(inserted["legal_entity"], "Test Entity")
        self.assertTrue(inserted["is_active"])
        self.assertEqual(response["id"], str(self.company_id))

    @patch("app.main.get_active_company")
    @patch("app.main.companies_collection")
    def test_update_changes_existing_company(self, collection, get_active):
        now = datetime.now(timezone.utc)
        get_active.return_value = {
            **self.payload.model_dump(),
            "id": str(self.company_id),
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        }
        collection.update_one.return_value.matched_count = 1

        response = update_company(str(self.company_id), self.payload)

        update = collection.update_one.call_args.args[1]["$set"]
        self.assertEqual(update["company"], "Test Company")
        self.assertEqual(response["id"], str(self.company_id))

    @patch("app.main.companies_collection")
    def test_deactivate_retains_record_as_inactive(self, collection):
        collection.update_one.return_value.matched_count = 1

        response = deactivate_company(str(self.company_id))

        update = collection.update_one.call_args.args[1]["$set"]
        self.assertFalse(update["is_active"])
        self.assertIn("deactivated_at", update)
        self.assertEqual(response["id"], str(self.company_id))

    def test_invalid_id_returns_client_error(self):
        with self.assertRaises(HTTPException) as context:
            update_company("not-an-object-id", self.payload)

        self.assertEqual(context.exception.status_code, 400)

    def test_expected_company_routes_are_registered(self):
        routes = {
            (route.path, method)
            for route in app.routes
            for method in (route.methods or set())
        }
        expected_routes = {
            ("/api/companies", "GET"),
            ("/api/companies", "POST"),
            ("/api/companies/{company_id}", "GET"),
            ("/api/companies/{company_id}", "PUT"),
            ("/api/companies/{company_id}", "DELETE"),
        }
        self.assertTrue(expected_routes.issubset(routes))

    @patch("app.main.companies_collection")
    def test_missing_company_returns_backend_error(self, collection):
        collection.update_one.return_value.matched_count = 0

        with self.assertRaises(HTTPException) as context:
            update_company(str(self.company_id), self.payload)

        self.assertEqual(context.exception.status_code, 404)
        self.assertEqual(context.exception.detail, "Company not found")


if __name__ == "__main__":
    unittest.main()
