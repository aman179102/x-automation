from __future__ import annotations

import html
import json
import logging
import random
import re
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

from app.ai import generate_json
from app.config import Settings
from app.text import normalize_space, twitter_length

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"

# Target search queries designed to catch human founders, tech leads, and startups hiring remotely
HIRING_SEARCH_QUERIES = (
    # Web Dev & Fullstack
    '("looking for" OR "need") ("website developer" OR "web dev" OR "fullstack" OR "frontend dev") ("remote" OR "dm me" OR "budget")',
    # Backend, Go, Python, APIs
    '("hiring" OR "looking for") ("remote" OR "contract") ("backend developer" OR "golang developer" OR "python developer")',
    # Scrappy Startups, Early MVPs & Gigs
    '("building an mvp" OR "need a developer" OR "hire a dev" OR "paid gig") ("remote" OR "dm me")',
    # Paid Internships & Junior/Mid Remote Roles
    '("hiring" OR "looking for") ("paid intern" OR "software intern" OR "junior developer") ("remote" OR "india" OR "anywhere")',
    # India Remote Tech Startups
    '("hiring" OR "looking for") ("remote developer" OR "remote engineer") ("bangalore" OR "india")',
    # Global / US Remote
    '"hiring remote" ("developer" OR "software engineer" OR "founding engineer") "dm"',
)

BOT_FILTER_KEYWORDS = [
    "jobbot", "careersbot", "alert", "listing", "hirebot", "recruitingbot",
    "job_search", "workfromhomebot", "aggregat",
]

HIRING_PITCH_PROMPT = """You are drafting a short, punchy 1-tap direct reply on X for Aman Kumar (@aman179102) to apply for a remote job/gig/internship posted by a startup founder or recruiter.

Aman Kumar's Profile:
- Senior Systems & AI Engineer, versatile fullstack & backend developer.
- Tech Stack: Go, Python, FastAPI, Node, React, Distributed Storage, Streaming pipelines, APIs, Web development.
- Key Strengths: Fast shipper, independent execution, zero hand-holding, clean architecture, high work ethic.
- Available for: Full remote, contracts, paid internships, MVP building, backend, web dev.
- Portfolio: https://github.com/aman179102

OPPORTUNITY DETAILS:
Role Type: {role_type}
Target Tweet by @{author}:
"{tweet_text}"

INSTRUCTIONS:
1. Match the exact need:
   - If Web Dev / Frontend: Mention fast web shipping, clean UI/API integration.
   - If Backend / Go / Python: Mention high throughput, clean APIs, database efficiency.
   - If Paid Intern / MVP / Quick Gig: Mention fast turnaround, hunger, ready to start immediately today.
2. Tone: Confident, energetic, professional, no corporate fluff, zero generic praise.
3. Call to Action: Always invite to DMs and include exact portfolio link: https://github.com/aman179102.
4. Total reply MUST be strictly under 230 characters.

Return JSON strictly:
{{
  "pitch": "Your tailored reply including https://github.com/aman179102 (under 230 characters).",
  "reasoning": "Why this pitch matches the specific tweet."
}}"""


@dataclass
class HiringOpportunity:
    tweet_id: str
    author: str
    text: str
    likes: int
    created_at: str
    role_type: str = "GENERAL_REMOTE"
    budget_or_rate: str = ""
    suggested_pitch: str = ""
    reasoning: str = ""


def _default_hiring_state() -> dict[str, Any]:
    return {
        "version": 1,
        "pitched_tweet_ids": [],
        "last_checked_at": None,
    }


def load_hiring_state(state_dir: Path) -> dict[str, Any]:
    state_file = state_dir / "hiring_state.json"
    if not state_file.exists():
        return _default_hiring_state()
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return _default_hiring_state()


def save_hiring_state(state_dir: Path, state: dict[str, Any]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "hiring_state.json"
    tmp_file = state_dir / "hiring_state.json.tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    tmp_file.replace(state_file)


def classify_role_type(tweet_text: str) -> str:
    text_lower = tweet_text.lower()
    if any(k in text_lower for k in ["intern", "internship"]):
        return "PAID_INTERN"
    elif any(k in text_lower for k in ["golang", "go dev", "go engineer"]):
        return "GOLANG_BACKEND"
    elif any(k in text_lower for k in ["web dev", "website", "frontend", "react", "nextjs", "css", "html"]):
        return "WEB_DEVELOPMENT"
    elif any(k in text_lower for k in ["backend", "python", "fastapi", "django", "node", "api"]):
        return "BACKEND_API"
    elif any(k in text_lower for k in ["mvp", "build an app", "gig", "contract", "freelance"]):
        return "STARTUP_MVP_GIG"
    return "REMOTE_SOFTWARE_ENGINEER"


def extract_budget(tweet_text: str) -> str:
    # Match patterns like $450, $70/hr, $70-100/hr, 50k, 100k, ₹50,000, 50k INR
    match = re.search(r"(\$\s?\d+[\d,]*(?:\s?-\s?\$?\d+[\d,]*)?(?:\s?/\s?hr|k)?|₹\s?\d+[\d,]*(?:k)?)", tweet_text, re.IGNORECASE)
    if match:
        return match.group(0).strip()
    return ""


def is_valid_hiring_tweet(tweet: Any, past_ids: set[str], my_handle: str) -> bool:
    t_id = str(getattr(tweet, "id", ""))
    if not t_id or t_id in past_ids:
        return False

    author = str(getattr(getattr(tweet, "user", None), "screen_name", "")).lower()
    if author == my_handle.lower().strip("@"):
        return False

    # Filter bot accounts
    if any(b in author for b in BOT_FILTER_KEYWORDS):
        return False

    text = str(getattr(tweet, "text", "") or getattr(tweet, "full_text", "")).lower()
    if len(text) < 35:
        return False

    # Spam terms
    spam_terms = ["airdrop", "giveaway", "whatsapp group", "telegram channel", "crypto signal", "buy followers"]
    if any(s in text for s in spam_terms):
        return False

    return True


async def find_hiring_opportunities(settings: Settings, limit: int = 5) -> list[HiringOpportunity]:
    """
    Searches X for active remote hiring tweets, paid gigs, internships, and startup MVP dev needs.
    """
    from twikit import Client as TwikitClient

    auth_token = settings.auth_token
    ct0 = settings.ct0
    if not auth_token or not ct0:
        logger.warning("No session cookies available for hiring opportunity search.")
        return []

    client = TwikitClient("en-US", impersonate="chrome124")
    client.set_cookies({"auth_token": auth_token, "ct0": ct0})

    state = load_hiring_state(settings.state_dir)
    past_ids = set(state.get("pitched_tweet_ids", []))

    # Pick 2 random queries per run to cover different angles
    queries = random.sample(HIRING_SEARCH_QUERIES, min(2, len(HIRING_SEARCH_QUERIES)))
    opportunities: list[HiringOpportunity] = []

    for q in queries:
        try:
            results = await client.search_tweet(q, "Latest")
            if not results:
                continue

            for t in results:
                if not is_valid_hiring_tweet(t, past_ids, settings.author_handle):
                    continue

                full_text = normalize_space(str(getattr(t, "text", "") or getattr(t, "full_text", "")))
                author = str(t.user.screen_name)
                likes = int(getattr(t, "favorite_count", 0) or 0)
                created_at = str(getattr(t, "created_at", ""))
                role_type = classify_role_type(full_text)
                budget = extract_budget(full_text)

                opportunities.append(HiringOpportunity(
                    tweet_id=str(t.id),
                    author=author,
                    text=full_text,
                    likes=likes,
                    created_at=created_at,
                    role_type=role_type,
                    budget_or_rate=budget,
                ))

                if len(opportunities) >= limit:
                    break
        except Exception as exc:
            logger.warning("Hiring query '%s' error: %s", q, exc)

        if len(opportunities) >= limit:
            break

    return opportunities


def draft_hiring_pitch(opp: HiringOpportunity, settings: Settings) -> tuple[str, str]:
    """
    Generates a tailored 1-tap pitch addressing the specific hiring need.
    """
    prompt = HIRING_PITCH_PROMPT.format(
        role_type=opp.role_type,
        author=opp.author,
        tweet_text=opp.text,
    )
    user_msg = f"Draft the tailored application pitch for @{opp.author}'s opportunity:"

    res = generate_json(settings, prompt, user_msg)
    if not res or not isinstance(res, dict) or not res.get("pitch"):
        # Reliable fallback tailored pitch
        fallback = f"Hi @{opp.author}! Versatile engineer specializing in web, backend & Go/Python. Fast shipper, clean code: https://github.com/aman179102. Ready to start immediately, let's talk in DMs!"
        return fallback, "Fallback high-impact portfolio pitch"

    pitch = normalize_space(str(res["pitch"]))
    reason = str(res.get("reasoning", ""))

    # Ensure Aman's GitHub is in the pitch
    if "github.com/aman179102" not in pitch:
        pitch = f"{pitch} https://github.com/aman179102"

    if twitter_length(pitch) > 280:
        pitch = pitch[:275] + "..."

    return pitch, reason


def send_hiring_card_to_telegram(
    opp: HiringOpportunity,
    pitch: str,
    reason: str,
    settings: Settings,
    target_chat_id: Optional[str] = None,
) -> bool:
    """
    Sends the hiring opportunity to Aman's Telegram with a 1-tap mobile reply button.
    """
    chat_id = target_chat_id or settings.telegram_chat_id.strip()
    bot_token = settings.telegram_bot_token.strip()
    if not bot_token or not chat_id:
        return False

    # Mobile Web Intent for 1-tap reply
    params = {
        "in_reply_to": opp.tweet_id,
        "text": pitch,
    }
    intent_url = f"https://x.com/intent/post?{urllib.parse.urlencode(params)}"
    tweet_url = f"https://x.com/{opp.author}/status/{opp.tweet_id}"

    keyboard = {
        "inline_keyboard": [
            [
                {"text": "💼 1-Tap Apply on X (Mobile App)", "url": intent_url}
            ],
            [
                {"text": "🌐 View Tweet on X", "url": tweet_url}
            ]
        ]
    }

    role_badge = opp.role_type.replace("_", " ").upper()
    budget_badge = f"💰 <b>Budget/Rate:</b> {opp.budget_or_rate}\n" if opp.budget_or_rate else ""

    escaped_author = html.escape(opp.author)
    display_text = opp.text[:300] + "..." if len(opp.text) > 300 else opp.text
    escaped_tweet = html.escape(display_text)
    escaped_pitch = html.escape(pitch)
    escaped_reason = html.escape(reason)

    message = (
        f"🚨 <b>REMOTE STARTUP OPPORTUNITY FOUND</b> [{role_badge}]\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏢 <b>Hiring Post by:</b> @{escaped_author} ({opp.likes} ❤️)\n"
        f"{budget_badge}"
        f"📝 <b>Original Post:</b>\n"
        f"<i>\"{escaped_tweet}\"</i>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 <b>TAILORED APPLICATION PITCH:</b>\n"
        f"<code>{escaped_pitch}</code>\n\n"
        f"🎯 <b>Match Strategy:</b>\n"
        f"<i>{escaped_reason}</i>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📲 <b>1-Tap Apply:</b>\n"
        f"Tap <b>[💼 1-Tap Apply on X]</b> niche.\n"
        f"Aapke phone ka official X app @{escaped_author} ke tweet par composer ke sath open hoga, pitch pre-filled hogi. "
        f"Bas <b>'Reply'</b> daba dein (0% ban risk)!"
    )

    # Save to history
    state = load_hiring_state(settings.state_dir)
    state.setdefault("pitched_tweet_ids", []).append(opp.tweet_id)
    state["last_checked_at"] = datetime.now(timezone.utc).isoformat()
    save_hiring_state(settings.state_dir, state)

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
        logger.error("Failed to send hiring card to Telegram: %s", exc)
        return False


def trigger_telegram_hiring(settings: Settings, target_chat_id: Optional[str] = None) -> bool:
    """
    Finds a fresh remote job / gig / startup opportunity and dispatches it to Telegram.
    """
    import asyncio
    chat_id = target_chat_id or settings.telegram_chat_id.strip()
    bot_token = settings.telegram_bot_token.strip()

    opps = asyncio.run(find_hiring_opportunities(settings, limit=5))
    if not opps:
        # Inform user if no immediate match
        try:
            with httpx.Client(timeout=15.0) as client:
                client.post(
                    f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": "ℹ️ <i>Scanning X live... Abhi koi fresh remote gig/job nahi mili. 10-15 minute baad dubara /job try karein!</i>",
                        "parse_mode": "HTML",
                    },
                )
        except Exception:
            pass
        return False

    for opp in opps:
        pitch, reason = draft_hiring_pitch(opp, settings)
        if pitch:
            return send_hiring_card_to_telegram(opp, pitch, reason, settings, target_chat_id=chat_id)

    return False
