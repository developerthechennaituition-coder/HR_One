import unittest
from unittest.mock import patch

from bson import ObjectId
from fastapi import HTTPException
from pydantic import ValidationError

from app.main import (
    BusinessUnitPayload,
    DepartmentPayload,
    DesignationPayload,
    TeamPayload,
    create_business_unit,
    create_department,
    create_designation,
    create_team,
    get_department,
    list_departments,
    update_department,
)


class UpdateResult:
    def __init__(self, matched_count):
        self.matched_count = matched_count


class InsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class InMemoryCollection:
    def __init__(self, records=None):
        self.records = {record["_id"]: record for record in records or []}

    def insert_one(self, record):
        record = dict(record)
        record["_id"] = ObjectId()
        self.records[record["_id"]] = record
        return InsertResult(record["_id"])

    def find_one(self, query):
        for record in self.records.values():
            if all(record.get(key) == value for key, value in query.items()):
                return dict(record)
        return None

    def find(self, query):
        matches = [
            dict(record)
            for record in self.records.values()
            if all(record.get(key) == value for key, value in query.items())
        ]
        return InMemoryCursor(matches)

    def update_one(self, query, update):
        record = self.find_one(query)
        if record is None:
            return UpdateResult(0)
        self.records[record["_id"]].update(update.get("$set", {}))
        return UpdateResult(1)


class InMemoryCursor:
    def __init__(self, records):
        self.records = records

    def sort(self, field, direction):
        self.records.sort(key=lambda record: record.get(field), reverse=direction < 0)
        return self

    def __iter__(self):
        return iter(self.records)


class OrganizationWorkflowTests(unittest.TestCase):
    company_id = "507f1f77bcf86cd799439011"

    def setUp(self):
        self.company = {
            "_id": ObjectId(self.company_id),
            "company": "Acme HR Services",
            "is_active": True,
        }
        self.companies = InMemoryCollection([self.company])
        self.business_units = InMemoryCollection()
        self.departments = InMemoryCollection()
        self.teams = InMemoryCollection()
        self.designations = InMemoryCollection()
        self.employees = InMemoryCollection()
        self.collections_patch = patch.multiple(
            "app.main",
            companies_collection=self.companies,
            business_units_collection=self.business_units,
            departments_collection=self.departments,
            teams_collection=self.teams,
            designations_collection=self.designations,
            employees_collection=self.employees,
        )
        self.collections_patch.start()

    def tearDown(self):
        self.collections_patch.stop()

    def test_sample_records_persist_as_a_reporting_hierarchy(self):
        business_unit = create_business_unit(
            BusinessUnitPayload(
                company_id=self.company_id,
                business_unit_name="Technology",
            )
        )
        root = create_department(
            DepartmentPayload(
                company_id=self.company_id,
                department_name="Engineering",
            )
        )
        child = create_department(
            DepartmentPayload(
                company_id=self.company_id,
                department_name="Platform",
                parent_department_id=root["id"],
            )
        )
        team = create_team(
            TeamPayload(
                company_id=self.company_id,
                department_id=child["id"],
                team_name="Developer Experience",
            )
        )
        designation = create_designation(
            DesignationPayload(
                company_id=self.company_id,
                designation_name="Staff Engineer",
            )
        )

        self.assertEqual(business_unit["company_id"], self.company_id)
        self.assertEqual(child["parent_department_id"], root["id"])
        self.assertEqual(team["department_id"], child["id"])
        self.assertEqual(designation["designation_name"], "Staff Engineer")
        self.assertEqual(len(list(list_departments())), 2)

    def test_edit_is_reflected_by_detail_and_reload_reads(self):
        department = create_department(
            DepartmentPayload(
                company_id=self.company_id,
                department_name="People Operations",
            )
        )
        payload = DepartmentPayload(
            company_id=self.company_id,
            department_name="People and Culture",
        )

        updated = update_department(department["id"], payload)
        reloaded = get_department(department["id"])

        self.assertEqual(updated["department_name"], "People and Culture")
        self.assertEqual(reloaded["department_name"], "People and Culture")

    def test_invalid_parent_and_missing_required_input_do_not_persist(self):
        with self.assertRaises(HTTPException) as invalid_parent:
            create_team(
                TeamPayload(
                    company_id=self.company_id,
                    department_id=str(ObjectId()),
                    team_name="Orphan Team",
                )
            )
        self.assertEqual(invalid_parent.exception.status_code, 400)
        self.assertEqual(self.teams.records, {})

        with self.assertRaises(ValidationError):
            DepartmentPayload(company_id=self.company_id, department_name=" ")

        with self.assertRaises(HTTPException) as invalid_id:
            get_department("not-an-object-id")
        self.assertEqual(invalid_id.exception.status_code, 400)

    def test_department_cannot_report_to_itself(self):
        department = create_department(
            DepartmentPayload(
                company_id=self.company_id,
                department_name="Finance",
            )
        )

        with self.assertRaises(HTTPException) as invalid_parent:
            update_department(
                department["id"],
                DepartmentPayload(
                    company_id=self.company_id,
                    department_name="Finance",
                    parent_department_id=department["id"],
                ),
            )
        self.assertEqual(invalid_parent.exception.status_code, 400)
        self.assertEqual(get_department(department["id"])["parent_department_id"], None)


if __name__ == "__main__":
    unittest.main()