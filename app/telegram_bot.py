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


def build_job_main_menu() -> tuple[str, dict]:
    """
    Builds the top-level interactive popup with Paid and Unpaid options.
    """
    text = (
        "💼 <b>SELECT REMOTE WORK &amp; COLLABORATION</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Aapko kis tarah ki <b>100% Remote</b> opportunity hunt karni hai?\n\n"
        "Option select karein:"
    )
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "💰 Paid Opportunities (Intern, Gig, Contract...)", "callback_data": "job_menu_paid"}
            ],
            [
                {"text": "🆓 Free / Unpaid / Collab (Intern, OSS, Equity...)", "callback_data": "job_menu_unpaid"}
            ],
            [
                {"text": "🌐 Any Remote Work (Surprise Me)", "callback_data": "job_search_any_all"}
            ]
        ]
    }
    return text, keyboard


def build_job_paid_menu() -> tuple[str, dict]:
    """
    Builds the sub-menu for Paid remote opportunities.
    """
    text = (
        "💰 <b>PAID REMOTE OPPORTUNITIES</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Kaunsa paid remote work search karna hai?\n\n"
        "Select your preferred paid role:"
    )
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "🎓 Paid Intern", "callback_data": "job_search_paid_intern"},
                {"text": "⚡ Quick Gig / MVP", "callback_data": "job_search_gig_mvp"},
            ],
            [
                {"text": "💼 Freelance Project", "callback_data": "job_search_freelance"},
                {"text": "📜 Contract Role", "callback_data": "job_search_contract"},
            ],
            [
                {"text": "🏢 Full-Time Remote", "callback_data": "job_search_fulltime"},
                {"text": "⏱️ Part-Time Remote", "callback_data": "job_search_parttime"},
            ],
            [
                {"text": "🪙 Code Bounties", "callback_data": "job_search_bounty"},
                {"text": "🎲 Any Paid Work", "callback_data": "job_search_any_paid"},
            ],
            [
                {"text": "🔙 Back to Main Menu", "callback_data": "job_menu_main"},
            ]
        ]
    }
    return text, keyboard


def build_job_unpaid_menu() -> tuple[str, dict]:
    """
    Builds the sub-menu for Free / Unpaid / Collaboration remote opportunities.
    """
    text = (
        "🆓 <b>FREE &amp; UNPAID COLLABORATIONS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Kaunsi remote collaboration ya experience search karni hai?\n\n"
        "Select your preferred category:"
    )
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "🆓 Unpaid Intern", "callback_data": "job_search_unpaid_intern"},
                {"text": "🤝 Open Source Collab", "callback_data": "job_search_opensource"},
            ],
            [
                {"text": "🚀 Co-Founder / Equity", "callback_data": "job_search_cofounder"},
                {"text": "🏆 Hackathon Teammate", "callback_data": "job_search_hackathon"},
            ],
            [
                {"text": "🎗️ Volunteer Dev / NGO", "callback_data": "job_search_volunteer"},
                {"text": "🎲 Any Unpaid Collab", "callback_data": "job_search_any_unpaid"},
            ],
            [
                {"text": "🔙 Back to Main Menu", "callback_data": "job_menu_main"},
            ]
        ]
    }
    return text, keyboard


def _send_telegram_menu(chat_id: str, bot_token: str, text: str, keyboard: dict) -> bool:
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "reply_markup": json.dumps(keyboard),
                },
            )
            return resp.status_code == 200
    except Exception as exc:
        logger.warning("Failed to send telegram menu: %s", exc)
        return False


def _edit_telegram_menu(chat_id: str, bot_token: str, message_id: int, text: str, keyboard: dict) -> bool:
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                f"{TELEGRAM_API_BASE}/bot{bot_token}/editMessageText",
                json={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "reply_markup": json.dumps(keyboard),
                },
            )
            if resp.status_code == 200:
                return True
            return _send_telegram_menu(chat_id, bot_token, text, keyboard)
    except Exception:
        return _send_telegram_menu(chat_id, bot_token, text, keyboard)


def _answer_callback_query(bot_token: str, callback_query_id: str, text: Optional[str] = None) -> bool:
    payload: dict[str, Any] = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(f"{TELEGRAM_API_BASE}/bot{bot_token}/answerCallbackQuery", json=payload)
            return resp.status_code == 200
    except Exception:
        return False


def _send_telegram_text(chat_id: str, bot_token: str, html_text: str) -> bool:
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": html_text,
                    "parse_mode": "HTML",
                },
            )
            return resp.status_code == 200
    except Exception:
        return False


def _send_comment_card(
    chat_id: str,
    bot_token: str,
    tweet_id: str,
    author: str,
    tweet_text: str,
    likes: int,
    reply_text: str,
    reason: str,
    eng_state: dict,
    settings: Settings,
) -> bool:
    from datetime import datetime, timezone
    from app.engagement import save_engagement_state

    # Modern X intent URL for direct reply
    params = {
        "in_reply_to": tweet_id,
        "text": reply_text,
    }
    intent_url = f"https://x.com/intent/post?{urllib.parse.urlencode(params)}"
    orig_url = f"https://x.com/{author}/status/{tweet_id}"

    keyboard = {
        "inline_keyboard": [
            [
                {"text": "💬 1-Tap Reply on X (Mobile App)", "url": intent_url}
            ],
            [
                {"text": "🌐 View Original Tweet", "url": orig_url}
            ]
        ]
    }

    escaped_author = html.escape(author)
    display_tweet = tweet_text[:280] + "..." if len(tweet_text) > 280 else tweet_text
    escaped_tweet = html.escape(display_tweet)
    escaped_reply = html.escape(reply_text)
    escaped_reason = html.escape(reason)

    message = (
        f"🎯 <b>TOP TECH DISCUSSION TARGET</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Author:</b> @{escaped_author} ({likes} ❤️)\n"
        f"📝 <b>Original Tweet:</b>\n"
        f"<i>\"{escaped_tweet}\"</i>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 <b>AI DRAFTED SMART COMMENT:</b>\n"
        f"<code>{escaped_reply}</code>\n\n"
        f"🎯 <b>Architectural Nuance:</b>\n"
        f"<i>{escaped_reason}</i>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📲 <b>1-Tap Posting:</b>\n"
        f"Niche <b>[💬 1-Tap Reply on X]</b> par tap karein.\n"
        f"Aapke phone ka official X app @{escaped_author} ke tweet par reply composer ke sath khulega, aur comment pre-filled hoga. "
        f"Bas <b>'Reply'</b> daba dein (0% ban risk)!"
    )

    now_iso = datetime.now(timezone.utc).isoformat()
    eng_state.setdefault("replies", []).append({
        "reply_tweet_id": "telegram_intent",
        "target_tweet_id": tweet_id,
        "target_author": author,
        "reply_text": reply_text,
        "replied_at": now_iso,
    })
    eng_state["last_replied_at"] = now_iso
    save_engagement_state(settings.state_dir, eng_state)

    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.post(
                f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": message,
                    "parse_mode": "HTML",
                    "reply_markup": json.dumps(keyboard),
                    "disable_web_page_preview": True,
                },
            )
            return resp.status_code == 200
    except Exception as exc:
        logger.error("Failed to send comment recommendation to Telegram: %s", exc)
        return False


def trigger_telegram_comment(settings: Settings, target_chat_id: Optional[str] = None) -> bool:
    """
    Finds top relevant engineering discussion tweets and dispatches a high-IQ
    smart comment recommendation to Telegram with a 1-tap mobile reply button.
    """
    from app.engagement import (
        find_engagement_candidates,
        draft_reply,
        validate_reply_quality,
        load_engagement_state,
    )

    chat_id = target_chat_id or settings.telegram_chat_id.strip()
    bot_token = settings.telegram_bot_token.strip()
    if not bot_token or not chat_id:
        logger.warning("Telegram bot credentials not configured for comment dispatch.")
        return False

    eng_state = load_engagement_state(settings.state_dir)
    candidates = []
    try:
        candidates = find_engagement_candidates(settings)
    except Exception as exc:
        logger.warning("find_engagement_candidates error: %s", exc)

    # Fallback to satellite search opportunities if regular candidates empty
    if not candidates:
        try:
            import asyncio
            from app.satellite import find_satellite_opportunities
            opps = asyncio.run(find_satellite_opportunities(settings))
            for o in opps:
                reply, reason = draft_reply(o.text, settings, author=o.author)
                if reply:
                    return _send_comment_card(
                        chat_id=chat_id,
                        bot_token=bot_token,
                        tweet_id=o.tweet_id,
                        author=o.author,
                        tweet_text=o.text,
                        likes=o.likes,
                        reply_text=reply,
                        reason=reason,
                        eng_state=eng_state,
                        settings=settings,
                    )
        except Exception as exc:
            logger.warning("satellite fallback error in trigger_telegram_comment: %s", exc)

    if not candidates:
        _send_telegram_text(
            chat_id,
            bot_token,
            "ℹ️ <b>Abhi koi nayi high-signal engineering discussion nahi mili.</b>\nKuch der baad dubara <b>/cmt</b> try karein!",
        )
        return False

    for c in candidates:
        reply, reason = draft_reply(c.text, settings, author=c.author)
        if not reply:
            continue
        valid, _ = validate_reply_quality(c, reply, eng_state)
        if not valid:
            continue

        return _send_comment_card(
            chat_id=chat_id,
            bot_token=bot_token,
            tweet_id=c.tweet_id,
            author=c.author,
            tweet_text=c.text,
            likes=c.likes,
            reply_text=reply,
            reason=reason,
            eng_state=eng_state,
            settings=settings,
        )

    _send_telegram_text(
        chat_id,
        bot_token,
        "⚠️ <i>Discussions mili par quality validator ne draft filter kar diya. Thodi der baad dubara /cmt try karein!</i>",
    )
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
    print("   • /cmt        - Find top tech discussion & get 1-Tap smart comment")
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

                    # 1. Process Callback Queries (Inline Button Taps)
                    cb = u.get("callback_query")
                    if cb:
                        cb_id = str(cb.get("id", ""))
                        cb_data = str(cb.get("data", ""))
                        cb_chat_id = str(cb.get("message", {}).get("chat", {}).get("id") or cb.get("from", {}).get("id", ""))
                        cb_msg_id = cb.get("message", {}).get("message_id")

                        if cb_data == "job_menu_main":
                            _answer_callback_query(bot_token, cb_id)
                            m_text, m_kb = build_job_main_menu()
                            if cb_msg_id:
                                _edit_telegram_menu(cb_chat_id, bot_token, cb_msg_id, m_text, m_kb)
                            else:
                                _send_telegram_menu(cb_chat_id, bot_token, m_text, m_kb)
                            continue

                        elif cb_data == "job_menu_paid":
                            _answer_callback_query(bot_token, cb_id)
                            m_text, m_kb = build_job_paid_menu()
                            if cb_msg_id:
                                _edit_telegram_menu(cb_chat_id, bot_token, cb_msg_id, m_text, m_kb)
                            else:
                                _send_telegram_menu(cb_chat_id, bot_token, m_text, m_kb)
                            continue

                        elif cb_data == "job_menu_unpaid":
                            _answer_callback_query(bot_token, cb_id)
                            m_text, m_kb = build_job_unpaid_menu()
                            if cb_msg_id:
                                _edit_telegram_menu(cb_chat_id, bot_token, cb_msg_id, m_text, m_kb)
                            else:
                                _send_telegram_menu(cb_chat_id, bot_token, m_text, m_kb)
                            continue

                        elif cb_data.startswith("job_search_"):
                            cat = cb_data.replace("job_search_", "")
                            from app.hiring_radar import CATEGORY_CONFIG, trigger_telegram_hiring
                            cat_title = CATEGORY_CONFIG.get(cat, {}).get("title", cat.replace("_", " ").title())
                            _answer_callback_query(bot_token, cb_id, text=f"Hunting {cat_title}...")
                            _send_telegram_text(
                                cb_chat_id,
                                bot_token,
                                f"🔍 <i>Hunting remote <b>{html.escape(cat_title)}</b> on X live...</i>",
                            )
                            trigger_telegram_hiring(settings, category=cat, target_chat_id=cb_chat_id)
                            continue

                    # 2. Process Chat Messages
                    msg = u.get("message") or u.get("edited_message")
                    if not msg:
                        continue
                    raw_text = (msg.get("text") or "").strip()
                    text = raw_text.lower()
                    chat_id = str(msg.get("chat", {}).get("id", ""))

                    if not text:
                        continue

                    if text in ("/start", "/help"):
                        help_text = (
                            "👋 <b>Welcome to Aman's X-Automation Co-Pilot!</b>\n\n"
                            "Commands you can send me anytime:\n"
                            "• <b>/job</b> - Interactive popup for 100% Remote Opportunities (Paid & Unpaid)\n"
                            "• <b>/cmt</b> - Find top tech discussion & get 1-Tap smart comment\n"
                            "• <b>/new</b> - Generate a fresh high-IQ post & visual card\n"
                            "• <b>/showcase</b> - Feature your projects (Storage, Analytics, PodVoice)\n"
                            "• <b>/deepdive</b> - Systems & Architecture deep dive\n"
                            "• <b>/discussion</b> - Engineering discussion question\n"
                            "• <b>/ping</b> - Check connection\n\n"
                            "⚡ Every post, comment & job pitch arrives with a 1-Tap button to publish from your phone with 0% ban risk!"
                        )
                        _send_telegram_text(chat_id, bot_token, help_text)
                    elif text == "/ping":
                        _send_telegram_text(
                            chat_id,
                            bot_token,
                            "🟢 Co-Pilot is online, healthy, and ready on Render 24/7!",
                        )
                    elif text in ("/job", "/jobs", "/remote", "/work", "/hiring", "/startup"):
                        m_text, m_kb = build_job_main_menu()
                        _send_telegram_menu(chat_id, bot_token, m_text, m_kb)
                    elif text in ("/job paid", "/jobs paid", "/paid"):
                        m_text, m_kb = build_job_paid_menu()
                        _send_telegram_menu(chat_id, bot_token, m_text, m_kb)
                    elif text in ("/job unpaid", "/jobs unpaid", "/unpaid", "/collab", "/free"):
                        m_text, m_kb = build_job_unpaid_menu()
                        _send_telegram_menu(chat_id, bot_token, m_text, m_kb)
                    elif text in ("/cmt", "/comment", "/reply", "/engage"):
                        _send_telegram_text(
                            chat_id,
                            bot_token,
                            "🔍 <i>Scanning X for trending engineering discussions & drafting high-IQ smart reply...</i>",
                        )
                        trigger_telegram_comment(settings, target_chat_id=chat_id)
                    elif text in ("/new", "/draft", "/post"):
                        _send_telegram_text(
                            chat_id,
                            bot_token,
                            "⚙️ <i>Researching latest engineering developments & generating draft...</i>",
                        )
                        trigger_telegram_post(settings, force=True)
                    elif text in ("/showcase", "/project"):
                        _send_telegram_text(
                            chat_id,
                            bot_token,
                            "⚙️ <i>Preparing project showcase for Aman's open-source architecture...</i>",
                        )
                        trigger_telegram_post(settings, preferred_kind="showcase", force=True)
                    elif text in ("/deepdive", "/deep_dive"):
                        _send_telegram_text(
                            chat_id,
                            bot_token,
                            "⚙️ <i>Formulating systems architecture deep dive...</i>",
                        )
                        trigger_telegram_post(settings, preferred_kind="deep_dive", force=True)
                    elif text in ("/discussion", "/question"):
                        _send_telegram_text(
                            chat_id,
                            bot_token,
                            "⚙️ <i>Crafting high-signal developer discussion dilemma...</i>",
                        )
                        trigger_telegram_post(settings, preferred_kind="discussion", force=True)

        except KeyboardInterrupt:
            print("\n👋 Telegram Bot listener stopped.")
            break
        except Exception as exc:
            logger.warning("Listener loop encountered error: %s", exc)
            time.sleep(3)

