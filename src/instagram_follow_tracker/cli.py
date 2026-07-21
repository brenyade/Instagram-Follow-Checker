from __future__ import annotations

import argparse
import os
from pathlib import Path

from instagram_follow_tracker.database import TrackerDatabase, normalize_username
from instagram_follow_tracker.instagram_client import InstagramClient
from instagram_follow_tracker.tracker import TrackerService


def default_db_path() -> Path:
    return Path(os.getenv("IG_TRACKER_DB_PATH", "data/instagram_tracker.db"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ig-tracker",
        description="Track Instagram follower changes and notify on new follows or unfollows.",
    )
    parser.add_argument("--db-path", default=str(default_db_path()))
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_account = subparsers.add_parser("add-account", help="Start tracking an Instagram account.")
    add_account.add_argument("username", nargs="?")

    subparsers.add_parser("list-accounts", help="List tracked Instagram accounts.")

    sync = subparsers.add_parser("sync", help="Sync follower data for one account or all accounts.")
    sync.add_argument("username", nargs="?")
    sync.add_argument("--all", action="store_true", help="Sync every tracked account.")
    sync.add_argument(
        "--no-notify",
        action="store_true",
        help="Store changes without sending notifications.",
    )

    notify_pending = subparsers.add_parser(
        "notify-pending",
        help="Retry notifications for changes that have not been marked as delivered.",
    )
    notify_pending.add_argument("username", nargs="?")

    watch = subparsers.add_parser("watch", help="Continuously sync all tracked accounts.")
    watch.add_argument(
        "--interval-seconds",
        type=int,
        default=int(os.getenv("IG_TRACKER_POLL_SECONDS", "900")),
    )

    return parser


def make_service(db_path: str) -> TrackerService:
    return TrackerService(
        database=TrackerDatabase(Path(db_path)),
        instagram_client=InstagramClient.from_env(),
    )


def make_database_only_service(db_path: str) -> TrackerService:
    return TrackerService(
        database=TrackerDatabase(Path(db_path)),
        instagram_client=InstagramClient(login_username="placeholder"),
    )


def _prompt_username() -> str:
    username = input("Instagram username to track: ").strip()
    if not username:
        raise SystemExit("Username is required.")
    return username


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in {"add-account", "list-accounts"}:
        database = TrackerDatabase(Path(args.db_path))
        if args.command == "add-account":
            username = args.username or _prompt_username()
            tracked = normalize_username(username)
            database.upsert_account(tracked)
            print(f"Now tracking @{tracked}.")
            return 0
        accounts = database.list_accounts()
        if not accounts:
            print("No accounts are being tracked yet.")
            return 0
        for account in accounts:
            print(f"@{account}")
        return 0

    if args.command == "sync":
        service = make_service(args.db_path)
        if args.all:
            results = service.sync_all(notify=not args.no_notify)
        else:
            username = args.username or _prompt_username()
            results = [service.sync_account(username, notify=not args.no_notify)]
        if not results:
            print("No tracked accounts found.")
            return 0
        for result in results:
            if result.baseline_created:
                print(f"Created follower baseline for @{result.account_username}.")
                continue
            print(
                f"@{result.account_username}: "
                f"{len(result.follow_events)} new follow(s), "
                f"{len(result.unfollow_events)} new unfollow(s)."
            )
            if result.notification_failures:
                print("Notification failures: " + "; ".join(result.notification_failures))
        return 0

    if args.command == "notify-pending":
        service = make_database_only_service(args.db_path)
        count, failures = service.notify_pending(args.username)
        print(f"Processed {count} pending notification event(s).")
        if failures:
            print("Notification failures: " + "; ".join(failures))
            return 1
        return 0

    if args.command == "watch":
        service = make_service(args.db_path)
        if args.interval_seconds <= 0:
            raise SystemExit("--interval-seconds must be greater than zero.")
        print(f"Watching tracked accounts every {args.interval_seconds} second(s).")
        service.watch(args.interval_seconds)
        return 0

    parser.error("Unknown command.")
    return 2
