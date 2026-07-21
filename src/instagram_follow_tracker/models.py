from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class FollowerRecord:
    username: str
    profile_id: str | None = None
    full_name: str = ""
    is_verified: bool = False


@dataclass(frozen=True)
class TrackedEvent:
    event_id: int
    account_username: str
    event_type: str
    follower: FollowerRecord
    observed_at: datetime

