from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


def _extract_json(value: str) -> dict[str, Any] | None:
    if not value:
        return None
    text = value.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].lstrip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _post_with_retry(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    provider: str,
) -> httpx.Response | None:
    for attempt in range(3):
        try:
            response = client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            logger.warning("%s request failed: %s", provider, exc.__class__.__name__)
            return None
        if response.status_code == 200:
            return response
        if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
            time.sleep(2**attempt)
            continue
        logger.warning("%s returned HTTP %s: %s", provider, response.status_code, response.text[:200])
        return None
    return None


def _gemini(
    client: httpx.Client, settings: Settings, system: str, user: str
) -> dict[str, Any] | None:
    if not settings.gemini_api_key:
        return None
    for model in settings.gemini_models:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={settings.gemini_api_key}"
        )
        payload = {
            "contents": [{"role": "user", "parts": [{"text": f"{system}\n\n{user}"}]}],
            "generationConfig": {"temperature": 0.3, "responseMimeType": "application/json"},
        }
        res = _post_with_retry(client, url, headers={"Content-Type": "application/json"}, payload=payload, provider="Gemini")
        if not res:
            continue
        try:
            candidates = res.json().get("candidates", [])
            text = candidates[0]["content"]["parts"][0]["text"]
            parsed = _extract_json(text)
            if parsed:
                return parsed
        except (KeyError, IndexError, TypeError):
            continue
    return None


def _groq(
    client: httpx.Client, settings: Settings, system: str, user: str
) -> dict[str, Any] | None:
    if not settings.groq_api_key:
        return None
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.groq_api_key}",
        "Content-Type": "application/json",
    }
    for model in settings.groq_models:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.3,
        }
        res = _post_with_retry(client, url, headers=headers, payload=payload, provider="Groq")
        if not res:
            continue
        try:
            content = res.json()["choices"][0]["message"]["content"]
            parsed = _extract_json(content)
            if parsed:
                return parsed
        except (KeyError, IndexError, TypeError):
            continue
    return None


def _openrouter(
    client: httpx.Client, settings: Settings, system: str, user: str
) -> dict[str, Any] | None:
    if not settings.openrouter_api_key:
        return None
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
    }
    for model in settings.openrouter_models:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.3,
        }
        res = _post_with_retry(client, url, headers=headers, payload=payload, provider="OpenRouter")
        if not res:
            continue
        try:
            content = res.json()["choices"][0]["message"]["content"]
            parsed = _extract_json(content)
            if parsed:
                return parsed
        except (KeyError, IndexError, TypeError):
            continue
    return None


def generate_json(settings: Settings, system: str, user: str) -> dict[str, Any] | None:
    """Attempts generation via Gemini, falling back to Groq, then OpenRouter."""
    with httpx.Client(timeout=15.0) as client:
        result = _gemini(client, settings, system, user)
        if result:
            return result
        result = _groq(client, settings, system, user)
        if result:
            return result
        result = _openrouter(client, settings, system, user)
        if result:
            return result
    return None
