from __future__ import annotations

import logging
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
        if not (settings.api_key and settings.api_secret and settings.access_token and settings.access_token_secret):
            raise TwitterError("Twitter/X credentials missing. Please set X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, and X_ACCESS_TOKEN_SECRET in .env.")

        self.auth = OAuth1Auth(
            consumer_key=settings.api_key,
            consumer_secret=settings.api_secret,
            access_token=settings.access_token,
            access_token_secret=settings.access_token_secret,
        )
        self.settings = settings

    def verify_credentials(self) -> dict[str, Any]:
        """Verify account credentials and return user profile information."""
        url = f"{TWITTER_API_BASE}/2/users/me"
        with httpx.Client(auth=self.auth, timeout=20.0) as client:
            resp = client.get(url, params={"user.fields": "name,username,description,public_metrics"})
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                logger.info("Authenticated as @%s (%s)", data.get("username"), data.get("name"))
                return data

            # Fallback to v1.1 verify_credentials if v2 has permissions differences
            v1_url = f"{TWITTER_API_BASE}/1.1/account/verify_credentials.json"
            v1_resp = client.get(v1_url)
            if v1_resp.status_code == 200:
                data = v1_resp.json()
                logger.info("Authenticated as @%s (%s)", data.get("screen_name"), data.get("name"))
                return {"username": data.get("screen_name"), "name": data.get("name"), "id": data.get("id_str")}

            raise TwitterError(f"Authentication failed (HTTP {resp.status_code}): {resp.text}")

    def upload_media(self, image_bytes: bytes, alt_text: str | None = None) -> str:
        """Upload image to Twitter v1.1 media endpoint and optionally attach alt-text."""
        upload_url = f"{TWITTER_UPLOAD_BASE}/1.1/media/upload.json"
        with httpx.Client(auth=self.auth, timeout=30.0) as client:
            files = {"media": ("card.png", image_bytes, "image/png")}
            resp = client.post(upload_url, files=files)
            if resp.status_code not in (200, 201, 202):
                raise TwitterError(f"Media upload failed (HTTP {resp.status_code}): {resp.text}")

            media_data = resp.json()
            media_id = str(media_data.get("media_id_string") or media_data.get("media_id"))
            if not media_id:
                raise TwitterError(f"No media_id returned from Twitter: {resp.text}")

            # Attach alt text if provided
            if alt_text:
                try:
                    meta_url = f"{TWITTER_UPLOAD_BASE}/1.1/media/metadata/create.json"
                    meta_payload = {
                        "media_id": media_id,
                        "alt_text": {"text": alt_text[:1000]},
                    }
                    client.post(meta_url, json=meta_payload)
                except Exception as exc:
                    logger.warning("Failed to attach alt text: %s", exc)

            return media_id

    def post_tweet(
        self,
        text: str,
        media_ids: list[str] | None = None,
        reply_to_id: str | None = None,
    ) -> TweetResult:
        """Create a tweet via Twitter API v2."""
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
                tweet_text = res_data.get("text", text)
                logger.info("Successfully posted tweet ID: %s", tweet_id)
                return TweetResult(tweet_id=tweet_id, text=tweet_text, media_ids=media_ids or [])

            if resp.status_code == 403:
                raise TwitterError(f"Twitter 403 Forbidden (Check write permissions in Developer Portal): {resp.text}")
            if resp.status_code == 429:
                raise TwitterError(f"Twitter Rate Limit Reached (429): {resp.text}")

            raise TwitterError(f"Failed to post tweet (HTTP {resp.status_code}): {resp.text}")

    def delete_tweet(self, tweet_id: str) -> bool:
        """Delete a tweet by ID."""
        url = f"{TWITTER_API_BASE}/2/tweets/{tweet_id}"
        with httpx.Client(auth=self.auth, timeout=15.0) as client:
            resp = client.delete(url)
            return resp.status_code in (200, 204)
