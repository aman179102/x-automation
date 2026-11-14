from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


@dataclass(frozen=True)
class SourceRef:
    name: str
    url: str
    kind: str = "primary"


@dataclass
class StoryItem:
    source: str
    title: str
    url: str
    summary: str
    published_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    category: str = "tech"
    guid: str = ""
    score: float = 0.0
    source_kind: str = "rss"  # "rss", "github", "showcase", "deep_dive", "discussion", etc.
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def text_for_filtering(self) -> str:
        return f"{self.title} {self.summary}"

    @property
    def is_breaking(self) -> bool:
        if self.extra.get("cisa"):
            return True
        cvss = self.extra.get("cvss")
        return bool(cvss is not None and float(cvss) >= 9.0)


@dataclass
class Story:
    item: StoryItem
    sources: list[SourceRef]
    facts: dict[str, Any] = field(default_factory=dict)

    @property
    def title(self) -> str:
        return self.item.title

    @property
    def summary(self) -> str:
        return self.item.summary

    @property
    def category(self) -> str:
        return self.item.category

    @property
    def primary_source(self) -> SourceRef:
        return self.sources[0] if self.sources else SourceRef("Tech", self.item.url)

    @property
    def is_breaking(self) -> bool:
        return self.item.is_breaking

    @property
    def is_showcase(self) -> bool:
        return self.item.source_kind == "showcase"

    @property
    def source_kind(self) -> str:
        return self.item.source_kind


@dataclass
class PostDraft:
    body: str
    image_title: str
    image_facts: list[str]
    alt_text: str
    visual_prompt: str = ""
    category: str = "tech"
    story: Optional[Story] = None
    media_path: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    format_kind: str = "deep_dive"
    has_image: bool = True
    technical_takeaway: str = ""
    quality_score: float = 0.0


@dataclass
class TweetResult:
    tweet_id: str
    text: str
    media_ids: list[str] = field(default_factory=list)
    created_at: str = ""
