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
    strip_urls,
    truncate_words,
    twitter_length,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a senior tech editor writing crisp, viral, and factually accurate tech posts for X (Twitter).
Target audience: Software engineers, AI researchers, and tech founders.

HARD CONSTRAINTS:
1. Strictly write in professional, punchy ENGLISH. No other language.
2. Character budget: The entire tweet (including hashtags and source) MUST BE UNDER 250 characters.
3. Style:
   - Open with the direct consequence or why it matters ("Engineers can now...", "Cloud teams face...").
   - Never start with generic filler ("Announced that", "Breaking news", "In an exciting update").
   - Include specific versions, CVEs, numbers, or metrics whenever present.
   - Use active voice, crisp sentences (max 18 words per sentence).
   - Zero hallucinations. Use only facts supplied in the input.
4. Output JSON format only:
{
  "body": "The tweet text without source link or hashtags (under 190 characters).",
  "image_title": "Concise headline for the card (3-7 words).",
  "image_facts": ["1-2 key metrics or technical highlights"],
  "visual_prompt": "1 sentence describing a dark aesthetic tech scene for image generation (e.g. 'futuristic server racks glowing with cybernetic blue and amber light, octane render 3d')",
  "alt_text": "1 descriptive sentence for accessibility and SEO image indexing."
}"""

SHOWCASE_PROMPT = """You are crafting an authentic, high-impact developer showcase tweet for an open-source project by systems engineer Aman Kumar.

HARD CONSTRAINTS:
1. Strictly in ENGLISH.
2. Tone: humble yet deeply technical, engineering-first (no buzzword soup, no hyper-hype).
3. Focus on architecture: mention key primitives (e.g. Raft, chunking, concurrency, latency).
4. Total body under 190 characters.
5. Output JSON:
{
  "body": "...",
  "image_title": "...",
  "image_facts": ["..."],
  "visual_prompt": "...",
  "alt_text": "..."
}"""


def _fallback_writer(story: Story, settings: Settings) -> PostDraft:
    """Deterministic, high-quality template writer when LLM is unavailable."""
    title = clean_markdown(story.title)
    summary = clean_markdown(story.summary)

    if story.is_showcase:
        body = f"Engineering deep dive: {title}. Built for high resilience with {story.facts.get('feature', 'zero-dependency design')}."
        visual_prompt = "abstract distributed system network nodes graph dark neon cyan aesthetic 8k"
        img_title = title
    else:
        # Extract first sentence of summary if available
        first_sentence = summary.split(".")[0].strip() if summary else title
        if len(first_sentence) > 140:
            first_sentence = truncate_words(first_sentence, 20)
        body = f"{title}. {first_sentence}"
        visual_prompt = f"abstract futuristic {story.category} technology scene dark sleek lighting 3d render"
        img_title = truncate_words(title, 7)

    body = truncate_words(body, 28)
    tags = hashtags_for_category(story.category)

    return PostDraft(
        body=body,
        image_title=img_title,
        image_facts=[story.category.upper()],
        alt_text=f"Technical update on {title}",
        visual_prompt=visual_prompt,
        category=story.category,
        story=story,
        tags=tags,
    )


class Writer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def create_draft(self, story: Story) -> PostDraft:
        prompt_sys = SHOWCASE_PROMPT if story.is_showcase else SYSTEM_PROMPT
        payload = {
            "title": story.title,
            "summary": truncate_words(story.summary, 120),
            "category": story.category,
            "source": story.primary_source.name,
            "facts": story.facts,
        }
        user_input = f"Input story facts:\n{json.dumps(payload, indent=2)}"

        result = generate_json(self.settings, prompt_sys, user_input)
        if not result or not result.get("body") or not result.get("image_title"):
            logger.info("Using template writer fallback for '%s'", story.title)
            return _fallback_writer(story, self.settings)

        body = normalize_space(str(result["body"]))
        image_title = normalize_space(str(result["image_title"]))
        image_facts = [str(f) for f in result.get("image_facts", [])]
        visual_prompt = str(result.get("visual_prompt", ""))
        alt_text = str(result.get("alt_text", image_title))
        tags = hashtags_for_category(story.category)

        return PostDraft(
            body=body,
            image_title=image_title,
            image_facts=image_facts,
            alt_text=alt_text,
            visual_prompt=visual_prompt,
            category=story.category,
            story=story,
            tags=tags,
        )

    def format_tweet_text(self, draft: PostDraft) -> str:
        """
        Assembles final tweet text within Twitter's 280-char limit:
        [Body text]

        Source: [Domain / Link]
        [Hashtags]
        """
        tags_str = " ".join(draft.tags[:2])
        source_line = ""

        if draft.story:
            if draft.story.is_showcase:
                source_line = f"Repo: {draft.story.item.url}"
            else:
                src_domain = domain(draft.story.item.url)
                source_line = f"Source: {draft.story.primary_source.name} ({src_domain})\n{draft.story.item.url}"

        # Combine
        parts = [draft.body]
        if source_line:
            parts.append(source_line)
        if tags_str:
            parts.append(tags_str)

        tweet = "\n\n".join(parts)

        # Enforce strict 280-char budget
        if twitter_length(tweet) > 280:
            # Drop source URL or trim body slightly
            overflow = twitter_length(tweet) - 275
            trimmed_body = draft.body[:-overflow].rstrip() + "..."
            parts[0] = trimmed_body
            tweet = "\n\n".join(parts)

        return tweet
