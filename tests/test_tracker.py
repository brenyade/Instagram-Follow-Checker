from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from instagram_follow_tracker.database import TrackerDatabase
from instagram_follow_tracker.models import FollowerRecord, TrackedEvent
from instagram_follow_tracker.tracker import TrackerService


class FakeInstagramClient:
    def __init__(self, responses: list[list[FollowerRecord]]) -> None:
        self.responses = responses
        self.calls = 0

    def get_followers(self, target_username: str) -> list[FollowerRecord]:
        response = self.responses[self.calls]
        self.calls += 1
        return response


class FakeNotifier:
    def __init__(self) -> None:
        self.messages: list[dict[str, list[TrackedEvent]]] = []

    def notify(self, changes_by_account: dict[str, list[TrackedEvent]]):
        self.messages.append(changes_by_account)
        return type("Result", (), {"failures": [], "succeeded": True})()


class TrackerServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "tracker.db"
        self.database = TrackerDatabase(db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_first_sync_creates_baseline_without_events(self) -> None:
        service = TrackerService(
            database=self.database,
            instagram_client=FakeInstagramClient(
                [[FollowerRecord(username="alpha"), FollowerRecord(username="beta")]]
            ),
            notifier=FakeNotifier(),
        )

        result = service.sync_account("target")

        self.assertTrue(result.baseline_created)
        self.assertEqual([], result.all_events)

    def test_follow_and_unfollow_changes_are_detected(self) -> None:
        notifier = FakeNotifier()
        service = TrackerService(
            database=self.database,
            instagram_client=FakeInstagramClient(
                [
                    [
                        FollowerRecord(username="alpha"),
                        FollowerRecord(username="beta"),
                    ],
                    [
                        FollowerRecord(username="beta"),
                        FollowerRecord(username="gamma"),
                    ],
                ]
            ),
            notifier=notifier,
        )

        service.sync_account("target")
        result = service.sync_account("target")

        self.assertFalse(result.baseline_created)
        self.assertEqual(["gamma"], [event.follower.username for event in result.follow_events])
        self.assertEqual(["alpha"], [event.follower.username for event in result.unfollow_events])
        self.assertEqual(1, len(notifier.messages))

    def test_follows_are_detected_after_empty_baseline(self) -> None:
        service = TrackerService(
            database=self.database,
            instagram_client=FakeInstagramClient(
                [
                    [],
                    [FollowerRecord(username="newfriend")],
                ]
            ),
            notifier=FakeNotifier(),
        )

        first = service.sync_account("target")
        second = service.sync_account("target")

        self.assertTrue(first.baseline_created)
        self.assertFalse(second.baseline_created)
        self.assertEqual(["newfriend"], [event.follower.username for event in second.follow_events])

    def test_notify_pending_marks_events_delivered(self) -> None:
        failing_notifier = type(
            "FailingNotifier",
            (),
            {"notify": lambda self, changes: type("Result", (), {"failures": ["boom"], "succeeded": False})()},
        )()
        service = TrackerService(
            database=self.database,
            instagram_client=FakeInstagramClient(
                [
                    [FollowerRecord(username="alpha")],
                    [
                        FollowerRecord(username="alpha"),
                        FollowerRecord(username="beta"),
                    ],
                ]
            ),
            notifier=failing_notifier,
        )

        service.sync_account("target")
        result = service.sync_account("target")
        self.assertEqual(1, len(result.follow_events))

        retry_notifier = FakeNotifier()
        retry_service = TrackerService(
            database=self.database,
            instagram_client=FakeInstagramClient([[FollowerRecord(username="alpha")]]),
            notifier=retry_notifier,
        )
        count, failures = retry_service.notify_pending("target")

        self.assertEqual(1, count)
        self.assertEqual([], failures)
        self.assertEqual(0, len(self.database.get_pending_events("target")))


if __name__ == "__main__":
    unittest.main()
