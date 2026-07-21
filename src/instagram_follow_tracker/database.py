from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from instagram_follow_tracker.models import FollowerRecord, TrackedEvent


def normalize_username(username: str) -> str:
    return username.strip().lstrip("@").lower()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TrackerDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def init_schema(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS tracked_accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tracked_account_id INTEGER NOT NULL,
                    captured_at TEXT NOT NULL,
                    follower_count INTEGER NOT NULL,
                    FOREIGN KEY (tracked_account_id) REFERENCES tracked_accounts(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS snapshot_followers (
                    snapshot_id INTEGER NOT NULL,
                    follower_username TEXT NOT NULL,
                    profile_id TEXT,
                    full_name TEXT NOT NULL DEFAULT '',
                    is_verified INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (snapshot_id, follower_username),
                    FOREIGN KEY (snapshot_id) REFERENCES snapshots(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tracked_account_id INTEGER NOT NULL,
                    snapshot_id INTEGER NOT NULL,
                    event_type TEXT NOT NULL CHECK (event_type IN ('follow', 'unfollow')),
                    follower_username TEXT NOT NULL,
                    profile_id TEXT,
                    full_name TEXT NOT NULL DEFAULT '',
                    is_verified INTEGER NOT NULL DEFAULT 0,
                    observed_at TEXT NOT NULL,
                    notified_at TEXT,
                    FOREIGN KEY (tracked_account_id) REFERENCES tracked_accounts(id) ON DELETE CASCADE,
                    FOREIGN KEY (snapshot_id) REFERENCES snapshots(id) ON DELETE CASCADE
                );
                """
            )

    def upsert_account(self, username: str) -> int:
        normalized = normalize_username(username)
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO tracked_accounts (username, created_at)
                VALUES (?, ?)
                ON CONFLICT(username) DO NOTHING
                """,
                (normalized, utc_now()),
            )
            row = connection.execute(
                "SELECT id FROM tracked_accounts WHERE username = ?",
                (normalized,),
            ).fetchone()
        if row is None:
            raise RuntimeError(f"Unable to create or load tracked account: {username}")
        return int(row["id"])

    def get_account_id(self, username: str) -> int | None:
        normalized = normalize_username(username)
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id FROM tracked_accounts WHERE username = ?",
                (normalized,),
            ).fetchone()
        return None if row is None else int(row["id"])

    def list_accounts(self) -> list[str]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT username FROM tracked_accounts ORDER BY username ASC"
            ).fetchall()
        return [str(row["username"]) for row in rows]

    def get_latest_snapshot_followers(self, account_id: int) -> dict[str, FollowerRecord] | None:
        with self.connect() as connection:
            snapshot = connection.execute(
                """
                SELECT id
                FROM snapshots
                WHERE tracked_account_id = ?
                ORDER BY captured_at DESC, id DESC
                LIMIT 1
                """,
                (account_id,),
            ).fetchone()
            if snapshot is None:
                return None
            rows = connection.execute(
                """
                SELECT follower_username, profile_id, full_name, is_verified
                FROM snapshot_followers
                WHERE snapshot_id = ?
                """,
                (int(snapshot["id"]),),
            ).fetchall()
        return {
            str(row["follower_username"]): FollowerRecord(
                username=str(row["follower_username"]),
                profile_id=None if row["profile_id"] is None else str(row["profile_id"]),
                full_name=str(row["full_name"]),
                is_verified=bool(row["is_verified"]),
            )
            for row in rows
        }

    def store_snapshot(self, account_id: int, followers: list[FollowerRecord]) -> int:
        captured_at = utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO snapshots (tracked_account_id, captured_at, follower_count)
                VALUES (?, ?, ?)
                """,
                (account_id, captured_at, len(followers)),
            )
            snapshot_id = int(cursor.lastrowid)
            connection.executemany(
                """
                INSERT INTO snapshot_followers (
                    snapshot_id,
                    follower_username,
                    profile_id,
                    full_name,
                    is_verified
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        snapshot_id,
                        follower.username,
                        follower.profile_id,
                        follower.full_name,
                        int(follower.is_verified),
                    )
                    for follower in followers
                ],
            )
        return snapshot_id

    def record_events(
        self,
        account_id: int,
        account_username: str,
        snapshot_id: int,
        event_type: str,
        followers: list[FollowerRecord],
    ) -> list[TrackedEvent]:
        if not followers:
            return []
        observed_at = utc_now()
        events: list[TrackedEvent] = []
        with self.connect() as connection:
            for follower in followers:
                cursor = connection.execute(
                    """
                    INSERT INTO events (
                        tracked_account_id,
                        snapshot_id,
                        event_type,
                        follower_username,
                        profile_id,
                        full_name,
                        is_verified,
                        observed_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        account_id,
                        snapshot_id,
                        event_type,
                        follower.username,
                        follower.profile_id,
                        follower.full_name,
                        int(follower.is_verified),
                        observed_at,
                    ),
                )
                events.append(
                    TrackedEvent(
                        event_id=int(cursor.lastrowid),
                        account_username=account_username,
                        event_type=event_type,
                        follower=follower,
                        observed_at=datetime.fromisoformat(observed_at),
                    )
                )
        return events

    def get_pending_events(self, account_username: str | None = None) -> dict[str, list[TrackedEvent]]:
        params: list[str] = []
        filter_sql = ""
        if account_username:
            filter_sql = "AND ta.username = ?"
            params.append(normalize_username(account_username))
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    e.id,
                    ta.username AS account_username,
                    e.event_type,
                    e.follower_username,
                    e.profile_id,
                    e.full_name,
                    e.is_verified,
                    e.observed_at
                FROM events e
                INNER JOIN tracked_accounts ta ON ta.id = e.tracked_account_id
                WHERE e.notified_at IS NULL
                {filter_sql}
                ORDER BY e.observed_at ASC, e.id ASC
                """,
                params,
            ).fetchall()
        grouped: dict[str, list[TrackedEvent]] = defaultdict(list)
        for row in rows:
            grouped[str(row["account_username"])].append(
                TrackedEvent(
                    event_id=int(row["id"]),
                    account_username=str(row["account_username"]),
                    event_type=str(row["event_type"]),
                    follower=FollowerRecord(
                        username=str(row["follower_username"]),
                        profile_id=None if row["profile_id"] is None else str(row["profile_id"]),
                        full_name=str(row["full_name"]),
                        is_verified=bool(row["is_verified"]),
                    ),
                    observed_at=datetime.fromisoformat(str(row["observed_at"])),
                )
            )
        return dict(grouped)

    def mark_events_notified(self, event_ids: list[int]) -> None:
        if not event_ids:
            return
        placeholders = ",".join("?" for _ in event_ids)
        with self.connect() as connection:
            connection.execute(
                f"UPDATE events SET notified_at = ? WHERE id IN ({placeholders})",
                [utc_now(), *event_ids],
            )
