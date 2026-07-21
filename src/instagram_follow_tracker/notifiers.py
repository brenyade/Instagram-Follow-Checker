from __future__ import annotations

import json
import os
import smtplib
import ssl
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage

from instagram_follow_tracker.models import TrackedEvent


def _format_follower(event: TrackedEvent) -> str:
    full_name = f" ({event.follower.full_name})" if event.follower.full_name else ""
    return f"@{event.follower.username}{full_name}"


def format_notification(changes_by_account: dict[str, list[TrackedEvent]]) -> str:
    lines = ["Instagram follower change summary"]
    for account_username, events in sorted(changes_by_account.items()):
        lines.append("")
        lines.append(f"Target account: @{account_username}")
        follows = [event for event in events if event.event_type == "follow"]
        unfollows = [event for event in events if event.event_type == "unfollow"]
        if follows:
            lines.append("  New followers:")
            lines.extend(f"    - {_format_follower(event)}" for event in follows)
        if unfollows:
            lines.append("  New unfollows:")
            lines.extend(f"    - {_format_follower(event)}" for event in unfollows)
    return "\n".join(lines)


@dataclass
class NotificationResult:
    failures: list[str]

    @property
    def succeeded(self) -> bool:
        return not self.failures


class NotificationBundle:
    def __init__(self) -> None:
        self.webhook_url = os.getenv("IG_TRACKER_WEBHOOK_URL")
        self.smtp_host = os.getenv("IG_TRACKER_SMTP_HOST")
        self.smtp_port = int(os.getenv("IG_TRACKER_SMTP_PORT", "587"))
        self.smtp_username = os.getenv("IG_TRACKER_SMTP_USERNAME")
        self.smtp_password = os.getenv("IG_TRACKER_SMTP_PASSWORD")
        self.smtp_from = os.getenv("IG_TRACKER_SMTP_FROM")
        self.smtp_to = os.getenv("IG_TRACKER_SMTP_TO")
        self.smtp_use_tls = os.getenv("IG_TRACKER_SMTP_USE_TLS", "true").lower() != "false"

    def notify(self, changes_by_account: dict[str, list[TrackedEvent]]) -> NotificationResult:
        if not changes_by_account:
            return NotificationResult(failures=[])
        message = format_notification(changes_by_account)
        print(message)
        failures: list[str] = []
        if self.webhook_url:
            try:
                self._send_webhook(message, changes_by_account)
            except Exception as error:  # pragma: no cover - network dependent
                failures.append(f"webhook: {error}")
        if self.smtp_host and self.smtp_from and self.smtp_to:
            try:
                self._send_email(message)
            except Exception as error:  # pragma: no cover - network dependent
                failures.append(f"email: {error}")
        return NotificationResult(failures=failures)

    def _send_webhook(
        self,
        message: str,
        changes_by_account: dict[str, list[TrackedEvent]],
    ) -> None:
        payload = {
            "text": message,
            "events": [
                {
                    "account_username": event.account_username,
                    "event_type": event.event_type,
                    "follower_username": event.follower.username,
                    "full_name": event.follower.full_name,
                    "observed_at": event.observed_at.isoformat(),
                }
                for events in changes_by_account.values()
                for event in events
            ],
        }
        request = urllib.request.Request(
            self.webhook_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=15):
            pass

    def _send_email(self, message: str) -> None:
        email = EmailMessage()
        email["Subject"] = "Instagram follower changes detected"
        email["From"] = self.smtp_from
        email["To"] = self.smtp_to
        email.set_content(message)
        context = ssl.create_default_context()
        with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=20) as smtp:
            if self.smtp_use_tls:
                smtp.starttls(context=context)
            if self.smtp_username and self.smtp_password:
                smtp.login(self.smtp_username, self.smtp_password)
            smtp.send_message(email)
