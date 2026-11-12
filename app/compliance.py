from __future__ import annotations

import logging
import re
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.config import Settings
from app.models import Story

logger = logging.getLogger(__name__)

BLOCKED_TERMS = {
    "election", "trump", "biden", "harris", "modi", "politics", "congress",
    "parliament", "war", "gaza", "israel", "ukraine", "russia", "military",
    "murder", "shooting", "porn", "nsfw", "casino", "gambling",
    "bitcoin price", "crypto pump", "token sale", "memecoin", "airdrop",
    "celebrity", "bollywood", "hollywood", "cricket", "football",
}


def is_allowed_topic(story: Story) -> bool:
    content = f"{story.title} {story.summary}".lower()
    for term in BLOCKED_TERMS:
        pattern = rf"\b{re.escape(term)}\b" if " " not in term else re.escape(term)
        if re.search(pattern, content, re.IGNORECASE):
            logger.info("Blocked story due to keyword match '%s': %s", term, story.title)
            return False
    return True


def can_post_now(state: dict, settings: Settings, force: bool = False) -> tuple[bool, str]:
    if force:
        return True, "Force mode enabled."

    tz = ZoneInfo(settings.timezone)
    now_local = datetime.now(tz)

    # 1. Check posting window
    start_h, start_m = map(int, settings.posting_window_start.split(":"))
    end_h, end_m = map(int, settings.posting_window_end.split(":"))
    window_start = now_local.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
    window_end = now_local.replace(hour=end_h, minute=end_m, second=0, microsecond=0)

    if not (window_start <= now_local <= window_end):
        return False, f"Outside posting window ({settings.posting_window_start} - {settings.posting_window_end} {settings.timezone}). Current: {now_local.strftime('%H:%M')}"

    # 2. Check daily post cap
    posts = state.get("posts", [])
    today_posts = [
        p for p in posts
        if datetime.fromisoformat(p["posted_at"]).astimezone(tz).date() == now_local.date()
    ]
    if len(today_posts) >= settings.daily_post_cap:
        return False, f"Daily cap of {settings.daily_post_cap} reached for today ({len(today_posts)} posted)."

    # 3. Check minimum spacing
    last_posted_str = state.get("last_posted_at")
    if last_posted_str:
        last_dt = datetime.fromisoformat(last_posted_str)
        elapsed = datetime.now(timezone.utc) - last_dt
        min_delta = timedelta(minutes=settings.min_spacing_minutes)
        if elapsed < min_delta:
            remaining_mins = int((min_delta - elapsed).total_seconds() // 60)
            return False, f"Minimum interval not reached. Wait {remaining_mins} more minutes."

    return True, "Ready to post."
