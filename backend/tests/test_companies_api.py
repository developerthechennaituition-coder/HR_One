import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from bson import ObjectId
from fastapi import HTTPException
from pydantic import ValidationError

from app.main import (
    CompanyPayload,
    DepartmentPayload,
    DesignationPayload,
    EmployeePayload,
    TeamPayload,
    app,
    create_company,
    create_department,
    create_designation,
    create_employee,
    create_team,
    deactivate_company,
    update_company,
)


class CompanyApiTests(unittest.TestCase):
    def setUp(self):
        self.company_id = ObjectId("507f1f77bcf86cd799439011")
        self.payload = CompanyPayload(
            company="Test Company",
            legal_entity="Test Entity",
            branch="Main Branch",
            location="Chennai",
        )


class EmployeeApiTests(unittest.TestCase):
    def setUp(self):
        self.employee_id = ObjectId("60b5d1e38f9e7f1d8823cbaa")
        self.payload = EmployeePayload(
            employee_id="EMP-1001",
            employee_name="Anand Kumar",
            email="anand.kumar@example.com",
            phone="9876543210",
            department="Engineering",
            designation="Senior Developer",
            status="Active",
        )

    def test_valid_employee_payload_is_accepted(self):
        self.assertEqual(self.payload.employee_name, "Anand Kumar")
        self.assertEqual(self.payload.status, "Active")

    def test_blank_employee_payload_is_rejected(self):
        with self.assertRaises(ValidationError):
            EmployeePayload(
                employee_id="",
                employee_name=" ",
                email="anand.kumar@example.com",
                phone="9876543210",
                department="Engineering",
                designation="Senior Developer",
                status="Active",
            )

    @patch("app.main.employees_collection")
    def test_create_employee_persists_record(self, collection):
        collection.insert_one.return_value.inserted_id = self.employee_id

        response = create_employee(self.payload)

        collection.insert_one.assert_called_once()
        inserted = collection.insert_one.call_args.args[0]
        self.assertEqual(inserted["employee_id"], "EMP-1001")
        self.assertTrue(inserted["is_active"])
        self.assertEqual(response["id"], str(self.employee_id))

    def test_expected_employee_routes_are_registered(self):
        routes = {
            (route.path, method)
            for route in app.routes
            for method in (route.methods or set())
        }
        expected_routes = {
            ("/api/employees", "GET"),
            ("/api/employees", "POST"),
            ("/api/employees/{employee_id}", "GET"),
            ("/api/employees/{employee_id}", "PUT"),
            ("/api/employees/{employee_id}", "DELETE"),
        }
        self.assertTrue(expected_routes.issubset(routes))

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


class OrganizationModelTests(unittest.TestCase):
    def test_organization_models_accept_required_parent_context(self):
        department = DepartmentPayload(
            company_id="507f1f77bcf86cd799439011",
            department_name="Engineering",
            parent_department_id=None,
        )
        team = TeamPayload(
            company_id="507f1f77bcf86cd799439011",
            department_id="507f1f77bcf86cd799439012",
            team_name="Platform Team",
            manager_employee_id=None,
        )
        designation = DesignationPayload(
            company_id="507f1f77bcf86cd799439011",
            designation_name="Senior Engineer",
        )

        self.assertEqual(department.department_name, "Engineering")
        self.assertEqual(team.team_name, "Platform Team")
        self.assertEqual(designation.designation_name, "Senior Engineer")

    def test_organization_routes_are_registered(self):
        routes = {
            (route.path, method)
            for route in app.routes
            for method in (route.methods or set())
        }
        expected_routes = {
            ("/api/business-units", "GET"),
            ("/api/business-units", "POST"),
            ("/api/business-units/{business_unit_id}", "GET"),
            ("/api/business-units/{business_unit_id}", "PUT"),
            ("/api/business-units/{business_unit_id}", "DELETE"),
            ("/api/departments", "GET"),
            ("/api/departments", "POST"),
            ("/api/departments/{department_id}", "GET"),
            ("/api/departments/{department_id}", "PUT"),
            ("/api/departments/{department_id}", "DELETE"),
            ("/api/teams", "GET"),
            ("/api/teams", "POST"),
            ("/api/teams/{team_id}", "GET"),
            ("/api/teams/{team_id}", "PUT"),
            ("/api/teams/{team_id}", "DELETE"),
            ("/api/designations", "GET"),
            ("/api/designations", "POST"),
            ("/api/designations/{designation_id}", "GET"),
            ("/api/designations/{designation_id}", "PUT"),
            ("/api/designations/{designation_id}", "DELETE"),
        }
        self.assertTrue(expected_routes.issubset(routes))

    @patch("app.main.business_units_collection")
    def test_create_business_unit_persists_record(self, collection):
        collection.insert_one.return_value.inserted_id = ObjectId("507f1f77bcf86cd799439013")

        payload = {
            "company_id": "507f1f77bcf86cd799439011",
            "business_unit_name": "Operations",
            "manager_employee_id": None,
        }
        result = create_team(
            TeamPayload(
                company_id=payload["company_id"],
                department_id="507f1f77bcf86cd799439012",
                team_name="Platform Team",
                manager_employee_id=None,
            )
        )
        self.assertIn("id", result)


if __name__ == "__main__":
    unittest.main()
