from __future__ import annotations

import os
from pathlib import Path

import instaloader

from instagram_follow_tracker.database import normalize_username
from instagram_follow_tracker.models import FollowerRecord


class InstagramClient:
    def __init__(
        self,
        login_username: str,
        password: str | None = None,
        session_file: Path | None = None,
    ) -> None:
        self.login_username = normalize_username(login_username)
        self.password = password
        self.session_file = session_file or Path("data") / f"{self.login_username}.session"

    @classmethod
    def from_env(cls) -> "InstagramClient":
        login_username = os.getenv("IG_TRACKER_LOGIN_USERNAME")
        if not login_username:
            raise RuntimeError("Set IG_TRACKER_LOGIN_USERNAME before syncing followers.")
        return cls(
            login_username=login_username,
            password=os.getenv("IG_TRACKER_LOGIN_PASSWORD"),
            session_file=Path(
                os.getenv(
                    "IG_TRACKER_SESSION_FILE",
                    str(Path("data") / f"{normalize_username(login_username)}.session"),
                )
            ),
        )

    def get_followers(self, target_username: str) -> list[FollowerRecord]:
        loader = instaloader.Instaloader(
            download_pictures=False,
            download_video_thumbnails=False,
            download_videos=False,
            download_comments=False,
            save_metadata=False,
            compress_json=False,
        )
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            if self.session_file.exists():
                loader.load_session_from_file(self.login_username, filename=str(self.session_file))
            elif self.password:
                loader.login(self.login_username, self.password)
                loader.save_session_to_file(filename=str(self.session_file))
            else:
                raise RuntimeError(
                    "No Instagram session file found and IG_TRACKER_LOGIN_PASSWORD is not set."
                )
            profile = instaloader.Profile.from_username(
                loader.context, normalize_username(target_username)
            )
            followers = [
                FollowerRecord(
                    username=normalize_username(follower.username),
                    profile_id=str(follower.userid),
                    full_name=follower.full_name or "",
                    is_verified=bool(follower.is_verified),
                )
                for follower in profile.get_followers()
            ]
        except instaloader.exceptions.InstaloaderException as error:
            raise RuntimeError(f"Instagram sync failed: {error}") from error
        return sorted(followers, key=lambda follower: follower.username)
