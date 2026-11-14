from __future__ import annotations

import json
import logging
from typing import Any

from app.ai import generate_json
from app.config import Settings
from app.models import PostDraft, Story
from app.text import (
    clean_markdown,
    domain,
    hashtags_for_category,
    normalize_space,
    truncate_words,
    twitter_length,
)

logger = logging.getLogger(__name__)

DEEP_DIVE_PROMPT = """You are Aman Kumar (@aman179102), a senior systems and backend engineer writing an insightful technical breakdown on X (Twitter).
Target Audience: Senior software engineers, systems architects, and founders.

CRITICAL EDITORIAL PRINCIPLES:
1. Think like a software engineer. Explain like a great teacher. Write like a real person.
2. Tone: Pragmatic, humble, deeply technical, conversational.
3. Natural, compelling opening: Start with an engineering mechanism, trade-off, or practical dilemma developers face.
4. Explain the "under the hood" reality: Mention concrete primitives (e.g., sequential disk I/O, ring buffers, work-stealing, lock contention, tuple immutability).
5. FORBIDDEN:
   - No clickbait, no ALL-CAPS words.
   - Never say "Breaking news", "Developers, listen up", "Game changer", "In an exciting update".
   - No excessive emojis (0 or 1 max).
6. LENGTH BUDGET:
   - Body must be strictly under 230 characters so the final tweet with tags fits comfortably within 280 characters.
7. Return JSON strictly:
{
  "body": "The complete post text (under 230 characters).",
  "image_title": "Concise technical title for concept card (3-6 words)",
  "technical_takeaway": "Key architectural trade-off or metric",
  "visual_prompt": "Clean, minimalist technical diagram description (e.g., 'minimal dark schematic diagram of sequential log-structured merge tree memtable flushing to disk, dark slate background, high contrast lines, no neon')",
  "alt_text": "Detailed accessibility description of the technical concept",
  "has_image": true
}"""

SHOWCASE_PROMPT = """You are systems engineer Aman Kumar (@aman179102), sharing real architectural decisions and lessons from building your software projects on X (Twitter).

CRITICAL EDITORIAL PRINCIPLES:
1. Authentic build-in-public / engineering showcase.
2. Focus on verified core design decisions and trade-offs (e.g., 4+2 Reed-Solomon erasure coding versus 3x replication, TimescaleDB chunk compression, or offline local XTTS synthesis).
3. Humble yet authoritative tone. No hype, no fabricated benchmarks (no 'sub-millisecond' or 'millions of events' claims unless specifically verified).
4. Strictly refer to projects by their actual license: do NOT call proprietary software open source.
5. Total body must be strictly under 200 characters.
6. Return JSON strictly:
{
  "body": "The post text (under 200 characters).",
  "image_title": "Project or architecture title (3-6 words)",
  "technical_takeaway": "The concrete trade-off or architectural metric",
  "visual_prompt": "Clean minimalist system architecture block diagram on dark slate, crisp lines",
  "alt_text": "Architecture overview of the project",
  "has_image": true
}"""

VERIFIED_NEWS_PROMPT = """You are Aman Kumar (@aman179102), a senior software engineer analyzing a verified technology announcement on X (Twitter).

CRITICAL EDITORIAL PRINCIPLES:
1. DO NOT simply rewrite the headline. Add original engineering analysis.
2. Address: What actually changed technically, and why does it matter to developers?
3. STRICT FACTUAL ACCURACY: Use ONLY verified facts from the provided article text. Never invent numbers, versions, benchmarks, or release dates.
4. Differentiate FACT vs ANALYSIS:
   - State the confirmed development clearly.
   - State the technical implication or trade-off.
5. STRICT LENGTH BUDGET: Total body must be strictly under 155 characters (to leave room for source URL without truncation).
6. Return JSON strictly:
{
  "body": "The verified announcement and technical analysis (under 155 characters).",
  "image_title": "Subject headline for graphic card (3-6 words)",
  "technical_takeaway": "Core architectural impact or caveat",
  "visual_prompt": "Contextual, high-precision technical visualization of the specific technology, clean minimal composition, dark background, no neon clutter",
  "alt_text": "Accurate description of the technology development",
  "has_image": true
}"""

DISCUSSION_PROMPT = """You are Aman Kumar (@aman179102), posing a thoughtful, high-IQ architecture dilemma on X (Twitter) that senior developers genuinely want to discuss.

CRITICAL EDITORIAL PRINCIPLES:
1. Ask a meaningful, practical systems design question about real trade-offs (e.g. read replicas vs sharding limits, eventual consistency vs consensus overhead).
2. Ground the question with a concrete scenario or operational friction.
3. Keep it conversational and thought-provoking.
4. Total body must be under 240 characters.
5. Return JSON strictly:
{
  "body": "The discussion question (under 240 characters).",
  "image_title": "",
  "technical_takeaway": "",
  "visual_prompt": "",
  "alt_text": "",
  "has_image": false
}"""


def _fallback_writer(story: Story, settings: Settings) -> PostDraft:
    """Deterministic, high-quality fallback writer when LLM is unavailable."""
    title = clean_markdown(story.title)
    summary = clean_markdown(story.summary)

    if story.source_kind == "showcase":
        feature = story.facts.get("feature", "resilient zero-dependency design")
        tradeoff = story.facts.get("tradeoff", "minimal memory footprint")
        body = f"Architecture notes on {title}: Designed around {tradeoff}. {feature}."
        img_title = title
        visual_prompt = "minimalist distributed systems block diagram dark slate vector"
        has_image = True
    elif story.source_kind == "deep_dive":
        concept = story.facts.get("core_concept", title)
        takeaway = story.facts.get("key_takeaway", summary)
        body = f"Engineering takeaway on {concept}: {takeaway}"
        img_title = truncate_words(title, 6)
        visual_prompt = "clean architectural diagram dark slate background high contrast"
        has_image = True
    elif story.source_kind == "discussion":
        takeaway = story.facts.get("key_takeaway", summary)
        body = f"{title}. In production, {takeaway} Where do you draw the line?"
        img_title = ""
        visual_prompt = ""
        has_image = False
    else:
        # Verified news fallback
        first_sentence = summary.split(".")[0].strip() if summary else title
        body = f"{title}. {first_sentence}"
        img_title = truncate_words(title, 6)
        visual_prompt = "minimalist technical graphic dark theme"
        has_image = True

    body = truncate_words(body, 32)
    tags = story.item.extra.get("tags") or hashtags_for_category(story.category)

    return PostDraft(
        body=body,
        image_title=img_title,
        image_facts=[story.category.upper()],
        alt_text=f"Technical update on {title}",
        visual_prompt=visual_prompt,
        category=story.category,
        story=story,
        tags=tags[:2],
        format_kind=story.source_kind,
        has_image=has_image,
        technical_takeaway=story.facts.get("key_takeaway", ""),
    )


class Writer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def create_draft(self, story: Story) -> PostDraft:
        kind = story.source_kind
        if kind == "showcase":
            prompt_sys = SHOWCASE_PROMPT
        elif kind == "deep_dive":
            prompt_sys = DEEP_DIVE_PROMPT
        elif kind == "discussion":
            prompt_sys = DISCUSSION_PROMPT
        else:
            prompt_sys = VERIFIED_NEWS_PROMPT

        # Build payload with full extracted article text if available
        article_text = story.facts.get("article_text", story.summary)
        payload = {
            "title": story.title,
            "source": story.primary_source.name,
            "category": story.category,
            "facts": story.facts,
            "article_excerpt": truncate_words(article_text, 400),
            "format_kind": kind,
        }

        user_input = f"Input Technical Data:\n{json.dumps(payload, indent=2)}"
        result = generate_json(self.settings, prompt_sys, user_input)

        if not result or not result.get("body"):
            logger.info("Using deterministic fallback writer for '%s'", story.title)
            return _fallback_writer(story, self.settings)

        body = normalize_space(str(result["body"]))
        image_title = normalize_space(str(result.get("image_title", "")))
        technical_takeaway = normalize_space(str(result.get("technical_takeaway", "")))
        visual_prompt = str(result.get("visual_prompt", ""))
        alt_text = str(result.get("alt_text", image_title or body[:50]))
        has_image = bool(result.get("has_image", kind != "discussion"))
        tags = story.item.extra.get("tags") or hashtags_for_category(story.category)

        normalized_kind = "news" if kind in ("news", "verified_news_analysis") else kind
        return PostDraft(
            body=body,
            image_title=image_title,
            image_facts=[technical_takeaway] if technical_takeaway else [story.category.upper()],
            alt_text=alt_text,
            visual_prompt=visual_prompt,
            category=story.category,
            story=story,
            tags=tags[:2],
            format_kind=normalized_kind,
            has_image=has_image,
            technical_takeaway=technical_takeaway,
        )

    def format_tweet_text(self, draft: PostDraft) -> str:
        """
        Assembles the final tweet text within Twitter's 280-char limit:
        [Body text]

        Source: [Domain] ([URL]) (for news) / Repo: [URL] (for showcase)
        [Hashtags]
        """
        tags_str = " ".join(draft.tags[:2]) if draft.tags else ""
        source_line = ""

        if draft.story:
            if draft.story.source_kind == "showcase":
                source_line = f"Repo: {draft.story.item.url}"
            elif draft.story.source_kind == "news":
                src_domain = domain(draft.story.item.url)
                source_line = f"Source: {draft.story.primary_source.name} ({src_domain})\n{draft.story.item.url}"
            elif draft.story.source_kind == "oss_discovery":
                source_line = f"Project: {draft.story.item.url}"

        parts = [draft.body]
        if source_line:
            parts.append(source_line)
        if tags_str and draft.format_kind != "discussion":
            parts.append(tags_str)

        tweet = "\n\n".join(parts)

        # Enforce strict 280-char budget (URLs count as 23 chars)
        if twitter_length(tweet) > 280:
            overflow = twitter_length(tweet) - 275
            trimmed_body = draft.body[:-overflow].rstrip() + "..."
            parts[0] = trimmed_body
            tweet = "\n\n".join(parts)

        return tweet
