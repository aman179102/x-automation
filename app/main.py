from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from app.compliance import can_post_now
from app.config import Settings, load_settings
from app.image import render_branded_image
from app.models import PostDraft, Story, StoryItem
from app.scheduler import run_cycle, run_loop
from app.select import rank_candidates, select_best_story
from app.sources import PERSONAL_PROJECTS, fetch_all_stories
from app.state import load_state, record_post, save_state
from app.text import twitter_length
from app.twitter import TwitterClient, TwitterError
from app.writer import Writer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("x-automation")


def _cli_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="X (Twitter) Automation Engine: Post curated tech insights & developer showcases."
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help="Publish directly to X (Twitter)")
    mode.add_argument("--dry-run", action="store_true", help="Generate post & image without publishing (default)")

    p.add_argument("--check-account", action="store_true", help="Verify Twitter API keys and account details")
    p.add_argument("--plan", action="store_true", help="List ranked story candidates and virality scores")
    p.add_argument("--showcase", action="store_true", help="Force post an open-source project showcase")
    p.add_argument("--test-image", action="store_true", help="Test AI/stock image generation with brand card overlay")
    p.add_argument("--force", action="store_true", help="Bypass posting windows and daily limits")
    p.add_argument("--daemon", action="store_true", help="Run continuously in background mode")
    return p


def main() -> int:
    parser = _cli_parser()
    args = parser.parse_args()

    settings = load_settings()

    # 1. Check account mode
    if args.check_account:
        print("\n🔍 Checking Twitter API v2 Credentials...")
        try:
            client = TwitterClient(settings)
            user_info = client.verify_credentials()
            print(f"✅ Success! Connected as: @{user_info.get('username')} ({user_info.get('name')})")
            print(f"   User ID: {user_info.get('id')}")
            return 0
        except TwitterError as exc:
            print(f"❌ Verification failed: {exc}")
            return 1

    # 2. Test image generation mode
    if args.test_image:
        print("\n🎨 Testing Dynamic Image Generator with Personal Branding...")
        sample_draft = PostDraft(
            body="Testing visual generator for X automation pipeline.",
            image_title="CPU Cache Timing Exploit in Isolated MicroVMs",
            image_facts=["CVSS 9.4", "Zero Network Bypass"],
            alt_text="Cybersecurity breakdown: CPU cache side-channel exploit in isolated microVMs",
            visual_prompt="futuristic cyberpunk microchip hardware glowing circuits dark blue amber octane render",
            category="security",
        )
        out_file = settings.output_dir / "test_brand_card.jpg"
        render_branded_image(sample_draft, settings, out_file)
        print(f"✅ Test card generated successfully: {out_file}\n")
        return 0

    # 3. Plan mode: list ranked candidates
    if args.plan:
        print("\n📊 Fetching feeds and ranking candidates...")
        state = load_state(settings.state_dir)
        stories = fetch_all_stories(settings)
        ranked = rank_candidates(stories, state)
        print(f"\nFound {len(ranked)} eligible candidates:\n" + "-" * 70)
        for idx, (story, score) in enumerate(ranked[:10], 1):
            print(f"{idx:2d}. [{story.category.upper():8s}] (Score: {score:.2f}) {story.title}")
            print(f"    Source: {story.primary_source.name} | {story.item.url}")
        print("-" * 70)
        return 0

    # 4. Continuous daemon mode
    if args.daemon:
        run_loop(settings, live=args.live)
        return 0

    # 5. Single cycle (dry-run or live)
    state = load_state(settings.state_dir)
    ok, reason = can_post_now(state, settings, force=args.force)
    if not ok and args.live:
        print(f"⏸️ Cannot post right now: {reason}")
        print("   (Use --force to bypass time windows for testing)")
        return 0

    # Select story (or showcase if requested)
    if args.showcase:
        print("🚀 Selecting open-source project showcase...")
        stories = fetch_all_stories(settings, include_showcase=True)
        best_story = stories[0]
    else:
        print("📡 Fetching latest tech stories...")
        stories = fetch_all_stories(settings)
        best_story = select_best_story(stories, state)

    if not best_story:
        print("ℹ️ No new eligible stories found.")
        return 0

    print(f"📌 Selected Story: {best_story.title}")
    print(f"   Category: {best_story.category.upper()} | Source: {best_story.primary_source.name}")

    # Generate draft
    print("✍️ Drafting tweet (English)...")
    writer = Writer(settings)
    draft = writer.create_draft(best_story)
    tweet_text = writer.format_tweet_text(draft)
    eff_len = twitter_length(tweet_text)

    # Render branded visual
    ts = int(time.time())
    img_path = settings.output_dir / f"tweet_{ts}.jpg"
    print(f"🖼️ Generating branded visual ({settings.image_mode} mode)...")
    render_branded_image(draft, settings, img_path)

    # Display preview
    print("\n" + "=" * 60)
    print("📱 PREVIEW TWEET")
    print("=" * 60)
    print(tweet_text)
    print("=" * 60)
    print(f"Character Count: {eff_len} / 280 (URLs counted as 23)")
    print(f"Image Output:    {img_path}")
    print(f"Image Alt Text:  {draft.alt_text}")
    print(f"Branding Banner: Curated by {settings.author_name} ({settings.author_handle})")
    print("=" * 60 + "\n")

    if not args.live:
        print("💡 [DRY-RUN COMPLETE] Run with '--live' to publish to Twitter/X.")
        return 0

    # Post live
    print("🚀 Publishing to X (Twitter)...")
    try:
        client = TwitterClient(settings)
        with open(img_path, "rb") as f:
            img_bytes = f.read()

        print("📤 Uploading media...")
        media_id = client.upload_media(img_bytes, alt_text=draft.alt_text)

        print("🐦 Posting tweet...")
        result = client.post_tweet(text=tweet_text, media_ids=[media_id])

        # Save state
        record_post(state, best_story, result.tweet_id, tweet_text, str(img_path))
        save_state(settings.state_dir, state)

        print(f"\n🎉 Successfully published! Tweet URL:")
        print(f"👉 https://x.com/i/status/{result.tweet_id}\n")
        return 0
    except TwitterError as exc:
        print(f"\n❌ Error publishing tweet: {exc}\n")
        return 1


def cli():
    sys.exit(main())


if __name__ == "__main__":
    cli()
