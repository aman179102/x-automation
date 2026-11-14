from __future__ import annotations

import html
import json
import logging
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
    When tapped on Android, it opens the official X app with the tweet pre-filled.
    """
    encoded_text = urllib.parse.quote(tweet_text)
    intent_url = f"https://twitter.com/intent/tweet?text={encoded_text}"

    keyboard_rows = [
        [
            {"text": "🚀 1-Tap Post on X (Mobile)", "url": intent_url}
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
