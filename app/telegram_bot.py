from __future__ import annotations

import html
import json
import logging
import re
import urllib.parse
from pathlib import Path
from typing import Optional

import httpx

from app.config import Settings
from app.models import PostDraft

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"


def build_telegram_keyboard(
    tweet_text: str,
    primary_source_url: str = "",
    portfolio_url: str = "https://github.com/aman179102",
) -> dict:
    """
    Builds the inline keyboard containing the 1-Tap mobile web intent URL.
    Uses modern https://x.com/intent/post with explicit &hashtags= parameter separation
    so mobile browsers and Twitter app do not truncate at the '#' fragment.
    """
    hashtags = re.findall(r"#(\w+)", tweet_text)
    clean_text = re.sub(r"#\w+", "", tweet_text).strip()

    params: dict[str, str] = {}
    if clean_text:
        params["text"] = clean_text
    if hashtags:
        params["hashtags"] = ",".join(hashtags)

    intent_url = f"https://x.com/intent/post?{urllib.parse.urlencode(params)}"

    keyboard_rows = [
        [
            {"text": "🚀 1-Tap Post on X (Mobile App)", "url": intent_url}
        ]
    ]

    second_row = []
    if primary_source_url and primary_source_url.startswith("http"):
        second_row.append({"text": "🌐 Original Source", "url": primary_source_url})
    if portfolio_url and portfolio_url.startswith("http"):
        second_row.append({"text": "🐙 Aman's GitHub", "url": portfolio_url})

    if second_row:
        keyboard_rows.append(second_row)

    return {"inline_keyboard": keyboard_rows}


def format_telegram_caption(
    draft: PostDraft,
    tweet_text: str,
    quality_score: float = 8.5,
) -> str:
    """
    Formats an HTML caption for Telegram (strictly within Telegram's 1024-char photo caption limit).
    """
    escaped_tweet = html.escape(tweet_text)
    format_name = html.escape(draft.format_kind.upper())

    caption = (
        f"⚡ <b>NEW X DRAFT READY</b> [{format_name}]\n"
        f"📊 <b>Quality Gate:</b> {quality_score:.1f}/10.0 (PASSED ✅)\n\n"
        f"<code>{escaped_tweet}</code>\n\n"
        f"📲 <i>Tap the button below to open official X app and publish with 0% ban risk!</i>"
    )
    if len(caption) > 1024:
        # Fallback to compact caption if body is too long
        caption = (
            f"⚡ <b>NEW X DRAFT READY</b> [{format_name}]\n"
            f"📊 <b>Score:</b> {quality_score:.1f}/10.0 (PASSED)\n\n"
            f"<code>{escaped_tweet[:800]}...</code>\n\n"
            f"📲 <i>Tap button below to post in 1 tap!</i>"
        )
    return caption


def format_full_message(
    draft: PostDraft,
    tweet_text: str,
    quality_score: float = 8.5,
    primary_source_url: str = "",
) -> str:
    """
    Full HTML message for Telegram text dispatches.
    """
    escaped_tweet = html.escape(tweet_text)
    format_name = html.escape(draft.format_kind.upper())

    msg = (
        f"🛡️ <b>[100% BAN-PROOF CO-PILOT] High-IQ Draft Ready</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>Format:</b> {format_name}\n"
        f"⭐ <b>Quality Score:</b> {quality_score:.1f} / 10.0 (Verified)\n"
    )
    if primary_source_url:
        msg += f"🔗 <b>Source:</b> {html.escape(primary_source_url)}\n"

    msg += (
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Prepared Post (tap text to copy):</b>\n"
        f"<code>{escaped_tweet}</code>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🚀 <b>Posting Method:</b>\n"
        f"Click the <b>[🚀 1-Tap Post on X]</b> button below.\n"
        f"It opens your native X app on your Android phone using your real device & residential connection. "
        f"No datacenter IP, no session cookies used — 100% immune to Twitter locks!"
    )
    return msg


def send_telegram_draft(
    draft: PostDraft,
    tweet_text: str,
    img_path: Optional[Path],
    settings: Settings,
    quality_score: float = 8.5,
    primary_source_url: str = "",
) -> bool:
    """
    Dispatches the generated post draft, branded visual card, and 1-tap mobile button to Aman's Telegram.
    """
    bot_token = settings.telegram_bot_token.strip()
    chat_id = settings.telegram_chat_id.strip()

    if not bot_token or not chat_id:
        logger.error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing from settings.")
        print("❌ Cannot send to Telegram: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is not configured in .env.")
        print("   Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to enable 1-Tap Mobile Co-Pilot.")
        return False

    keyboard = build_telegram_keyboard(
        tweet_text=tweet_text,
        primary_source_url=primary_source_url,
        portfolio_url=settings.portfolio_url,
    )
    markup_json = json.dumps(keyboard)

    # 1. Send with Photo if image exists
    if img_path and Path(img_path).is_file():
        caption = format_telegram_caption(draft, tweet_text, quality_score)
        photo_url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendPhoto"
        try:
            with open(img_path, "rb") as photo_file:
                files = {"photo": (Path(img_path).name, photo_file, "image/jpeg")}
                data = {
                    "chat_id": chat_id,
                    "caption": caption,
                    "parse_mode": "HTML",
                    "reply_markup": markup_json,
                }
                with httpx.Client(timeout=25.0) as client:
                    resp = client.post(photo_url, data=data, files=files)
                    if resp.status_code == 200:
                        logger.info("Successfully sent visual card draft to Telegram!")
                        return True
                    else:
                        logger.warning("Telegram sendPhoto returned %d: %s. Falling back to sendMessage.", resp.status_code, resp.text)
        except Exception as exc:
            logger.warning("Failed to send image to Telegram (%s). Falling back to text.", exc)

    # 2. Text message fallback or text-only posts
    message_text = format_full_message(draft, tweet_text, quality_score, primary_source_url)
    msg_url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message_text,
        "parse_mode": "HTML",
        "reply_markup": markup_json,
        "disable_web_page_preview": False,
    }

    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.post(msg_url, json=payload)
            if resp.status_code == 200:
                logger.info("Successfully sent draft to Telegram chat!")
                return True
            else:
                logger.error("Telegram sendMessage failed (%d): %s", resp.status_code, resp.text)
                print(f"❌ Telegram API Error ({resp.status_code}): {resp.text}")
                return False
    except Exception as exc:
        logger.error("Telegram dispatch error: %s", exc)
        print(f"❌ Failed to reach Telegram API: {exc}")
        return False


def test_telegram_ping(settings: Settings) -> bool:
    """
    Sends a test ping to verify Telegram Bot configuration.
    """
    bot_token = settings.telegram_bot_token.strip()
    chat_id = settings.telegram_chat_id.strip()

    if not bot_token or not chat_id:
        print("❌ Cannot test Telegram: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing in .env.")
        return False

    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": (
            "🔔 <b>[X-Automation Co-Pilot Connected]</b>\n\n"
            f"Hello <b>{html.escape(settings.author_name)}</b> ({html.escape(settings.author_handle)})!\n\n"
            "Your Telegram 1-Tap Mobile Co-Pilot is active. "
            "Whenever a new high-IQ technical draft and branded card are generated, "
            "you will receive it right here with a 1-tap mobile button to post safely from your phone!"
        ),
        "parse_mode": "HTML",
    }
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(url, json=payload)
            if resp.status_code == 200:
                print("✅ Telegram connection verified! Test ping sent successfully to your Telegram chat.")
                return True
            else:
                print(f"❌ Telegram API returned error {resp.status_code}: {resp.text}")
                return False
    except Exception as exc:
        print(f"❌ Connection failed: {exc}")
        return False


def trigger_telegram_post(
    settings: Settings,
    preferred_kind: Optional[str] = None,
    force: bool = True,
) -> bool:
    """
    Executes a full research, verification, quality gate, card rendering, and dispatch cycle to Telegram.
    """
    import time
    from app.compliance import can_post_now
    from app.image import render_branded_image, validate_image_consistency
    from app.quality import evaluate_quality_gate
    from app.scheduler import _pick_balanced_format
    from app.select import select_best_story
    from app.sources import fetch_all_stories
    from app.state import load_state, record_post, save_state
    from app.verify import verify_draft_against_source
    from app.writer import Writer

    state = load_state(settings.state_dir)
    if not force:
        ok, reason = can_post_now(state, settings, force=False)
        if not ok:
            logger.info("Posting window or limits active: %s", reason)
            return False

    kind = preferred_kind or _pick_balanced_format(state.get("posts", []))
    stories = fetch_all_stories(settings)
    best_story = select_best_story(stories, state, preferred_kind=kind)
    if not best_story:
        best_story = select_best_story(stories, state)
    if not best_story:
        logger.warning("No eligible stories found.")
        return False

    writer = Writer(settings)
    draft = writer.create_draft(best_story)
    tweet_text = writer.format_tweet_text(draft)

    # Fact verification for news
    if best_story.source_kind == "news":
        article_text = best_story.facts.get("article_text", "")
        verification = verify_draft_against_source(draft.body, best_story.title, article_text, settings)
        if not verification.passed:
            logger.warning("Fact check rejected: %s", verification.rejection_reason)
            return False

    qg = evaluate_quality_gate(draft, tweet_text, state.get("posts", []), settings, has_image=draft.has_image)
    if not qg.passed or qg.score < 8.0:
        logger.warning("Quality gate rejected (score=%.1f): %s", qg.score, qg.verdict)
        return False

    img_path = None
    if draft.has_image:
        ts = int(time.time())
        candidate_img = settings.output_dir / f"tweet_{ts}.jpg"
        img_path = render_branded_image(draft, settings, candidate_img)
        valid_img, _ = validate_image_consistency(draft, img_path)
        if not valid_img:
            img_path = None

    source_url = getattr(best_story.item, "url", "") if best_story and best_story.item else ""
    ok = send_telegram_draft(
        draft=draft,
        tweet_text=tweet_text,
        img_path=img_path,
        settings=settings,
        quality_score=qg.score,
        primary_source_url=source_url,
    )
    if ok:
        record_post(
            state,
            best_story,
            "telegram_dispatch",
            tweet_text,
            str(img_path) if img_path else "",
            format_kind=best_story.source_kind,
            quality_score=qg.score,
        )
        save_state(settings.state_dir, state)
        return True
    return False


def run_telegram_listener(settings: Settings) -> None:
    """
    Runs a continuous Telegram polling listener so Aman can generate drafts
    by sending /new, /showcase, /deepdive, or /discussion directly inside Telegram!
    """
    import time
    bot_token = settings.telegram_bot_token.strip()
    if not bot_token:
        print("❌ TELEGRAM_BOT_TOKEN is missing in .env.")
        return

    print("\n🤖 [TELEGRAM INTERACTIVE BOT RUNNING]")
    print(f"   Connected to @x_akk_bot (Chat ID: {settings.telegram_chat_id})")
    print("   Available Telegram commands in chat:")
    print("   • /new        - Generate new balanced draft & visual card")
    print("   • /showcase   - Feature personal open-source project")
    print("   • /deepdive   - Systems architecture deep dive")
    print("   • /discussion - High-signal developer discussion")
    print("   • /ping       - Check bot status")
    print("\n   Listening for incoming messages... (Press Ctrl+C to stop)\n")

    offset = 0
    # Clear old pending updates
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(f"{TELEGRAM_API_BASE}/bot{bot_token}/getUpdates?offset=-1")
            if resp.status_code == 200:
                data = resp.json().get("result", [])
                if data:
                    offset = data[-1]["update_id"] + 1
    except Exception:
        pass

    while True:
        try:
            with httpx.Client(timeout=35.0) as client:
                resp = client.get(f"{TELEGRAM_API_BASE}/bot{bot_token}/getUpdates?offset={offset}&timeout=25")
                if resp.status_code != 200:
                    time.sleep(2)
                    continue

                updates = resp.json().get("result", [])
                for u in updates:
                    offset = u["update_id"] + 1
                    msg = u.get("message") or u.get("edited_message")
                    if not msg:
                        continue
                    text = (msg.get("text") or "").strip().lower()
                    chat_id = str(msg.get("chat", {}).get("id", ""))

                    if not text:
                        continue

                    if text in ("/start", "/help"):
                        help_text = (
                            "👋 <b>Welcome to Aman's X-Automation Co-Pilot!</b>\n\n"
                            "Commands you can send me anytime:\n"
                            "• <b>/new</b> - Generate a fresh high-IQ post & visual card\n"
                            "• <b>/showcase</b> - Feature your projects (Storage, Analytics, PodVoice)\n"
                            "• <b>/deepdive</b> - Systems & Architecture deep dive\n"
                            "• <b>/discussion</b> - Engineering discussion question\n"
                            "• <b>/ping</b> - Check connection\n\n"
                            "⚡ Every post arrives with a 1-Tap button to publish from your phone with 0% ban risk!"
                        )
                        client.post(f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage", json={
                            "chat_id": chat_id,
                            "text": help_text,
                            "parse_mode": "HTML",
                        })
                    elif text == "/ping":
                        client.post(f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage", json={
                            "chat_id": chat_id,
                            "text": "🟢 Co-Pilot is online, healthy, and ready!",
                        })
                    elif text in ("/new", "/draft", "/post"):
                        client.post(f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage", json={
                            "chat_id": chat_id,
                            "text": "⚙️ <i>Researching latest engineering developments & generating draft...</i>",
                            "parse_mode": "HTML",
                        })
                        trigger_telegram_post(settings, force=True)
                    elif text in ("/showcase", "/project"):
                        client.post(f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage", json={
                            "chat_id": chat_id,
                            "text": "⚙️ <i>Preparing project showcase for Aman's open-source architecture...</i>",
                            "parse_mode": "HTML",
                        })
                        trigger_telegram_post(settings, preferred_kind="showcase", force=True)
                    elif text in ("/deepdive", "/deep_dive"):
                        client.post(f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage", json={
                            "chat_id": chat_id,
                            "text": "⚙️ <i>Formulating systems architecture deep dive...</i>",
                            "parse_mode": "HTML",
                        })
                        trigger_telegram_post(settings, preferred_kind="deep_dive", force=True)
                    elif text in ("/discussion", "/question"):
                        client.post(f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage", json={
                            "chat_id": chat_id,
                            "text": "⚙️ <i>Crafting high-signal developer discussion dilemma...</i>",
                            "parse_mode": "HTML",
                        })
                        trigger_telegram_post(settings, preferred_kind="discussion", force=True)

        except KeyboardInterrupt:
            print("\n👋 Telegram Bot listener stopped.")
            break
        except Exception as exc:
            logger.warning("Listener loop encountered error: %s", exc)
            time.sleep(3)

