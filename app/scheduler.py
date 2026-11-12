from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.compliance import can_post_now
from app.config import Settings
from app.image import render_branded_image
from app.models import Story
from app.select import select_best_story
from app.sources import fetch_all_stories
from app.state import load_state, record_post, save_state
from app.twitter import TwitterClient
from app.writer import Writer

logger = logging.getLogger(__name__)


def run_cycle(settings: Settings, live: bool = False, force: bool = False) -> bool:
    """Executes a single check-and-post automation cycle."""
    state = load_state(settings.state_dir)

    ok, reason = can_post_now(state, settings, force=force)
    if not ok:
        logger.info("Cycle skipped: %s", reason)
        return False

    # Determine whether this cycle is a personal project showcase
    import random
    do_showcase = random.random() < settings.showcase_ratio

    stories = fetch_all_stories(settings, include_showcase=do_showcase)
    best_story = select_best_story(stories, state) if not do_showcase else (stories[0] if stories else None)

    if not best_story:
        logger.info("No fresh or qualified stories found.")
        return False

    logger.info("Selected story: %s (Category: %s)", best_story.title, best_story.category)

    # Generate draft
    writer = Writer(settings)
    draft = writer.create_draft(best_story)
    tweet_text = writer.format_tweet_text(draft)

    # Render branded image
    img_name = f"tweet_{int(time.time())}.jpg"
    img_path = settings.output_dir / img_name
    render_branded_image(draft, settings, img_path)
    logger.info("Generated image at %s", img_path)

    if not live:
        logger.info("[DRY RUN] Would post the following tweet:\n\n%s\n", tweet_text)
        return True

    # Publish live to Twitter
    client = TwitterClient(settings)
    with open(img_path, "rb") as f:
        img_bytes = f.read()

    media_id = client.upload_media(img_bytes, alt_text=draft.alt_text)
    result = client.post_tweet(text=tweet_text, media_ids=[media_id])

    # Record state
    record_post(state, best_story, result.tweet_id, tweet_text, str(img_path))
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
