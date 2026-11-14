from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.analytics import load_analytics_state
from app.compliance import can_post_now
from app.config import Settings
from app.image import render_branded_image, validate_image_consistency
from app.models import PostDraft, Story
from app.quality import evaluate_quality_gate
from app.select import select_best_story
from app.sources import fetch_all_stories
from app.state import load_state, record_post, save_state
from app.twitter import TwitterClient
from app.verify import verify_draft_against_source
from app.writer import Writer

logger = logging.getLogger(__name__)


def _pick_balanced_format(recent_posts: list[dict]) -> str:
    """
    Selects next post format to achieve target distribution (Section 14):
    - 40% deep_dive
    - 25% showcase
    - 20% news
    - 10% discussion
    - 5% oss_discovery
    """
    targets = {
        "deep_dive": 0.40,
        "showcase": 0.25,
        "news": 0.20,
        "discussion": 0.10,
        "oss_discovery": 0.05,
    }

    if not recent_posts:
        return "deep_dive"

    # Count recent representation in the last 10 posts
    counts = {k: 0 for k in targets}
    for p in recent_posts[-10:]:
        kind = p.get("format_kind") or ("showcase" if p.get("category") == "showcase" else "news")
        if kind in counts:
            counts[kind] += 1

    total = max(1, sum(counts.values()))
    # Find format most under-represented compared to target
    under_represented = sorted(
        targets.keys(),
        key=lambda k: (counts[k] / total) - targets[k],
    )
    return under_represented[0]


def run_cycle(settings: Settings, live: bool = False, force: bool = False) -> bool:
    """
    Executes an end-to-end publishing cycle adhering to Sections 11–20:
    Source Discovery -> Article Extraction -> Fact Verification ->
    Content Generation -> Quality Gate -> Image Generation & Validation -> Publishing.
    """
    state = load_state(settings.state_dir)

    ok, reason = can_post_now(state, settings, force=force)
    if not ok:
        logger.info("Cycle skipped: %s", reason)
        return False

    # 1. Determine next balanced format kind
    recent_posts = state.get("posts", [])
    target_format = _pick_balanced_format(recent_posts)
    logger.info("Selected target format for cycle: %s", target_format)

    # 2. Fetch candidates & select qualified story informed by real analytics
    analytics = load_analytics_state(settings.state_dir)
    format_perf = analytics.get("format_performance")
    stories = fetch_all_stories(settings)
    best_story = select_best_story(stories, state, preferred_kind=target_format, analytics_perf=format_perf)

    if not best_story:
        # Fallback to any qualified candidate
        best_story = select_best_story(stories, state, analytics_perf=format_perf)

    if not best_story:
        logger.info("No eligible or verified stories found.")
        return False

    logger.info(
        "Selected story: '%s' (Format: %s, Category: %s)",
        best_story.title,
        best_story.source_kind,
        best_story.category,
    )

    # 3. Content Generation
    writer = Writer(settings)
    draft = writer.create_draft(best_story)
    tweet_text = writer.format_tweet_text(draft)

    # 4. Strict News Fact Verification (Section 12)
    if best_story.source_kind == "news":
        article_text = best_story.facts.get("article_text", "")
        verification = verify_draft_against_source(
            draft.body,
            best_story.title,
            article_text,
            settings,
        )
        if not verification.passed:
            logger.warning(
                "Fact verification failed for '%s': %s (Unsubstantiated: %s). Rejecting post.",
                best_story.title,
                verification.rejection_reason,
                verification.unsubstantiated_claims,
            )
            return False
        logger.info("Fact verification passed with confidence %.2f", verification.confidence_score)

    # 5. Quality Gate Evaluation (Section 18)
    qg_result = evaluate_quality_gate(
        draft,
        tweet_text,
        recent_posts,
        settings,
        has_image=draft.has_image,
    )
    if not qg_result.passed or qg_result.score < 8.0:
        logger.warning(
            "Quality Gate failed (Score: %.1f/10.0, Issues: %s). Holding back post.",
            qg_result.score,
            qg_result.issues,
        )
        return False

    draft.quality_score = qg_result.score
    logger.info("Quality Gate PASSED (Score: %.1f/10.0). Verdict: %s", qg_result.score, qg_result.verdict)

    # 6. Professional Visual Generation & Validation (Section 15)
    img_path = None
    if draft.has_image:
        ts = int(time.time())
        candidate_img_path = settings.output_dir / f"tweet_{ts}.jpg"
        img_path = render_branded_image(draft, settings, candidate_img_path)
        valid_img, img_reason = validate_image_consistency(draft, img_path)
        if not valid_img:
            logger.warning("Image failed consistency check (%s); falling back to text-only post.", img_reason)
            img_path = None

    if not live:
        logger.info("[DRY RUN] Approved post passed all gates:")
        logger.info("\n%s\n", tweet_text)
        if img_path:
            logger.info("With visual: %s", img_path)
        else:
            logger.info("Format: Clean text-only post")
        return True

    # 7. Publishing to Twitter/X
    client = TwitterClient(settings)
    media_ids = []
    if img_path:
        media_id = client.upload_media(img_path, alt_text=draft.alt_text)
        media_ids.append(media_id)

    result = client.post_tweet(text=tweet_text, media_ids=media_ids)

    # 8. Record State
    record_post(
        state,
        best_story,
        result.tweet_id,
        tweet_text,
        str(img_path) if img_path else "",
        format_kind=best_story.source_kind,
        quality_score=qg_result.score,
    )
    save_state(settings.state_dir, state)

    logger.info("🚀 Posted successfully! Tweet URL: https://x.com/i/status/%s", result.tweet_id)
    return True


def run_loop(settings: Settings, live: bool = False) -> None:
    """Runs continuous scheduler loop."""
    logger.info("Starting automation daemon (Checking every 15 minutes)...")
    while True:
        try:
            run_cycle(settings, live=live)
        except Exception as exc:
            logger.error("Error in scheduler loop: %s", exc, exc_info=True)
        time.sleep(15 * 60)
