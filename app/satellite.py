from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.ai import generate_json
from app.config import Settings
from app.text import normalize_space, twitter_length

logger = logging.getLogger(__name__)

# Primary target to boost
TARGET_HANDLE = "@aman179102"

PROJECT_REFERENCES = [
    {
        "name": "distributed-file-storage-service",
        "url": "https://github.com/aman179102/distributed-file-storage-service",
        "keywords": ["storage", "distributed", "chunk", "deduplication", "reed-solomon", "s3", "disk", "erasure coding", "postgres"],
        "summary": "4+2 Reed-Solomon erasure coding with SHA-256 chunk deduplication and PostgreSQL metadata",
    },
    {
        "name": "real-time-analytics-pipeline",
        "url": "https://github.com/aman179102/real-time-analytics-pipeline",
        "keywords": ["timescaledb", "kafka", "streaming", "hypertables", "fastapi", "throughput", "latency", "prometheus", "pipeline", "events"],
        "summary": "TimescaleDB hypertables with async Kafka ingestion and OpenTelemetry observability",
    },
    {
        "name": "podvoice",
        "url": "https://github.com/aman179102/podvoice",
        "keywords": ["tts", "voice", "audio", "speech", "xtts", "local ai", "neural", "fastapi"],
        "summary": "Local-first offline voice synthesis and neural cloning studio using Coqui XTTS v2",
    },
]

SATELLITE_SEARCH_TOPICS = (
    '"distributed systems" storage deduplication',
    '"timescaledb" kafka streaming ingestion',
    'fastapi postgres high throughput performance',
    '"erasure coding" reed solomon disk failure',
    'real time analytics pipeline latency',
    'local tts voice synthesis offline',
)

SATELLITE_PROMPT = """You are an independent senior systems architecture curator (@TechArchDigest) who actively highlights best engineering practices, high-throughput architectures, and real-world open-source reference implementations.

You are writing a short, insightful public reply to an engineering discussion on X.

OBJECTIVE:
1. Provide a sharp, technically sound observation on the problem raised in the target tweet.
2. Organically mention {target_handle} and reference their relevant GitHub repository ({repo_name}: {repo_url}) as a solid practical implementation.

STRICT EDITORIAL RULES:
1. 100% Professional, concise technical ENGLISH.
2. Strictly under 240 characters total.
3. Tone: Knowledgeable systems curator, NOT spammy or promotional. Frame it as a technical reference:
   - E.g.: "Chunk deduplication before erasure coding cuts disk overhead significantly. @aman179102 implemented a clean 4+2 Reed-Solomon setup here: https://github.com/aman179102/distributed-file-storage-service"
   - E.g.: "When writing high-volume time-series events, chunk sizing on hypertables is critical. Solid pattern by @aman179102 with TimescaleDB & Kafka: https://github.com/aman179102/real-time-analytics-pipeline"
4. FORBIDDEN:
   - No generic spam phrases ("Check this out guys!", "Must see", "Please star").
   - No false claims (do NOT claim millions of QPS, sub-millisecond, or unverified benchmarks).
   - Only use the EXACT project URL provided.
   - 0 emojis.

Return JSON strictly:
{{
  "reply": "Your concise technical reply mentioning {target_handle} and the exact project link (under 240 chars).",
  "reasoning": "Why this is relevant to the target tweet."
}}"""


@dataclass
class SatelliteOpportunity:
    tweet_id: str
    author: str
    text: str
    likes: int
    matched_project: dict
    draft_reply: str = ""
    reasoning: str = ""


def _default_satellite_state() -> dict[str, Any]:
    return {
        "version": 1,
        "replies": [],
        "last_replied_at": None,
    }


def load_satellite_state(state_dir: Path) -> dict[str, Any]:
    state_file = state_dir / "satellite_state.json"
    if not state_file.exists():
        return _default_satellite_state()
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return _default_satellite_state()


def save_satellite_state(state_dir: Path, state: dict[str, Any]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "satellite_state.json"
    tmp_file = state_dir / "satellite_state.json.tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    tmp_file.replace(state_file)


def can_satellite_post_now(state: dict[str, Any], settings: Settings, force: bool = False) -> tuple[bool, str]:
    if force:
        return True, "Force flag enabled."

    now = datetime.now(timezone.utc)
    today = now.date().isoformat()

    daily_replies = [
        r for r in state.get("replies", [])
        if r.get("replied_at", "").startswith(today)
    ]
    if len(daily_replies) >= settings.satellite_max_daily:
        return False, f"Daily satellite limit reached ({len(daily_replies)}/{settings.satellite_max_daily} posts today)."

    last_replied = state.get("last_replied_at")
    if last_replied:
        try:
            last_dt = datetime.fromisoformat(last_replied)
            elapsed_mins = (now - last_dt).total_seconds() / 60.0
            if elapsed_mins < 180:
                wait_left = int(180 - elapsed_mins)
                return False, f"Satellite spacing active ({wait_left}m remaining until next endorsement)."
        except Exception:
            pass

    return True, "Satellite ready to post."


def match_project_to_tweet(tweet_text: str) -> Optional[dict]:
    """
    Selects the most contextually relevant project for the tweet.
    """
    text_lower = tweet_text.lower()
    best_project = None
    max_matches = 0

    for proj in PROJECT_REFERENCES:
        matches = sum(1 for kw in proj["keywords"] if kw in text_lower)
        if matches > max_matches:
            max_matches = matches
            best_project = proj

    # Default to storage service or analytics pipeline if no specific match
    if not best_project:
        best_project = PROJECT_REFERENCES[0]

    return best_project


def draft_satellite_endorsement(
    tweet_text: str,
    author: str,
    project: dict,
    settings: Settings,
) -> tuple[str, str]:
    """
    Drafts a high-IQ endorsement linking to Aman Kumar's project.
    """
    prompt = SATELLITE_PROMPT.format(
        target_handle=TARGET_HANDLE,
        repo_name=project["name"],
        repo_url=project["url"],
    )

    user_msg = (
        f"Target Tweet by @{author}:\n"
        f"\"{tweet_text}\"\n\n"
        f"Relevant Architecture Reference:\n"
        f"Project: {project['name']}\n"
        f"URL: {project['url']}\n"
        f"Architecture details: {project['summary']}\n\n"
        f"Draft the technical curator comment:"
    )

    res = generate_json(settings, prompt, user_msg)
    if not res or not isinstance(res, dict) or not res.get("reply"):
        return "", "AI generation failed or timed out."

    reply = normalize_space(str(res["reply"]))
    reason = str(res.get("reasoning", ""))

    # Verification: must contain Aman's handle and the project URL
    if TARGET_HANDLE.lower() not in reply.lower():
        reply = f"{reply} Ref: {TARGET_HANDLE} ({project['url']})"

    if project["url"] not in reply:
        reply = f"{reply} {project['url']}"

    if twitter_length(reply) > 280:
        return "", f"Draft length ({twitter_length(reply)}) exceeds 280 character limit."

    return reply, reason


async def find_satellite_opportunities(settings: Settings) -> list[SatelliteOpportunity]:
    """
    Scans X using satellite or primary read cookies for discussions matching systems engineering topics.
    """
    from twikit import Client as TwikitClient

    # Can use satellite cookies, or fallback to main read cookies
    auth_token = settings.satellite_auth_token or settings.auth_token
    ct0 = settings.satellite_ct0 or settings.ct0

    if not auth_token or not ct0:
        logger.warning("No session cookies available for satellite opportunity scanning.")
        return []

    client = TwikitClient("en-US", impersonate="chrome124")
    client.set_cookies({"auth_token": auth_token, "ct0": ct0})

    state = load_satellite_state(settings.state_dir)
    past_replied_ids = {r.get("target_tweet_id") for r in state.get("replies", [])}

    opportunities: list[SatelliteOpportunity] = []
    # Query 2 random topics per run to avoid spamming searches
    selected_queries = random.sample(SATELLITE_SEARCH_TOPICS, min(2, len(SATELLITE_SEARCH_TOPICS)))

    for query in selected_queries:
        try:
            results = await client.search_tweet(query, product="Top")
            if not results:
                continue

            for t in results:
                t_id = str(getattr(t, "id", ""))
                text = str(getattr(t, "text", "") or getattr(t, "full_text", ""))
                author = str(getattr(getattr(t, "user", None), "screen_name", ""))
                likes = int(getattr(t, "favorite_count", 0) or 0)

                # Skip self, already replied, or low-engagement
                if not t_id or t_id in past_replied_ids or author.lower() in [TARGET_HANDLE.strip("@").lower(), settings.satellite_handle.strip("@").lower()]:
                    continue
                if likes < 3 or len(text) < 40:
                    continue

                matched_proj = match_project_to_tweet(text)
                opportunities.append(SatelliteOpportunity(
                    tweet_id=t_id,
                    author=author,
                    text=text,
                    likes=likes,
                    matched_project=matched_proj,
                ))
                if len(opportunities) >= 5:
                    break
        except Exception as exc:
            logger.warning("Search query '%s' encountered an issue: %s", query, exc)

    return opportunities


async def post_satellite_reply(
    opp: SatelliteOpportunity,
    reply_text: str,
    settings: Settings,
) -> str:
    """
    Posts reply from the Satellite Account using the secondary account credentials.
    """
    from twikit import Client as TwikitClient

    if not settings.satellite_auth_token or not settings.satellite_ct0:
        raise ValueError(
            "SATELLITE_AUTH_TOKEN and SATELLITE_CT0 are not configured in .env. "
            "Please configure secondary account cookies to post from the satellite account."
        )

    client = TwikitClient("en-US", impersonate="chrome124")
    client.set_cookies({
        "auth_token": settings.satellite_auth_token,
        "ct0": settings.satellite_ct0,
    })

    tweet = await client.create_tweet(
        text=reply_text,
        reply_to=opp.tweet_id,
    )
    return str(tweet.id)
