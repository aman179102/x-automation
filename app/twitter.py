from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.models import TweetResult
from app.oauth import OAuth1Auth

logger = logging.getLogger(__name__)

TWITTER_API_BASE = "https://api.twitter.com"
TWITTER_UPLOAD_BASE = "https://upload.twitter.com"


class TwitterError(RuntimeError):
    pass


class TwitterClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.use_free_driver = bool(settings.auth_token and settings.ct0)
        self._staged_media_path: str | None = None

        if self.use_free_driver:
            logger.info("Using Free Web Session driver (Zero API Fees).")
        else:
            if not (settings.api_key and settings.api_secret and settings.access_token and settings.access_token_secret):
                raise TwitterError("Twitter credentials missing. Please set AUTH_TOKEN & CT0 (Free mode) or API keys in .env.")
            self.auth = OAuth1Auth(
                consumer_key=settings.api_key,
                consumer_secret=settings.api_secret,
                access_token=settings.access_token,
                access_token_secret=settings.access_token_secret,
            )
            logger.info("Using Official Twitter API v2 driver.")

    def _init_twikit(self):
        from twikit import Client as TwikitClient
        client = TwikitClient("en-US", impersonate="chrome124")
        client.set_cookies({
            "auth_token": self.settings.auth_token,
            "ct0": self.settings.ct0,
        })
        return client

    def verify_credentials(self) -> dict[str, Any]:
        """Verify account credentials and return user profile information."""
        if self.use_free_driver:
            async def _get_user():
                client = self._init_twikit()
                user = await client.user()
                return {"username": user.screen_name, "name": user.name, "id": str(user.id)}

            try:
                data = asyncio.run(_get_user())
                logger.info("Authenticated as @%s (%s)", data.get("username"), data.get("name"))
                return data
            except Exception as exc:
                raise TwitterError(f"Free session verification failed: {exc}")

        # Official API
        url = f"{TWITTER_API_BASE}/2/users/me"
        with httpx.Client(auth=self.auth, timeout=20.0) as client:
            resp = client.get(url, params={"user.fields": "name,username,description,public_metrics"})
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                logger.info("Authenticated as @%s (%s)", data.get("username"), data.get("name"))
                return data
            raise TwitterError(f"Authentication failed (HTTP {resp.status_code}): {resp.text}")

    def upload_media(self, image_path: Path | str, alt_text: str | None = None) -> str:
        """Stage media for posting or upload via official API."""
        if self.use_free_driver:
            # Stage the file path to be uploaded inside the same event loop in post_tweet
            self._staged_media_path = str(image_path)
            return "staged_media"

        # Official API upload
        upload_url = f"{TWITTER_UPLOAD_BASE}/1.1/media/upload.json"
        img_bytes = Path(image_path).read_bytes()
        with httpx.Client(auth=self.auth, timeout=30.0) as client:
            files = {"media": ("card.png", img_bytes, "image/png")}
            resp = client.post(upload_url, files=files)
            if resp.status_code not in (200, 201, 202):
                raise TwitterError(f"Media upload failed (HTTP {resp.status_code}): {resp.text}")

            media_data = resp.json()
            media_id = str(media_data.get("media_id_string") or media_data.get("media_id"))

            if alt_text:
                try:
                    meta_url = f"{TWITTER_UPLOAD_BASE}/1.1/media/metadata/create.json"
                    client.post(meta_url, json={"media_id": media_id, "alt_text": {"text": alt_text[:1000]}})
                except Exception:
                    pass

            return media_id

    def post_tweet(
        self,
        text: str,
        media_ids: list[str] | None = None,
        reply_to_id: str | None = None,
    ) -> TweetResult:
        """Create a tweet (handling single-loop upload and create in Free mode)."""
        if self.use_free_driver:
            staged_path = self._staged_media_path

            async def _run_post():
                client = self._init_twikit()
                final_media_ids = None
                if staged_path and Path(staged_path).exists():
                    mid = await client.upload_media(staged_path, media_category="tweet_image")
                    final_media_ids = [str(mid)]

                tweet = await client.create_tweet(
                    text=text,
                    media_ids=final_media_ids,
                    reply_to=reply_to_id,
                )
                return str(tweet.id), final_media_ids or []

            try:
                tweet_id, used_media = asyncio.run(_run_post())
                logger.info("Successfully posted tweet via Free Web Driver! Tweet ID: %s", tweet_id)
                return TweetResult(tweet_id=tweet_id, text=text, media_ids=used_media)
            except Exception as exc:
                raise TwitterError(f"Free tweet posting failed: {exc}")

        # Official API
        url = f"{TWITTER_API_BASE}/2/tweets"
        payload: dict[str, Any] = {"text": text}
        if media_ids:
            payload["media"] = {"media_ids": [str(m) for m in media_ids]}
        if reply_to_id:
            payload["reply"] = {"in_reply_to_tweet_id": str(reply_to_id)}

        with httpx.Client(auth=self.auth, timeout=25.0) as client:
            resp = client.post(url, json=payload)
            if resp.status_code in (200, 201):
                res_data = resp.json().get("data", {})
                tweet_id = str(res_data.get("id"))
                return TweetResult(tweet_id=tweet_id, text=text, media_ids=media_ids or [])
            raise TwitterError(f"Failed to post tweet (HTTP {resp.status_code}): {resp.text}")

    def delete_tweet(self, tweet_id: str) -> bool:
        if self.use_free_driver:
            async def _run_del():
                client = self._init_twikit()
                await client.delete_tweet(tweet_id)

            try:
                asyncio.run(_run_del())
                return True
            except Exception:
                return False

        url = f"{TWITTER_API_BASE}/2/tweets/{tweet_id}"
        with httpx.Client(auth=self.auth, timeout=15.0) as client:
            resp = client.delete(url)
            return resp.status_code in (200, 204)
