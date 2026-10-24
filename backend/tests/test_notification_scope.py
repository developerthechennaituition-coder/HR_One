import unittest
from unittest.mock import patch

from bson import ObjectId

from app import notifications


class Cursor:
    def __init__(self, records):
        self.records = records

    def sort(self, *_args):
        return self

    def __iter__(self):
        return iter(self.records)


class NotificationCollection:
    def __init__(self, records):
        self.records = records

    def find(self, *_args):
        return Cursor(self.records)


class NotificationScopeTests(unittest.TestCase):
    def test_leave_alerts_are_not_returned_by_common_notifications_endpoint(self):
        leave_collection = NotificationCollection([{
            "leave_notification_id": "L001",
            "recipient_user_id": "employee-id",
        }])
        with (
            patch.object(
                notifications,
                "get_authenticated_user",
                return_value={"_id": ObjectId(), "role": "employee"},
            ),
            patch.object(notifications, "notification_recipients_collection", NotificationCollection([])),
            patch.object(notifications, "notifications_collection", NotificationCollection([])),
            patch.object(notifications, "leave_notifications_collection", leave_collection),
            patch.object(notifications, "_unread_common_count_for_user", return_value=0),
        ):
            result = notifications.list_my_notifications()

        self.assertEqual(result["notifications"], [])
        self.assertEqual(result["unread_count"], 0)

    def test_snapshot_keeps_leave_ids_separate_from_common_notification_ids(self):
        common_id = ObjectId()
        with (
            patch.object(
                notifications,
                "get_authenticated_user",
                return_value={"_id": ObjectId(), "role": "employee"},
            ),
            patch.object(
                notifications,
                "notification_recipients_collection",
                NotificationCollection([{"notification_id": common_id}]),
            ),
            patch.object(
                notifications,
                "notifications_collection",
                NotificationCollection([{"notification_id": "N009"}]),
            ),
            patch.object(
                notifications,
                "leave_notifications_collection",
                NotificationCollection([{"leave_notification_id": "L009"}]),
            ),
            patch.object(notifications, "_unread_common_count_for_user", return_value=2),
            patch.object(notifications, "_expire_notifications"),
        ):
            result = notifications.get_notification_snapshot()

        self.assertEqual(result["notification_ids"], ["N009"])
        self.assertEqual(result["leave_notification_ids"], ["L009"])
        self.assertEqual(result["unread_count"], 2)


if __name__ == "__main__":
    unittest.main()
