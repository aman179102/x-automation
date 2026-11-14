from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.config import Settings
from app.state import load_state

logger = logging.getLogger(__name__)


@dataclass
class PostMetrics:
    tweet_id: str
    format_kind: str
    likes: int = 0
    reposts: int = 0
    replies: int = 0
    impressions: Optional[int] = None  # None if unavailable from endpoint
    collected_at: str = ""


def _default_analytics_state() -> dict[str, Any]:
    return {
        "version": 1,
        "last_updated": None,
        "metrics": {},
        "format_performance": {},
    }


def load_analytics_state(state_dir: Path) -> dict[str, Any]:
    f = state_dir / "analytics.json"
    if not f.exists():
        return _default_analytics_state()
    try:
        with open(f, "r", encoding="utf-8") as fp:
            return json.load(fp)
    except Exception:
        return _default_analytics_state()


def save_analytics_state(state_dir: Path, state: dict[str, Any]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    f = state_dir / "analytics.json"
    tmp = state_dir / "analytics.json.tmp"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(state, fp, indent=2, ensure_ascii=False)
    tmp.replace(f)


async def fetch_real_tweet_metrics(tweet_id: str, settings: Settings) -> Optional[dict[str, Any]]:
    """
    Fetches real metrics for a published tweet using the authenticated client.
    Never fabricates metrics. If a metric cannot be retrieved, returns None.
    """
    if not settings.auth_token or not settings.ct0:
        return None

    try:
        from twikit import Client as TwikitClient
        client = TwikitClient("en-US", impersonate="chrome124")
        client.set_cookies({
            "auth_token": settings.auth_token,
            "ct0": settings.ct0,
        })

        tweet = await client.get_tweet_by_id(tweet_id)
        if not tweet:
            return None

        # Extract only actually present metrics
        likes = getattr(tweet, "favorite_count", 0) or 0
        reposts = getattr(tweet, "retweet_count", 0) or 0
        replies = getattr(tweet, "reply_count", 0) or 0
        views = getattr(tweet, "view_count", None)

        return {
            "tweet_id": tweet_id,
            "likes": int(likes),
            "reposts": int(reposts),
            "replies": int(replies),
            "impressions": int(views) if views is not None else None,
            "profile_visits": None,  # Not exposed via Twitter public/v2 endpoint without web dashboard export
            "collected_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        logger.debug("Failed retrieving metrics for tweet %s: %s", tweet_id, exc)
        return None


def sync_recent_analytics(settings: Settings) -> dict[str, Any]:
    """
    Syncs verified engagement metrics for the last 15 published posts.
    Calculates empirical format performance to guide content selection.
    """
    state_dir = settings.state_dir
    history = load_state(state_dir)
    analytics = load_analytics_state(state_dir)

    posts = history.get("posts", [])
    if not posts:
        return analytics

    recent = posts[-15:]
    updated_count = 0

    for p in recent:
        tid = str(p.get("tweet_id", ""))
        if not tid:
            continue

        try:
            metrics = asyncio.run(fetch_real_tweet_metrics(tid, settings))
            if metrics:
                analytics.setdefault("metrics", {})[tid] = metrics
                analytics["metrics"][tid]["format_kind"] = p.get("format_kind", "news")
                analytics["metrics"][tid]["title"] = p.get("title", "")
                updated_count += 1
        except Exception as exc:
            logger.debug("Metrics collection skipped for %s: %s", tid, exc)

    # Compute empirical format averages
    format_stats: dict[str, dict[str, float]] = {}
    for tid, m in analytics.get("metrics", {}).items():
        kind = m.get("format_kind", "news")
        stats = format_stats.setdefault(kind, {"likes": 0, "reposts": 0, "count": 0})
        stats["likes"] += m.get("likes", 0)
        stats["reposts"] += m.get("reposts", 0)
        stats["count"] += 1

    format_perf = {}
    for kind, stats in format_stats.items():
        if stats["count"] > 0:
            format_perf[kind] = {
                "avg_likes": round(stats["likes"] / stats["count"], 2),
                "avg_reposts": round(stats["reposts"] / stats["count"], 2),
                "sample_size": stats["count"],
            }

    analytics["format_performance"] = format_perf
    analytics["last_updated"] = datetime.now(timezone.utc).isoformat()
    save_analytics_state(state_dir, analytics)

    logger.info("Synced real analytics for %d posts across formats: %s", updated_count, format_perf)
    return analytics
