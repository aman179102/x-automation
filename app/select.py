from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.compliance import is_allowed_topic
from app.models import Story
from app.state import is_seen

logger = logging.getLogger(__name__)

TECH_BOOST_TERMS = {
    "distributed": 1.8,
    "architecture": 1.7,
    "alphaproof": 1.7,
    "verification": 1.7,
    "agentic": 1.6,
    "on-device": 1.6,
    "compiler": 1.6,
    "storage": 1.6,
    "concurrency": 1.5,
    "open source": 1.5,
    "benchmark": 1.5,
    "database": 1.5,
    "performance": 1.5,
    "llm": 1.4,
    "golang": 1.4,
    "runtime": 1.4,
    "model": 1.3,
    "release": 1.3,
}

PR_FLUFF_TERMS = {
    "caregivers", "business of law", "elevates travel", "customer story",
    "partnering with", "advisory", "marketing",
}


def score_story(story: Story) -> float:
    base = story.item.score
    text = f"{story.title} {story.summary}".lower()

    # De-prioritize security/vulnerabilities
    if story.category == "security":
        base *= 0.50

    # De-prioritize non-technical PR stories
    for term in PR_FLUFF_TERMS:
        if term in text:
            base *= 0.40
            break

    # Keyword bonuses
    multiplier = 1.0
    for term, boost in TECH_BOOST_TERMS.items():
        if term in text:
            multiplier = max(multiplier, boost)

    # Recency bonus (stories < 12h old get a bump)
    now = datetime.now(timezone.utc)
    age_hours = (now - story.item.published_at).total_seconds() / 3600.0
    recency_mult = 1.0
    if age_hours <= 6:
        recency_mult = 1.3
    elif age_hours <= 12:
        recency_mult = 1.15
    elif age_hours > 48:
        recency_mult = 0.7

    return round(base * multiplier * recency_mult, 3)


def rank_candidates(stories: list[Story], state: dict[str, Any]) -> list[tuple[Story, float]]:
    scored: list[tuple[Story, float]] = []
    seen_fingerprints: set[str] = set()

    for s in stories:
        # Check seen
        if is_seen(state, s.item.url):
            continue

        # Check compliance / topic filter
        if not is_allowed_topic(s):
            continue

        score = score_story(s)
        scored.append((s, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def select_best_story(stories: list[Story], state: dict[str, Any]) -> Story | None:
    ranked = rank_candidates(stories, state)
    if not ranked:
        return None
    return ranked[0][0]
