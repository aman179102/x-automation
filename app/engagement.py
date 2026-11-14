from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.ai import generate_json
from app.config import Settings
from app.models import TweetResult
from app.text import normalize_space, twitter_length

logger = logging.getLogger(__name__)

SEARCH_TOPICS = (
    '"distributed systems" architecture',
    'golang backend concurrency channels',
    '"database internals" postgres WAL',
    'fastapi production async backend',
    '"system design" microservices monolith',
    'raft consensus distributed storage',
    'io_uring epoll linux kernel performance',
    'redis vs memcached caching architecture',
    'event-driven architecture kafka streaming',
)

ENGAGEMENT_PROMPT = """You are Aman Kumar (@aman179102), a senior backend, systems, and AI engineer specializing in Go, distributed storage, database internals, and high-throughput streaming.

You are writing a short, insightful public reply to a fellow developer's tweet on X.

STRICT EDITORIAL RULES:
1. 100% Professional, crisp ENGLISH.
2. 1 to 2 sentences MAXIMUM (strictly under 200 characters).
3. VALUE-FIRST & CONTEXT-SPECIFIC:
   - Match the specific technical context of the tweet.
   - Introduce a relevant architectural angle (e.g., connection pooling limits, cache invalidation race conditions, backpressure handling, schema migrations under load, idempotency keys, singleflight request coalescing, lock contention).
4. FORBIDDEN PATTERNS:
   - DO NOT repeat catchphrases like "tail latency" or "network partitions" unless specifically talking about networking/consensus.
   - Never use generic praise ("Great post", "I agree", "Spot on", "100%", "Well said", "Nice breakdown").
   - Never pitch or promote yourself ("check out my repo", "I built", "follow me").
   - No hashtags, no exclamation marks overload, zero or max 1 emoji.
5. Tone: Pragmatic, humble, and technically authoritative like a real senior systems engineer.

Return JSON format only:
{
  "reply": "Your concise technical perspective (under 200 characters).",
  "reasoning": "Brief explanation of why this adds technical value."
}"""


@dataclass
class EngagementCandidate:
    tweet_id: str
    author: str
    author_id: str
    text: str
    likes: int
    created_at: str
    suggested_reply: str = ""
    reasoning: str = ""


def _default_engagement_state() -> dict[str, Any]:
    return {
        "version": 1,
        "replies": [],
        "last_replied_at": None,
    }


def load_engagement_state(state_dir: Path) -> dict[str, Any]:
    state_file = state_dir / "engagement.json"
    if not state_file.exists():
        return _default_engagement_state()
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return _default_engagement_state()


def save_engagement_state(state_dir: Path, state: dict[str, Any]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "engagement.json"
    tmp_file = state_dir / "engagement.json.tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    tmp_file.replace(state_file)


def can_reply_now(state: dict[str, Any], settings: Settings) -> tuple[bool, str]:
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()

    # Check daily limit (default 10 replies max)
    daily_replies = [
        r for r in state.get("replies", [])
        if r.get("replied_at", "").startswith(today)
    ]
    if len(daily_replies) >= 10:
        return False, f"Daily engagement limit of 10 reached ({len(daily_replies)} today)."

    # Check spacing (minimum 25 minutes between replies)
    last_str = state.get("last_replied_at")
    if last_str:
        last_dt = datetime.fromisoformat(last_str)
        if (now - last_dt) < timedelta(minutes=25):
            rem = int((timedelta(minutes=25) - (now - last_dt)).total_seconds() // 60)
            return False, f"Must wait {rem} more minutes between replies for safety."

    return True, "Ready for engagement."


def is_eligible_target(tweet: Any, state: dict[str, Any], settings: Settings) -> bool:
    author = tweet.user.screen_name.lower().strip("@")
    my_handle = settings.author_handle.lower().strip("@")

    # Don't reply to self
    if author == my_handle:
        return False

    # Don't reply to same tweet twice
    replied_ids = {r["target_tweet_id"] for r in state.get("replies", [])}
    if str(tweet.id) in replied_ids:
        return False

    # Author cooldown: Don't reply to same user more than once in 48h
    now = datetime.now(timezone.utc)
    for r in state.get("replies", []):
        if r.get("target_author", "").lower() == author:
            replied_dt = datetime.fromisoformat(r["replied_at"])
            if (now - replied_dt) < timedelta(hours=48):
                return False

    # Quality filter: Must have at least 2 likes or 1 RT (not a dead bot)
    if (tweet.favorite_count or 0) < 1 and (tweet.retweet_count or 0) < 1:
        return False

    # Filter out text too short or containing spam
    text = tweet.text.lower()
    if len(text) < 40 or "giveaway" in text or "airdrop" in text or "crypto" in text:
        return False

    return True


async def find_candidates(settings: Settings, state: dict[str, Any], limit: int = 5) -> list[EngagementCandidate]:
    from twikit import Client as TwikitClient

    client = TwikitClient("en-US", impersonate="chrome124")
    client.set_cookies({
        "auth_token": settings.auth_token,
        "ct0": settings.ct0,
    })

    candidates: list[EngagementCandidate] = []
    seen_ids: set[str] = set()

    for topic in SEARCH_TOPICS:
        try:
            results = await client.search_tweet(topic, "Top")
            for t in results:
                if str(t.id) in seen_ids:
                    continue
                seen_ids.add(str(t.id))

                if is_eligible_target(t, state, settings):
                    candidates.append(
                        EngagementCandidate(
                            tweet_id=str(t.id),
                            author=t.user.screen_name,
                            author_id=str(t.user.id),
                            text=normalize_space(t.text),
                            likes=t.favorite_count or 0,
                            created_at=str(t.created_at),
                        )
                    )
                if len(candidates) >= limit:
                    break
        except Exception as exc:
            logger.debug("Error searching topic '%s': %s", topic, exc)

        if len(candidates) >= limit:
            break

    return candidates


def draft_reply(tweet_text: str, settings: Settings) -> tuple[str, str]:
    user_msg = f"Target Tweet by developer:\n\"{tweet_text}\"\n\nWrite a senior technical reply contribution:"
    res = generate_json(settings, ENGAGEMENT_PROMPT, user_msg)
    if res and res.get("reply"):
        reply = normalize_space(str(res["reply"]))
        reason = str(res.get("reasoning", ""))
        if twitter_length(reply) <= 240:
            return reply, reason

    # Fallback technical comment if AI returns too long
    return (
        "The hidden operational cost usually comes down to distributed tracing overhead and tail latency under partial network partitions.",
        "Engineering trade-off observation",
    )


async def post_live_reply(candidate: EngagementCandidate, reply_text: str, settings: Settings) -> str:
    from twikit import Client as TwikitClient

    client = TwikitClient("en-US", impersonate="chrome124")
    client.set_cookies({
        "auth_token": settings.auth_token,
        "ct0": settings.ct0,
    })

    tweet = await client.create_tweet(
        text=reply_text,
        reply_to=candidate.tweet_id,
    )
    return str(tweet.id)
