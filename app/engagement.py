from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

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
2. 1 to 2 sentences MAXIMUM (strictly under 190 characters).
3. VALUE-FIRST & CONTEXT-SPECIFIC:
   - Directly respond to the specific topic and technical trade-off raised in the target tweet.
   - Add a concrete, actionable engineering angle (e.g., connection pooling limits, cache invalidation races, backpressure handling, schema migrations, idempotency keys, singleflight request coalescing, lock contention).
4. FORBIDDEN:
   - DO NOT repeat canned catchphrases ("tail latency", "network partitions", "serialization overhead") unless the tweet is strictly about that specific subject.
   - NEVER use generic praise ("Great post", "I agree", "Spot on", "100%", "Well said", "Nice point").
   - NEVER pitch or self-promote ("check out my repo", "I built", "follow me").
   - No hashtags, no exclamation marks overload, 0 emojis.
5. Tone: Pragmatic, humble, and technically authoritative like a real senior systems engineer.

Return JSON format strictly:
{
  "reply": "Your concise technical perspective (strictly under 190 characters).",
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
    score: float = 0.0
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

    daily_replies = [
        r for r in state.get("replies", [])
        if r.get("replied_at", "").startswith(today)
    ]
    if len(daily_replies) >= 8:
        return False, f"Daily engagement limit of 8 reached ({len(daily_replies)} today)."

    last_str = state.get("last_replied_at")
    if last_str:
        last_dt = datetime.fromisoformat(last_str)
        if (now - last_dt) < timedelta(minutes=30):
            rem = int((timedelta(minutes=30) - (now - last_dt)).total_seconds() // 60)
            return False, f"Spacing cooldown: wait {rem} more minutes between replies."

    return True, "Ready for engagement."


def is_eligible_target(tweet: Any, state: dict[str, Any], settings: Settings) -> tuple[bool, str]:
    author = tweet.user.screen_name.lower().strip("@")
    my_handle = settings.author_handle.lower().strip("@")

    # Don't reply to self
    if author == my_handle:
        return False, "Target is self"

    # Don't reply to same tweet twice
    replied_ids = {str(r["target_tweet_id"]) for r in state.get("replies", [])}
    if str(tweet.id) in replied_ids:
        return False, "Already replied to tweet"

    # Author cooldown: Don't reply to same user more than once in 72h
    now = datetime.now(timezone.utc)
    for r in state.get("replies", []):
        if r.get("target_author", "").lower() == author:
            replied_dt = datetime.fromisoformat(r["replied_at"])
            if (now - replied_dt) < timedelta(hours=72):
                return False, f"Author @{author} in 72h cooldown"

    # Recency check: Avoid old or resolved discussions (> 48 hours old)
    created_str = getattr(tweet, "created_at", None)
    if created_str:
        try:
            # Twikit returns standard RFC 2822 or ISO format
            if hasattr(tweet, "created_at_datetime"):
                created_dt = tweet.created_at_datetime
            else:
                from email.utils import parsedate_to_datetime
                created_dt = parsedate_to_datetime(str(created_str))
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=timezone.utc)
            if (now - created_dt) > timedelta(hours=48):
                return False, "Tweet older than 48 hours"
        except Exception:
            pass

    # Engagement quality threshold
    favs = tweet.favorite_count or 0
    if favs < 2:
        return False, "Insufficient engagement (< 2 likes)"

    # Text length & noise filter
    text = tweet.text.lower()
    if len(text) < 45:
        return False, "Text too short for technical discussion"

    spam_terms = ["giveaway", "airdrop", "crypto", "presale", "whitelist", "dm me", "follow back", "hiring"]
    if any(s in text for s in spam_terms):
        return False, "Contains spam or non-technical promotional terms"

    return True, "Eligible"


def score_candidate(candidate: EngagementCandidate) -> float:
    """Ranks candidates by technical relevance, recency, and discussion depth."""
    text_lower = candidate.text.lower()
    score = 1.0

    tech_terms = [
        "architecture", "distributed", "database", "postgres", "concurrency",
        "golang", "fastapi", "latency", "scale", "performance", "cache",
        "streaming", "kafka", "raft", "storage", "microservices",
    ]
    term_matches = sum(1 for t in tech_terms if t in text_lower)
    score += term_matches * 0.4

    # Engagement bonus
    if candidate.likes >= 10:
        score += 0.5
    elif candidate.likes >= 5:
        score += 0.25

    return round(score, 2)


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

                eligible, reason = is_eligible_target(t, state, settings)
                if eligible:
                    cand = EngagementCandidate(
                        tweet_id=str(t.id),
                        author=t.user.screen_name,
                        author_id=str(t.user.id),
                        text=normalize_space(t.text),
                        likes=t.favorite_count or 0,
                        created_at=str(getattr(t, "created_at", "")),
                    )
                    cand.score = score_candidate(cand)
                    candidates.append(cand)
        except Exception as exc:
            logger.debug("Error searching topic '%s': %s", topic, exc)

        if len(candidates) >= limit * 2:
            break

    # Rank candidates by score
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates[:limit]


def draft_reply(tweet_text: str, settings: Settings, author: str = "") -> tuple[str, str]:
    """
    Generates a context-specific, senior technical reply.
    STRICTLY SKIPS if AI generation fails: NEVER returns a hardcoded fallback string.
    """
    author_prefix = f"@{author}: " if author else ""
    user_msg = (
        f"Target Developer Tweet ({author_prefix}):\n"
        f"\"{tweet_text}\"\n\n"
        f"Draft a senior engineering perspective that directly addresses the architectural trade-off:"
    )

    res = generate_json(settings, ENGAGEMENT_PROMPT, user_msg)
    if not res or not isinstance(res, dict) or not res.get("reply"):
        logger.warning("AI reply generation failed or timed out. Skipping reply (fail-closed).")
        return "", "AI generation failed or service timed out (canned fallback disabled)."

    reply = normalize_space(str(res["reply"]))
    reason = str(res.get("reasoning", ""))

    if twitter_length(reply) > 200:
        logger.warning("Drafted reply exceeds 200 characters (%d chars). Skipping.", twitter_length(reply))
        return "", f"Reply length {twitter_length(reply)} exceeds 200 characters."

    return reply, reason


def validate_reply_quality(
    candidate: EngagementCandidate,
    reply_text: str,
    state: dict[str, Any],
) -> tuple[bool, str]:
    """
    Final validation stage before publishing a reply:
    1. Checks length.
    2. Rejects generic canned replies or repetitive patterns.
    3. Prevents repeating catchphrases across recent replies in history.
    """
    if not reply_text.strip():
        return False, "Reply text is empty."

    if twitter_length(reply_text) > 200:
        return False, f"Reply length ({twitter_length(reply_text)}) exceeds limit."

    # Check for forbidden praise or canned openings
    forbidden_starters = ["great post", "nice point", "i agree", "spot on", "well said", "100%"]
    reply_lower = reply_text.lower()
    for f in forbidden_starters:
        if reply_lower.startswith(f):
            return False, f"Forbidden generic opening detected: '{f}'"

    # Check against recent published replies in state to prevent repetition
    recent_replies = [r.get("reply_text", "").lower() for r in state.get("replies", [])[-15:]]
    for past in recent_replies:
        # If the reply shares more than the first 25 characters with a previous reply, reject!
        if len(reply_lower) >= 25 and reply_lower[:25] in past:
            return False, "Reply opening too repetitive compared to recent published replies."

    # Rejection of catchphrase loops: do not allow 'tail latency under network partitions'
    if "tail latency under partial network partitions" in reply_lower:
        return False, "Detected repetitive canned catchphrase loop."

    return True, "Reply passed quality validation."


def find_engagement_candidates(settings: Settings) -> list[EngagementCandidate]:
    state = load_engagement_state(settings.state_dir)
    return asyncio.run(find_candidates(settings, state, limit=6))


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
