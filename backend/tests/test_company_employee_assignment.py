import unittest
from unittest.mock import patch

from bson import ObjectId
from fastapi import HTTPException

from app.core_routes import validate_company_employee_ids


class CompanyEmployeeAssignmentValidationTests(unittest.TestCase):
    def setUp(self):
        self.employee_id = ObjectId("6ab7763c9fb916db6381d2fa")

    @patch("app.core_routes.users_collection")
    @patch("app.core_routes.employees_collection")
    def test_legacy_user_without_is_active_flag_is_assignable(self, employees, users):
        employees.find_one.return_value = None
        users.find_one.return_value = {
            "_id": self.employee_id,
            "employee_id": "EMP-1001",
            "name": "Rohith Kumar",
            # Legacy documents may not have is_active.
        }

        result = validate_company_employee_ids([str(self.employee_id)])

        self.assertEqual(result, [str(self.employee_id)])
        users.find_one.assert_called_once_with({"_id": self.employee_id})

    @patch("app.core_routes.users_collection")
    @patch("app.core_routes.employees_collection")
    def test_legacy_employee_without_is_active_flag_is_assignable(self, employees, users):
        employees.find_one.return_value = {
            "_id": self.employee_id,
            "employee_id": "EMP-1001",
            "employee_name": "Rohith Kumar",
            "status": "Active",
        }
        users.find_one.return_value = None

        self.assertEqual(
            validate_company_employee_ids([str(self.employee_id)]),
            [str(self.employee_id)],
        )

    @patch("app.core_routes.users_collection")
    @patch("app.core_routes.employees_collection")
    def test_explicitly_inactive_employee_is_rejected(self, employees, users):
        employees.find_one.return_value = None
        users.find_one.return_value = {"_id": self.employee_id, "is_active": False}

        with self.assertRaises(HTTPException) as context:
            validate_company_employee_ids([str(self.employee_id)])

        self.assertEqual(context.exception.status_code, 422)
        self.assertIn("inactive", context.exception.detail.lower())

    @patch("app.core_routes.users_collection")
    @patch("app.core_routes.employees_collection")
    def test_employee_code_is_not_accepted_as_database_id(self, employees, users):
        with self.assertRaises(HTTPException) as context:
            validate_company_employee_ids(["EMP-1001"])

        self.assertEqual(context.exception.status_code, 422)
        employees.find_one.assert_not_called()
        users.find_one.assert_not_called()

    @patch("app.core_routes.users_collection")
    @patch("app.core_routes.employees_collection")
    def test_duplicate_ids_are_normalized(self, employees, users):
        employees.find_one.return_value = None
        users.find_one.return_value = {"_id": self.employee_id, "is_active": True}

        result = validate_company_employee_ids([str(self.employee_id), str(self.employee_id)])

        self.assertEqual(result, [str(self.employee_id)])
        self.assertEqual(users.find_one.call_count, 1)


if __name__ == "__main__":
    unittest.main()
