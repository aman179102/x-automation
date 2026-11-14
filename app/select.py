from __future__ import annotations

import logging
import random
from datetime import datetime, timezone
from typing import Any

from app.compliance import is_allowed_topic
from app.extractor import extract_article_content
from app.models import Story
from app.state import is_seen

logger = logging.getLogger(__name__)

TECH_BOOST_TERMS = {
    "distributed": 1.8,
    "architecture": 1.7,
    "database": 1.7,
    "storage": 1.6,
    "concurrency": 1.6,
    "consensus": 1.6,
    "golang": 1.5,
    "runtime": 1.5,
    "compiler": 1.5,
    "performance": 1.5,
    "latency": 1.5,
    "io_uring": 1.5,
    "agentic": 1.4,
    "benchmark": 1.4,
    "open source": 1.3,
    "release": 1.3,
}

PR_FLUFF_TERMS = {
    "caregivers", "business of law", "elevates travel", "customer story",
    "partnering with", "advisory", "marketing", "earnings call",
}


def score_story(story: Story) -> float:
    base = story.item.score
    text = f"{story.title} {story.summary}".lower()

    # De-prioritize security CVE noise
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

    # Recency bonus for news items
    if story.source_kind == "news":
        now = datetime.now(timezone.utc)
        age_hours = (now - story.item.published_at).total_seconds() / 3600.0
        recency_mult = 1.0
        if age_hours <= 12:
            recency_mult = 1.3
        elif age_hours <= 24:
            recency_mult = 1.15
        elif age_hours > 72:
            recency_mult = 0.6
        return round(base * multiplier * recency_mult, 3)

    return round(base * multiplier, 3)


def rank_candidates(stories: list[Story], state: dict[str, Any]) -> list[tuple[Story, float]]:
    scored: list[tuple[Story, float]] = []

    for s in stories:
        if is_seen(state, s.item.url):
            continue

        if not is_allowed_topic(s):
            continue

        score = score_story(s)
        scored.append((s, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def select_best_story(stories: list[Story], state: dict[str, Any], preferred_kind: str | None = None) -> Story | None:
    """
    Selects the highest-ranked qualified candidate.
    For news stories, enforces full article extraction and verification.
    """
    ranked = rank_candidates(stories, state)
    if not ranked:
        return None

    # Filter by preferred format kind if specified to maintain format mix
    if preferred_kind:
        filtered = [s for s, _ in ranked if s.source_kind == preferred_kind]
        if filtered:
            candidates = filtered
        else:
            candidates = [s for s, _ in ranked]
    else:
        candidates = [s for s, _ in ranked]

    for candidate in candidates:
        if candidate.source_kind == "news":
            logger.info("Extracting and verifying source article: %s", candidate.item.url)
            extracted = extract_article_content(candidate.item.url)
            if not extracted.is_valid:
                logger.info(
                    "Skipping news story '%s' (Extraction failed: %s)",
                    candidate.title,
                    extracted.failure_reason,
                )
                continue
            # Store validated article text for writer and verification
            candidate.facts["article_text"] = extracted.text
            candidate.facts["article_published_date"] = extracted.published_date
            return candidate
        else:
            # Curated deep dives, showcases, and discussions are pre-grounded
            return candidate

    return None
