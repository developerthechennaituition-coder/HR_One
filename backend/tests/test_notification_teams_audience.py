import unittest
from unittest.mock import patch

from bson import ObjectId

from app import notifications


def _matches(doc, query):
    for key, value in query.items():
        if key == "$or":
            if not any(_matches(doc, clause) for clause in value):
                return False
        elif isinstance(value, dict) and "$in" in value:
            if doc.get(key) not in value["$in"]:
                return False
        elif doc.get(key) != value:
            return False
    return True


class FakeUsers:
    def __init__(self, docs):
        self.docs = docs

    def find(self, query, _projection=None):
        return [doc for doc in self.docs if _matches(doc, query)]


class SpecificTeamsAudienceTests(unittest.TestCase):
    def setUp(self):
        self.m1, self.m2, self.e1, self.e2, self.e3, self.gone = (ObjectId() for _ in range(6))
        self.users = FakeUsers([
            {"_id": self.m1, "employee_id": "M1", "is_active": True},
            {"_id": self.m2, "employee_id": "M2", "is_active": True, "reporting_manager_id": str(self.m1)},
            {"_id": self.e1, "employee_id": "E1", "is_active": True, "reporting_manager_id": str(self.m1)},
            {"_id": self.e2, "employee_id": "E2", "is_active": True, "reporting_manager_id": str(self.m2)},
            {"_id": self.e3, "employee_id": "E3", "is_active": True, "reporting_manager_id": str(self.m2)},
            {"_id": self.gone, "employee_id": "E4", "is_active": False, "reporting_manager_id": str(self.m1)},
        ])

    def _resolve(self, team_ids):
        audience = notifications.NotificationAudience(type="teams", team_ids=[str(value) for value in team_ids])
        with patch.object(notifications, "notifications_users_collection", return_value=self.users):
            return notifications.resolve_recipient_user_ids(audience)

    def test_managers_and_their_reports_are_notified_once(self):
        # m2 reports to m1 and is also selected itself: still exactly one copy.
        result = self._resolve([self.m1, self.m2])
        self.assertEqual(len(result), len(set(result)))
        self.assertEqual(set(result), {str(v) for v in (self.m1, self.m2, self.e1, self.e2, self.e3)})

    def test_inactive_reports_are_skipped(self):
        self.assertNotIn(str(self.gone), self._resolve([self.m1]))

    def test_empty_selection_is_rejected(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            self._resolve([])


if __name__ == "__main__":
    unittest.main()
