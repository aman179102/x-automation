from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from app.compliance import can_post_now
from app.config import Settings, load_settings
from app.image import render_branded_image, validate_image_consistency
from app.models import PostDraft, Story, StoryItem
from app.quality import evaluate_quality_gate
from app.scheduler import _pick_balanced_format, run_cycle, run_loop
from app.select import rank_candidates, select_best_story
from app.sources import fetch_all_stories
from app.state import load_state, record_post, save_state
from app.text import twitter_length
from app.twitter import TwitterClient, TwitterError
from app.verify import verify_draft_against_source
from app.writer import Writer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("x-automation")


def _cli_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="X (Twitter) Professional Publishing Engine for Aman Kumar (@aman179102)."
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help="Publish directly to X (Twitter)")
    mode.add_argument("--dry-run", action="store_true", help="Generate post & image without publishing (default)")

    p.add_argument("--check-account", action="store_true", help="Verify Twitter credentials and account details")
    p.add_argument("--plan", action="store_true", help="List ranked candidates across diverse formats with scores")
    p.add_argument("--showcase", action="store_true", help="Force post an open-source project showcase")
    p.add_argument("--deep-dive", action="store_true", help="Force post an engineering deep dive")
    p.add_argument("--discussion", action="store_true", help="Force post a technical discussion question")
    p.add_argument("--test-image", action="store_true", help="Test technical graphic generation with brand card overlay")
    p.add_argument("--engagement-plan", action="store_true", help="Find relevant developer tweets & draft smart replies")
    p.add_argument("--engagement-live", action="store_true", help="Publish a smart reply to a top tech tweet")
    p.add_argument("--sync-analytics", action="store_true", help="Sync real post metrics and calculate format performance")
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

    # Sync Analytics mode
    if getattr(args, "sync_analytics", False):
        import json
        from app.analytics import sync_recent_analytics
        print("\n📈 Syncing verified post metrics from X...")
        res = sync_recent_analytics(settings)
        print("✅ Analytics sync complete:")
        print(json.dumps(res.get("format_performance", {}), indent=2))
        return 0

    # 2. Test image generation mode
    if args.test_image:
        print("\n🎨 Testing Technical Architecture Card Generator...")
        sample_draft = PostDraft(
            body="Testing visual generator for X automation pipeline.",
            image_title="LSM-Trees vs B+ Trees",
            image_facts=["Sequential Disk I/O vs Random Writes"],
            alt_text="Architecture diagram comparing LSM-Trees and B-Trees in storage engines",
            visual_prompt="minimal dark slate technical architecture diagram high contrast",
            category="database",
            format_kind="deep_dive",
            technical_takeaway="Trades compaction read overhead for zero random write penalties.",
        )
        out_file = settings.output_dir / "test_brand_card.jpg"
        render_branded_image(sample_draft, settings, out_file)
        print(f"✅ Test card generated successfully: {out_file}\n")
        return 0

    # 3. Plan mode: list ranked candidates across diverse formats
    if args.plan:
        print("\n📊 Fetching feeds & candidates across diverse formats...")
        state = load_state(settings.state_dir)
        stories = fetch_all_stories(settings)
        ranked = rank_candidates(stories, state)
        recent_posts = state.get("posts", [])
        recommended_format = _pick_balanced_format(recent_posts)

        print(f"\nNext Recommended Format to maintain 40/25/20/10/5 mix: [{recommended_format.upper()}]")
        print(f"Found {len(ranked)} eligible candidates:\n" + "-" * 75)
        for idx, (story, score) in enumerate(ranked[:12], 1):
            kind_tag = story.source_kind.upper()
            cat_tag = story.category.upper()
            print(f"{idx:2d}. [{kind_tag:12s} | {cat_tag:10s}] (Score: {score:.2f}) {story.title}")
            print(f"    Source: {story.primary_source.name} | {story.item.url}")
        print("-" * 75 + "\n")
        return 0

    # 4. Engagement modes (Smart Commenting)
    if args.engagement_plan or args.engagement_live:
        import asyncio
        from datetime import datetime, timezone
        from app.engagement import (
            can_reply_now,
            draft_reply,
            find_engagement_candidates,
            load_engagement_state,
            post_live_reply,
            save_engagement_state,
            validate_reply_quality,
        )

        eng_state = load_engagement_state(settings.state_dir)
        if not args.force:
            ok, reason = can_reply_now(eng_state, settings)
            if not ok and args.engagement_live:
                print(f"⏸️ Cannot reply right now: {reason}")
                return 0

        print("\n🔍 Scanning X for relevant high-signal engineering discussions...")
        candidates = find_engagement_candidates(settings)
        if not candidates:
            print("ℹ️ No eligible engagement candidates found right now.")
            return 0

        print(f"Found {len(candidates)} high-potential tweets for technical engagement:\n" + "=" * 70)
        valid_candidates = []
        for idx, c in enumerate(candidates, 1):
            print(f"\n[{idx}] Tweet by @{c.author} ({c.likes} likes, Score: {c.score}) | ID: {c.tweet_id}")
            print(f"    Post: \"{c.text[:130]}...\"")
            reply, reason = draft_reply(c.text, settings, author=c.author)
            if reply:
                is_valid, val_reason = validate_reply_quality(c, reply, eng_state)
                if is_valid:
                    c.suggested_reply = reply
                    c.reasoning = reason
                    valid_candidates.append(c)
                    print(f"    💬 Drafted Reply: \"{reply}\"")
                    print(f"    💡 Strategic Angle: {reason}")
                else:
                    print(f"    ⚠️ Reply rejected by quality validator: {val_reason}")
            else:
                print(f"    ⚠️ Reply skipped: {reason}")
        print("\n" + "=" * 70)

        if not args.engagement_live:
            print("💡 [ENGAGEMENT PLAN COMPLETE] Run with '--engagement-live' to post the top reply.\n")
            return 0

        if not valid_candidates:
            print("❌ No reply passed the quality and safety validation gates. Skipping reply (fail-closed).\n")
            return 0

        target = valid_candidates[0]
        print(f"\n🚀 Posting technical reply to @{target.author}'s tweet ({target.tweet_id})...")
        try:
            reply_id = asyncio.run(post_live_reply(target, target.suggested_reply, settings))
            now_iso = datetime.now(timezone.utc).isoformat()
            eng_state.setdefault("replies", []).append({
                "reply_tweet_id": reply_id,
                "target_tweet_id": target.tweet_id,
                "target_author": target.author,
                "reply_text": target.suggested_reply,
                "replied_at": now_iso,
            })
            eng_state["last_replied_at"] = now_iso
            save_engagement_state(settings.state_dir, eng_state)
            print(f"🎉 Successfully posted reply! URL: https://x.com/{settings.author_handle.strip('@')}/status/{reply_id}\n")
            return 0
        except Exception as exc:
            print(f"❌ Failed to post reply: {exc}\n")
            return 1

    # 5. Continuous daemon mode
    if args.daemon:
        run_loop(settings, live=args.live)
        return 0

    # 6. Single cycle (dry-run or live) with full Quality Gate & Verification
    state = load_state(settings.state_dir)
    ok, reason = can_post_now(state, settings, force=args.force)
    if not ok and args.live:
        print(f"⏸️ Cannot post right now: {reason}")
        print("   (Use --force to bypass time windows for testing)")
        return 0

    # Select story
    preferred_kind = None
    if args.showcase:
        preferred_kind = "showcase"
    elif getattr(args, "deep_dive", False):
        preferred_kind = "deep_dive"
    elif getattr(args, "discussion", False):
        preferred_kind = "discussion"
    else:
        preferred_kind = _pick_balanced_format(state.get("posts", []))

    print(f"🎯 Target Format: [{preferred_kind.upper()}]")
    print("📡 Fetching stories & evaluating sources...")
    stories = fetch_all_stories(settings)
    best_story = select_best_story(stories, state, preferred_kind=preferred_kind)

    if not best_story:
        best_story = select_best_story(stories, state)

    if not best_story:
        print("ℹ️ No new eligible stories found.")
        return 0

    print(f"📌 Selected Candidate: {best_story.title}")
    print(f"   Format: {best_story.source_kind.upper()} | Category: {best_story.category.upper()} | Source: {best_story.primary_source.name}")

    # Generate draft
    print("✍️ Generating technical draft...")
    writer = Writer(settings)
    draft = writer.create_draft(best_story)
    tweet_text = writer.format_tweet_text(draft)
    eff_len = twitter_length(tweet_text)

    # Fact verification for news
    if best_story.source_kind == "news":
        print("🔍 Verifying facts against original extracted article...")
        article_text = best_story.facts.get("article_text", "")
        verification = verify_draft_against_source(draft.body, best_story.title, article_text, settings)
        if not verification.passed:
            print(f"❌ REJECTED BY FACT-CHECKER: {verification.rejection_reason}")
            if verification.unsubstantiated_claims:
                print(f"   Unsubstantiated: {verification.unsubstantiated_claims}")
            return 1
        print(f"✅ Facts Verified! (Confidence: {verification.confidence_score:.2f})")

    # Quality Gate Evaluation
    print("⚖️ Evaluating Publishing Quality Gate (Sections 11-20)...")
    qg = evaluate_quality_gate(draft, tweet_text, state.get("posts", []), settings, has_image=draft.has_image)
    print(f"   Quality Score: {qg.score:.1f} / 10.0 | Passed: {'YES' if qg.passed else 'NO'}")
    if qg.issues:
        print(f"   Gate Feedback: {qg.issues}")
    if not qg.passed or qg.score < 8.0:
        print(f"❌ Post held back by Quality Gate: {qg.verdict}")
        return 1

    # Render branded visual (or clean text-only)
    img_path = None
    if draft.has_image:
        ts = int(time.time())
        candidate_img = settings.output_dir / f"tweet_{ts}.jpg"
        print("🖼️ Generating professional technical graphic...")
        img_path = render_branded_image(draft, settings, candidate_img)
        valid_img, img_reason = validate_image_consistency(draft, img_path)
        if not valid_img:
            print(f"⚠️ Visual failed consistency check ({img_reason}). Switching to clean text-only post.")
            img_path = None

    # Display preview
    print("\n" + "=" * 65)
    print("📱 PREVIEW TWEET")
    print("=" * 65)
    print(tweet_text)
    print("=" * 65)
    print(f"Character Count:  {eff_len} / 280 (URLs counted as 23)")
    print(f"Format Kind:      {draft.format_kind.upper()}")
    print(f"Quality Score:    {qg.score:.1f} / 10.0 (Gate: PASSED)")
    if img_path:
        print(f"Image Output:     {img_path}")
        print(f"Image Alt Text:   {draft.alt_text}")
    else:
        print("Image Output:     [TEXT-ONLY POST - No forced graphic]")
    print(f"Author Branding:  {settings.author_name} ({settings.author_handle})")
    print("=" * 65 + "\n")

    if not args.live:
        print("💡 [DRY-RUN COMPLETE] Run with '--live' to publish directly to X.")
        return 0

    # Post live
    print("🚀 Publishing to X (Twitter)...")
    try:
        client = TwitterClient(settings)
        media_ids = []
        if img_path:
            print("📤 Uploading media...")
            media_id = client.upload_media(img_path, alt_text=draft.alt_text)
            media_ids.append(media_id)

        print("🐦 Posting tweet...")
        result = client.post_tweet(text=tweet_text, media_ids=media_ids)

        # Save state
        record_post(
            state,
            best_story,
            result.tweet_id,
            tweet_text,
            str(img_path) if img_path else "",
            format_kind=best_story.source_kind,
            quality_score=qg.score,
        )
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
