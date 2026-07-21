from __future__ import annotations

import time
from dataclasses import dataclass, field

from instagram_follow_tracker.database import TrackerDatabase, normalize_username
from instagram_follow_tracker.instagram_client import InstagramClient
from instagram_follow_tracker.models import TrackedEvent
from instagram_follow_tracker.notifiers import NotificationBundle


@dataclass
class SyncResult:
    account_username: str
    baseline_created: bool
    follow_events: list[TrackedEvent] = field(default_factory=list)
    unfollow_events: list[TrackedEvent] = field(default_factory=list)
    notification_failures: list[str] = field(default_factory=list)

    @property
    def all_events(self) -> list[TrackedEvent]:
        return [*self.follow_events, *self.unfollow_events]


class TrackerService:
    def __init__(
        self,
        database: TrackerDatabase,
        instagram_client: InstagramClient,
        notifier: NotificationBundle | None = None,
    ) -> None:
        self.database = database
        self.instagram_client = instagram_client
        self.notifier = notifier or NotificationBundle()

    def add_account(self, username: str) -> str:
        normalized = normalize_username(username)
        self.database.upsert_account(normalized)
        return normalized

    def list_accounts(self) -> list[str]:
        return self.database.list_accounts()

    def sync_account(self, username: str, notify: bool = True) -> SyncResult:
        normalized = normalize_username(username)
        account_id = self.database.upsert_account(normalized)
        previous_followers = self.database.get_latest_snapshot_followers(account_id)
        current_followers = {
            follower.username: follower
            for follower in self.instagram_client.get_followers(normalized)
        }
        snapshot_id = self.database.store_snapshot(account_id, list(current_followers.values()))
        if previous_followers is None:
            return SyncResult(account_username=normalized, baseline_created=True)
        follow_records = [
            current_followers[username]
            for username in sorted(set(current_followers) - set(previous_followers))
        ]
        unfollow_records = [
            previous_followers[username]
            for username in sorted(set(previous_followers) - set(current_followers))
        ]
        follow_events = self.database.record_events(
            account_id,
            normalized,
            snapshot_id,
            "follow",
            follow_records,
        )
        unfollow_events = self.database.record_events(
            account_id,
            normalized,
            snapshot_id,
            "unfollow",
            unfollow_records,
        )
        result = SyncResult(
            account_username=normalized,
            baseline_created=False,
            follow_events=follow_events,
            unfollow_events=unfollow_events,
        )
        if notify and result.all_events:
            notification_result = self.notifier.notify({normalized: result.all_events})
            result.notification_failures = notification_result.failures
            if notification_result.succeeded:
                self.database.mark_events_notified(
                    [event.event_id for event in result.all_events]
                )
        return result

    def sync_all(self, notify: bool = True) -> list[SyncResult]:
        return [
            self.sync_account(account, notify=notify)
            for account in self.database.list_accounts()
        ]

    def notify_pending(self, account_username: str | None = None) -> tuple[int, list[str]]:
        pending = self.database.get_pending_events(account_username)
        if not pending:
            return 0, []
        notification_result = self.notifier.notify(pending)
        if notification_result.succeeded:
            self.database.mark_events_notified(
                [
                    event.event_id
                    for events in pending.values()
                    for event in events
                ]
            )
        return sum(len(events) for events in pending.values()), notification_result.failures

    def watch(self, interval_seconds: int) -> None:
        while True:
            self.sync_all(notify=True)
            time.sleep(interval_seconds)
