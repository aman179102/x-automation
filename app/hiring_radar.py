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

# Target search queries categorized for Paid, Unpaid/Collab, and Any Remote opportunities
CATEGORY_CONFIG: dict[str, dict[str, Any]] = {
    # ─── PAID OPPORTUNITIES ──────────────────────────────────────────────
    "paid_intern": {
        "title": "Paid Tech Internship",
        "badge": "🎓 PAID INTERNSHIP",
        "queries": (
            '("hiring" OR "looking for") ("paid intern" OR "software intern" OR "engineering intern" OR "web dev intern" OR "developer intern") ("remote" OR "wfh" OR "anywhere")',
            '("stipend" OR "paid internship") ("developer" OR "frontend" OR "backend" OR "fullstack" OR "python" OR "react") ("remote" OR "online")',
            '("intern" OR "internship") ("remote" OR "wfh") ("stipend" OR "paid" OR "$") ("developer" OR "engineer")',
        ),
        "tone_hint": "Highlight high hunger, strong work ethic, proven GitHub portfolio, quick learning, ready to start immediately today.",
    },
    "gig_mvp": {
        "title": "Quick Gig / MVP Build",
        "badge": "⚡ QUICK GIG / MVP",
        "queries": (
            '("building an mvp" OR "need a dev" OR "build my mvp" OR "hire a dev" OR "quick gig") ("remote" OR "dm me" OR "budget")',
            '("looking for a developer" OR "hire someone to build") ("app" OR "website" OR "tool" OR "mvp") ("remote" OR "dm")',
            '("need a developer to build" OR "paying someone to build") ("mvp" OR "landing page" OR "full stack") ("remote" OR "dm")',
        ),
        "tone_hint": "Highlight rapid turnaround, MVP building from 0 to 1, fullstack execution, clean architecture.",
    },
    "freelance": {
        "title": "Freelance Project",
        "badge": "💼 FREELANCE PROJECT",
        "queries": (
            '("freelance" OR "freelancer") ("developer" OR "engineer" OR "programmer" OR "web dev" OR "fullstack") ("remote" OR "dm")',
            '("looking for freelance" OR "need freelance") ("developer" OR "frontend" OR "backend" OR "react" OR "python" OR "golang") ("remote")',
            '("freelance gig" OR "freelance project") ("developer" OR "software" OR "web") ("remote" OR "wfh")',
        ),
        "tone_hint": "Highlight independent execution, clear milestones, high accountability, direct communication.",
    },
    "contract": {
        "title": "Contract Developer Role",
        "badge": "📜 CONTRACT ROLE",
        "queries": (
            '("contract" OR "contractor") ("software engineer" OR "backend engineer" OR "fullstack developer" OR "golang") ("remote" OR "wfh")',
            '("hiring contract" OR "remote contract") ("developer" OR "engineer") ("dm" OR "apply" OR "budget")',
            '("contract developer" OR "contract engineer") ("python" OR "go" OR "backend" OR "apis") ("remote")',
        ),
        "tone_hint": "Highlight backend and systems reliability, high-throughput APIs, clean codebases, flexible contract hours.",
    },
    "fulltime": {
        "title": "Full-Time Remote",
        "badge": "🏢 FULL-TIME REMOTE",
        "queries": (
            '("hiring" OR "looking for") ("full time" OR "founding engineer" OR "software engineer" OR "web developer") ("remote" OR "anywhere") ("dm" OR "apply")',
            '("remote" OR "wfh") ("hiring software engineer" OR "hiring full stack" OR "hiring backend") ("dm" OR "reach out")',
            '("founding engineer" OR "lead engineer") ("remote" OR "anywhere") ("dm" OR "apply")',
        ),
        "tone_hint": "Highlight senior systems capabilities, ownership mindset, loves shipping fast in high-velocity startups.",
    },
    "parttime": {
        "title": "Part-Time Remote",
        "badge": "⏱️ PART-TIME REMOTE",
        "queries": (
            '("part time" OR "part-time") ("developer" OR "engineer" OR "programmer" OR "web dev") ("remote" OR "dm")',
            '("looking for part time" OR "hiring part time") ("software" OR "dev" OR "coding" OR "backend") ("remote")',
        ),
        "tone_hint": "Highlight focused, efficient execution, 15-20 hours/week of high-leverage engineering output.",
    },
    "bounty": {
        "title": "Code Bounties & Paid Issues",
        "badge": "🪙 CODE BOUNTY",
        "queries": (
            '("bounty" OR "code bounty" OR "bug bounty" OR "paid issue") ("developer" OR "github" OR "fix" OR "feature") ("remote" OR "$")',
            '("paying" OR "reward") ("to fix" OR "build feature" OR "repo") ("github" OR "dm")',
            '("github bounty" OR "issue bounty") ("remote" OR "$") ("developer" OR "coder")',
        ),
        "tone_hint": "Highlight deep codebase reading, rapid bug isolation, clean PR submission, fast turnaround.",
    },
    "any_paid": {
        "title": "Any Paid Remote Work",
        "badge": "💰 PAID REMOTE",
        "queries": (),
        "tone_hint": "Emphasize fast shipping, high ROI, production reliability, ready to start immediately today.",
    },

    # ─── UNPAID / COLLABORATION OPPORTUNITIES ──────────────────────────
    "unpaid_intern": {
        "title": "Unpaid / Learning Internship",
        "badge": "🆓 UNPAID INTERNSHIP",
        "queries": (
            '("unpaid intern" OR "unpaid internship" OR "volunteer intern" OR "student intern" OR "summer intern") ("developer" OR "tech" OR "software" OR "web") ("remote" OR "wfh")',
            '("internship" OR "intern") ("remote") ("experience" OR "certificate" OR "learn") ("developer" OR "tech")',
        ),
        "tone_hint": "Highlight intense hunger to learn and contribute, high reliability, clean code, ready to do the heavy lifting remotely.",
    },
    "opensource": {
        "title": "Open Source Collaboration",
        "badge": "🤝 OPEN SOURCE COLLAB",
        "queries": (
            '("looking for contributors" OR "open source project" OR "open-source" OR "need contributors") ("github" OR "repo" OR "developer") ("remote")',
            '("help wanted" OR "contribute to" OR "collaborators wanted") ("open source" OR "github") ("python" OR "go" OR "react" OR "rust")',
        ),
        "tone_hint": "Highlight active open-source contributor, clean git hygiene, thorough tests, enthusiasm for the project.",
    },
    "cofounder": {
        "title": "Technical Co-Founder / Equity",
        "badge": "🚀 CO-FOUNDER / EQUITY",
        "queries": (
            '("looking for a technical cofounder" OR "technical co-founder" OR "tech cofounder" OR "equity partner") ("remote" OR "mvp" OR "startup")',
            '("looking for a cto" OR "founding dev" OR "co-founder") ("equity" OR "remote" OR "dm me")',
        ),
        "tone_hint": "Highlight fullstack systems architect, can build and scale the MVP from scratch, startup resilience.",
    },
    "hackathon": {
        "title": "Hackathon Teammate",
        "badge": "🏆 HACKATHON TEAM",
        "queries": (
            '("hackathon" OR "hackathon team" OR "need teammates" OR "looking for teammate") ("developer" OR "engineer" OR "builder") ("remote" OR "online")',
            '("hackathon" AND ("need a coder" OR "need dev" OR "teammates wanted" OR "looking for team"))',
        ),
        "tone_hint": "Highlight fast builder, handles frontend + backend + AI integration under tight hackathon countdowns.",
    },
    "volunteer": {
        "title": "Volunteer Dev / NGO",
        "badge": "🎗️ VOLUNTEER / NGO",
        "queries": (
            '("volunteer developer" OR "volunteer engineer" OR "pro bono" OR "ngo developer" OR "non-profit") ("website" OR "software" OR "remote")',
            '("looking for volunteers" OR "volunteer coder") ("tech" OR "app" OR "website") ("remote")',
        ),
        "tone_hint": "Highlight dedication to the social cause, reliable delivery, clean web/software development.",
    },
    "any_unpaid": {
        "title": "Any Unpaid Collaboration",
        "badge": "🆓 UNPAID COLLAB",
        "queries": (),
        "tone_hint": "Highlight dedication, collaborative spirit, eagerness to build meaningful software remotely.",
    },

    # ─── ANY ALL REMOTE ──────────────────────────────────────────────────
    "any_all": {
        "title": "Any Remote Opportunity",
        "badge": "🌐 REMOTE OPPORTUNITY",
        "queries": (),
        "tone_hint": "Highlight versatile engineering skills (web, backend, Go/Python), fast shipping, and ready to start immediately.",
    },
}

PAID_KEYS = ("paid_intern", "gig_mvp", "freelance", "contract", "fulltime", "parttime", "bounty")
UNPAID_KEYS = ("unpaid_intern", "opensource", "cofounder", "hackathon", "volunteer")

BOT_FILTER_KEYWORDS = [
    "jobbot", "careersbot", "alert", "listing", "hirebot", "recruitingbot",
    "job_search", "workfromhomebot", "aggregat",
]

# Strict on-site / in-office disqualifiers
ONSITE_DISQUALIFIERS = [
    "in-office only", "on-site only", "onsite only", "no remote", "not remote",
    "relocation required", "must relocate to", "must be in office", "work from office only",
    "office based only", "must work from bangalore office", "must work from gurgaon office",
]

HIRING_PITCH_PROMPT = """You are drafting a short, punchy 1-tap direct reply on X for Aman Kumar (@aman179102) to apply for a remote opportunity posted by a startup founder, recruiter, or lead developer.

Aman Kumar's Profile:
- Senior Systems & AI Engineer, versatile fullstack & backend developer.
- Tech Stack: Go, Python, FastAPI, Node, React, Distributed Storage, Streaming pipelines, APIs, Web development.
- Key Strengths: Fast shipper, independent execution, zero hand-holding, clean architecture, high work ethic.
- Available for: 100% remote work (paid gigs, contracts, internships, MVPs, open source, collaborations).
- Portfolio: https://github.com/aman179102

OPPORTUNITY DETAILS:
Role / Category: {role_type}
Target Tweet by @{author}:
"{tweet_text}"
Strategy Hint: {tone_hint}

INSTRUCTIONS:
1. Match the exact opportunity:
   - If Paid / Gig / Contract / Web / Backend: Emphasize fast delivery, production reliability, clean code, ready to start immediately today.
   - If Intern / Unpaid / Volunteer: Emphasize hunger, high dedication, clean PRs, fast learning, ready to do the heavy lifting remotely.
   - If Co-Founder / Hackathon: Emphasize rapid MVP building from scratch, fullstack versatility, builder grit.
2. Tone: Confident, energetic, professional, no corporate fluff, zero generic praise.
3. Call to Action: Always invite to DMs and include exact portfolio link: https://github.com/aman179102.
4. Total reply MUST be strictly under 230 characters.

Return JSON strictly:
{{
  "pitch": "Your tailored reply including https://github.com/aman179102 (under 230 characters).",
  "reasoning": "Why this pitch matches the specific opportunity."
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


def classify_role_type(tweet_text: str, category: Optional[str] = None) -> str:
    """
    Classifies the role type, respecting requested category if provided.
    """
    if category and category in CATEGORY_CONFIG and category not in ("any_paid", "any_unpaid", "any_all"):
        return category.upper()

    text_lower = tweet_text.lower()
    if any(k in text_lower for k in ["bounty", "bug bounty", "paid issue"]):
        return "BOUNTY"
    elif any(k in text_lower for k in ["unpaid intern", "volunteer intern"]):
        return "UNPAID_INTERN"
    elif any(k in text_lower for k in ["intern", "internship", "stipend"]):
        return "PAID_INTERN"
    elif any(k in text_lower for k in ["open source", "opensource", "contributor"]):
        return "OPENSOURCE"
    elif any(k in text_lower for k in ["cofounder", "co-founder", "equity partner", "cto"]):
        return "COFOUNDER"
    elif any(k in text_lower for k in ["hackathon", "teammate"]):
        return "HACKATHON"
    elif any(k in text_lower for k in ["volunteer", "pro bono", "ngo", "non-profit"]):
        return "VOLUNTEER"
    elif any(k in text_lower for k in ["golang", "go dev", "go engineer"]):
        return "GOLANG_BACKEND"
    elif any(k in text_lower for k in ["web dev", "website", "frontend", "react", "nextjs", "css", "html"]):
        return "WEB_DEVELOPMENT"
    elif any(k in text_lower for k in ["backend", "python", "fastapi", "django", "node", "api"]):
        return "BACKEND_API"
    elif any(k in text_lower for k in ["contract", "contractor"]):
        return "CONTRACT"
    elif any(k in text_lower for k in ["freelance", "freelancer"]):
        return "FREELANCE"
    elif any(k in text_lower for k in ["mvp", "build an app", "gig"]):
        return "STARTUP_MVP_GIG"
    elif any(k in text_lower for k in ["full time", "full-time", "founding engineer"]):
        return "FULLTIME"
    elif any(k in text_lower for k in ["part time", "part-time"]):
        return "PARTTIME"
    return "REMOTE_SOFTWARE_ENGINEER"


def extract_budget(tweet_text: str) -> str:
    """
    Extracts budget, hourly rate, stipend, or equity percentages from tweet text.
    """
    match = re.search(
        r"(\$\s?\d+[\d,]*(?:\s?-\s?\$?\d+[\d,]*)?(?:\s?/\s?hr|k|/mo)?|₹\s?\d+[\d,]*(?:k|/mo)?|\d+(?:\.\d+)?%\s?equity|\bstipend\s*[:=-]?\s*[\$₹]?\d+[\d,k]*)",
        tweet_text,
        re.IGNORECASE,
    )
    if match:
        return match.group(0).strip()
    return ""


def is_valid_hiring_tweet(tweet: Any, past_ids: set[str], my_handle: str) -> bool:
    """
    Verifies that the tweet is a legitimate, non-bot, strictly remote opportunity.
    """
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
    if len(text) < 30:
        return False

    # Disqualify explicit on-site requirements
    if any(onsite in text for onsite in ONSITE_DISQUALIFIERS):
        return False

    # Spam terms
    spam_terms = ["airdrop", "giveaway", "whatsapp group", "telegram channel", "crypto signal", "buy followers"]
    if any(s in text for s in spam_terms):
        return False

    return True


def get_queries_for_category(category: str) -> list[str]:
    """
    Resolves search queries for a specific category or aggregate category.
    """
    cat = category.lower().strip()
    if cat in CATEGORY_CONFIG and CATEGORY_CONFIG[cat]["queries"]:
        return list(CATEGORY_CONFIG[cat]["queries"])

    if cat == "any_paid":
        pool: list[str] = []
        for k in PAID_KEYS:
            pool.extend(CATEGORY_CONFIG[k]["queries"])
        return pool

    if cat == "any_unpaid":
        pool: list[str] = []
        for k in UNPAID_KEYS:
            pool.extend(CATEGORY_CONFIG[k]["queries"])
        return pool

    # any_all fallback
    all_pool: list[str] = []
    for k in PAID_KEYS + UNPAID_KEYS:
        all_pool.extend(CATEGORY_CONFIG[k]["queries"])
    return all_pool


async def find_hiring_opportunities(
    settings: Settings,
    category: str = "any_all",
    limit: int = 5,
) -> list[HiringOpportunity]:
    """
    Searches X for active remote opportunities tailored to the selected category.
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

    query_pool = get_queries_for_category(category)
    if not query_pool:
        query_pool = get_queries_for_category("any_all")

    # Pick 2-3 queries from the pool
    queries = random.sample(query_pool, min(3, len(query_pool)))
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
                role_type = classify_role_type(full_text, category=category)
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


def draft_hiring_pitch(
    opp: HiringOpportunity,
    settings: Settings,
    category: str = "any_all",
) -> tuple[str, str]:
    """
    Generates a tailored 1-tap pitch addressing the specific opportunity and category.
    """
    cat_info = CATEGORY_CONFIG.get(category, {})
    tone_hint = cat_info.get("tone_hint", "Fast shipper, versatile engineer, clean code, ready to start immediately today.")

    prompt = HIRING_PITCH_PROMPT.format(
        role_type=opp.role_type,
        author=opp.author,
        tweet_text=opp.text,
        tone_hint=tone_hint,
    )
    user_msg = f"Draft the tailored application pitch for @{opp.author}'s opportunity:"

    res = generate_json(settings, prompt, user_msg)
    if not res or not isinstance(res, dict) or not res.get("pitch"):
        # Category-aware fallback
        if category in ("unpaid_intern", "opensource", "volunteer"):
            fallback = f"Hi @{opp.author}! Passionate engineer eager to contribute. Check my code: https://github.com/aman179102. Ready to submit clean PRs and help your team, let's connect!"
        elif category in ("cofounder", "hackathon"):
            fallback = f"Hey @{opp.author}! Fullstack builder who can take your MVP from 0 to 1 fast. Portfolio: https://github.com/aman179102. Let's build together, DMs open!"
        else:
            fallback = f"Hi @{opp.author}! Versatile engineer specializing in web, backend & Go/Python. Fast shipper, clean code: https://github.com/aman179102. Ready to start immediately, let's talk in DMs!"
        return fallback, "High-impact portfolio pitch"

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
    category: str = "any_all",
) -> bool:
    """
    Sends the remote opportunity to Aman's Telegram with a 1-tap mobile reply button.
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

    badge = CATEGORY_CONFIG.get(category, {}).get("badge") or opp.role_type.replace("_", " ").upper()
    budget_badge = f"💰 <b>Budget/Rate/Equity:</b> {opp.budget_or_rate}\n" if opp.budget_or_rate else ""

    keyboard = {
        "inline_keyboard": [
            [
                {"text": "💼 1-Tap Apply on X (Mobile App)", "url": intent_url}
            ],
            [
                {"text": "🌐 View Tweet on X", "url": tweet_url}
            ],
            [
                {"text": f"🔄 Hunt Another ({badge[:18]})", "callback_data": f"job_search_{category}"},
                {"text": "📋 Change Category", "callback_data": "job_menu_main"},
            ]
        ]
    }

    escaped_author = html.escape(opp.author)
    display_text = opp.text[:300] + "..." if len(opp.text) > 300 else opp.text
    escaped_tweet = html.escape(display_text)
    escaped_pitch = html.escape(pitch)
    escaped_reason = html.escape(reason)

    message = (
        f"🚨 <b>REMOTE OPPORTUNITY FOUND</b> [{badge}]\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏢 <b>Posted by:</b> @{escaped_author} ({opp.likes} ❤️)\n"
        f"{budget_badge}"
        f"📝 <b>Original Post:</b>\n"
        f"<i>\"{escaped_tweet}\"</i>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 <b>TAILORED APPLICATION PITCH:</b>\n"
        f"<code>{escaped_pitch}</code>\n\n"
        f"🎯 <b>Pitch Strategy:</b>\n"
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


def trigger_telegram_hiring(
    settings: Settings,
    category: str = "any_all",
    target_chat_id: Optional[str] = None,
) -> bool:
    """
    Finds a fresh remote opportunity for the selected category and dispatches it to Telegram.
    """
    import asyncio
    chat_id = target_chat_id or settings.telegram_chat_id.strip()
    bot_token = settings.telegram_bot_token.strip()

    opps = asyncio.run(find_hiring_opportunities(settings, category=category, limit=5))
    if not opps:
        cat_title = CATEGORY_CONFIG.get(category, {}).get("title", category)
        try:
            with httpx.Client(timeout=15.0) as client:
                kb = {
                    "inline_keyboard": [
                        [
                            {"text": f"🔄 Retry {cat_title}", "callback_data": f"job_search_{category}"},
                            {"text": "📋 Change Category", "callback_data": "job_menu_main"},
                        ]
                    ]
                }
                client.post(
                    f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": f"ℹ️ <i>Scanning X live... Abhi <b>{html.escape(cat_title)}</b> mai koi fresh 100% remote tweet nahi mila. 5-10 minute baad retry karein ya doosri category chunein!</i>",
                        "parse_mode": "HTML",
                        "reply_markup": json.dumps(kb),
                    },
                )
        except Exception:
            pass
        return False

    for opp in opps:
        pitch, reason = draft_hiring_pitch(opp, settings, category=category)
        if pitch:
            return send_hiring_card_to_telegram(
                opp,
                pitch,
                reason,
                settings,
                target_chat_id=chat_id,
                category=category,
            )

    return False
