from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import Settings
from app.models import PostDraft, Story

logger = logging.getLogger(__name__)


def _default_state() -> dict[str, Any]:
    return {
        "version": 1,
        "seen_urls": [],
        "posts": [],
        "last_posted_at": None,
    }


def load_state(state_dir: Path) -> dict[str, Any]:
    state_file = state_dir / "history.json"
    if not state_file.exists():
        return _default_state()
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.warning("Failed to load state: %s. Using clean state.", exc)
        return _default_state()


def save_state(state_dir: Path, state: dict[str, Any]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "history.json"
    temp_file = state_dir / "history.json.tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    temp_file.replace(state_file)


def is_seen(state: dict[str, Any], url: str) -> bool:
    return url in state.get("seen_urls", [])


def record_post(
    state: dict[str, Any],
    story: Story,
    tweet_id: str,
    tweet_text: str,
    media_path: str = "",
    format_kind: str = "news",
    quality_score: float = 0.0,
) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    state.setdefault("seen_urls", []).append(story.item.url)
    state.setdefault("posts", []).append(
        {
            "tweet_id": tweet_id,
            "title": story.title,
            "url": story.item.url,
            "category": story.category,
            "format_kind": format_kind,
            "quality_score": round(quality_score, 1),
            "posted_at": now_iso,
            "text": tweet_text,
            "media_path": media_path,
        }
    )
    state["last_posted_at"] = now_iso

    # Keep seen_urls bounded to recent 1,000 items
    if len(state["seen_urls"]) > 1000:
        state["seen_urls"] = state["seen_urls"][-1000:]
