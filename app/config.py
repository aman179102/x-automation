from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


class ConfigError(RuntimeError):
    pass


def env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


DEFAULT_GEMINI_MODELS = (
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
)
DEFAULT_GROQ_MODELS = (
    "openai/gpt-oss-120b",
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-20b",
)
DEFAULT_OPENROUTER_MODELS = (
    "openrouter/free",
)


def _model_list(plural: str, singular: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(plural) or os.getenv(singular)
    if not raw:
        return default
    values = tuple(v.strip() for v in raw.split(",") if v.strip())
    return values or default


@dataclass(frozen=True)
class Settings:
    # Twitter / X API Credentials (OAuth 1.0a)
    api_key: str = ""
    api_secret: str = ""
    access_token: str = ""
    access_token_secret: str = ""
    bearer_token: str = ""

    # 100% Free Web Session Authentication (Zero API Fees)
    auth_token: str = ""
    ct0: str = ""

    # Personal Branding
    author_name: str = "Aman Kumar"
    author_handle: str = "@aman179102"
    author_title: str = "Systems & AI Engineer"
    github_user: str = "aman179102"
    portfolio_url: str = "https://github.com/aman179102"

    # AI Model Keys & Fallbacks
    gemini_api_key: str = ""
    groq_api_key: str = ""
    openrouter_api_key: str = ""
    gemini_models: tuple[str, ...] = DEFAULT_GEMINI_MODELS
    groq_models: tuple[str, ...] = DEFAULT_GROQ_MODELS
    openrouter_models: tuple[str, ...] = DEFAULT_OPENROUTER_MODELS

    # Image Generation
    # Modes: "ai" (Dynamic Pollinations AI + Brand Overlay), "stock" (Pexels), "card" (Pillow Tech Card)
    image_mode: str = "ai"
    pexels_api_key: str = ""

    # Project Showcase Ratio (0.20 = 20% personal project posts)
    showcase_ratio: float = 0.20

    # Safety & Rate Limits (100% Ban-Proof Conservative Settings)
    timezone: str = "Asia/Kolkata"
    daily_post_cap: int = 2
    min_spacing_minutes: int = 240
    posting_window_start: str = "09:00"
    posting_window_end: str = "23:00"

    # Paths
    state_dir: Path = field(default_factory=lambda: ROOT / "state")
    output_dir: Path = field(default_factory=lambda: ROOT / "output")
    assets_dir: Path = field(default_factory=lambda: ROOT / "assets")

    # Optional integrations
    github_token: str = ""
    healthcheck_url: str = ""


def load_settings(overrides: dict[str, object] | None = None) -> Settings:
    overrides = overrides or {}

    def get_str(name: str, alt_names: tuple[str, ...] = (), default: str = "") -> str:
        for n in (name, *alt_names):
            val = os.getenv(n)
            if val is not None and val.strip():
                return val.strip()
        return default

    # Accept multiple common env names
    api_key = str(overrides.get("api_key") or get_str("X_API_KEY", ("TWITTER_API_KEY", "X_CONSUMER_KEY")))
    api_secret = str(overrides.get("api_secret") or get_str("X_API_SECRET", ("TWITTER_API_SECRET", "X_CONSUMER_SECRET")))
    access_token = str(overrides.get("access_token") or get_str("X_ACCESS_TOKEN", ("TWITTER_ACCESS_TOKEN",)))
    access_token_secret = str(overrides.get("access_token_secret") or get_str("X_ACCESS_TOKEN_SECRET", ("TWITTER_ACCESS_TOKEN_SECRET",)))
    bearer_token = str(overrides.get("bearer_token") or get_str("X_BEARER_TOKEN", ("TWITTER_BEARER_TOKEN",)))

    gemini_key = str(overrides.get("gemini_api_key") or get_str("GEMINI_API_KEY"))
    groq_key = str(overrides.get("groq_api_key") or get_str("GROQ_API_KEY"))
    openrouter_key = str(overrides.get("openrouter_api_key") or get_str("OPENROUTER_API_KEY"))

    author_name = str(overrides.get("author_name") or get_str("AUTHOR_NAME", default="Aman Kumar"))
    author_handle = str(overrides.get("author_handle") or get_str("AUTHOR_HANDLE", default="@aman179102"))
    author_title = str(overrides.get("author_title") or get_str("AUTHOR_TITLE", default="Systems & AI Engineer"))
    github_user = str(overrides.get("github_user") or get_str("GITHUB_USER", default="aman179102"))
    portfolio_url = str(overrides.get("portfolio_url") or get_str("PORTFOLIO_URL", default=f"https://github.com/{github_user}"))

    image_mode = str(overrides.get("image_mode") or get_str("IMAGE_MODE", default="ai")).lower()
    pexels_key = str(overrides.get("pexels_api_key") or get_str("PEXELS_API_KEY", default=""))

    tz_str = str(overrides.get("timezone") or get_str("TIMEZONE", default="Asia/Kolkata"))
    daily_cap = int(overrides.get("daily_post_cap") or int(os.getenv("DAILY_POST_CAP", "4")))
    min_spacing = int(overrides.get("min_spacing_minutes") or int(os.getenv("MIN_SPACING_MINUTES", "120")))

    auth_token = str(overrides.get("auth_token") or get_str("AUTH_TOKEN", ("TWITTER_AUTH_TOKEN", "X_AUTH_TOKEN")))
    ct0 = str(overrides.get("ct0") or get_str("CT0", ("TWITTER_CT0", "X_CT0")))

    return Settings(
        api_key=api_key,
        api_secret=api_secret,
        access_token=access_token,
        access_token_secret=access_token_secret,
        bearer_token=bearer_token,
        auth_token=auth_token,
        ct0=ct0,
        author_name=author_name,
        author_handle=author_handle,
        author_title=author_title,
        github_user=github_user,
        portfolio_url=portfolio_url,
        gemini_api_key=gemini_key,
        groq_api_key=groq_key,
        openrouter_api_key=openrouter_key,
        gemini_models=_model_list("GEMINI_MODELS", "GEMINI_MODEL", DEFAULT_GEMINI_MODELS),
        groq_models=_model_list("GROQ_MODELS", "GROQ_MODEL", DEFAULT_GROQ_MODELS),
        openrouter_models=_model_list("OPENROUTER_MODELS", "OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODELS),
        image_mode=image_mode,
        pexels_api_key=pexels_key,
        timezone=tz_str,
        daily_post_cap=daily_cap,
        min_spacing_minutes=min_spacing,
        github_token=get_str("GITHUB_TOKEN"),
        healthcheck_url=get_str("HEALTHCHECK_URL"),
    )
