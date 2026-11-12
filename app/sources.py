from __future__ import annotations

import concurrent.futures
import logging
import random
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any

import httpx

from app.config import Settings
from app.models import SourceRef, Story, StoryItem
from app.text import canonical_url, clean_markdown, domain, normalize_space, strip_html, title_fingerprint

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FeedSpec:
    name: str
    url: str
    category: str
    weight: float
    limit: int = 10


FEEDS: tuple[FeedSpec, ...] = (
    FeedSpec("Google DeepMind", "https://deepmind.google/blog/rss.xml", "ai", 1.0, 8),
    FeedSpec("OpenAI", "https://openai.com/news/rss.xml", "ai", 1.0, 8),
    FeedSpec("Hugging Face", "https://huggingface.co/blog/feed.xml", "ai", 0.95, 8),
    FeedSpec("GitHub Blog", "https://github.blog/feed/", "dev", 0.95, 8),
    FeedSpec("Go Blog", "https://go.dev/blog/feed.atom", "dev", 0.95, 6),
    FeedSpec("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index", "tech", 0.9, 10),
    FeedSpec("Cloudflare", "https://blog.cloudflare.com/rss/", "dev", 0.85, 8),
    FeedSpec("The Verge", "https://www.theverge.com/rss/index.xml", "bigtech", 0.8, 10),
    FeedSpec("TechCrunch", "https://techcrunch.com/feed/", "startups", 0.75, 10),
    FeedSpec("The Hacker News", "https://feeds.feedburner.com/TheHackersNews", "security", 0.4, 5),
)

# Aman Kumar's verified open-source projects for authority & SEO showcases
PERSONAL_PROJECTS = (
    {
        "title": "Distributed File Storage Service in Go",
        "url": "https://github.com/aman179102",
        "summary": "High-performance S3-compatible distributed storage engine featuring Reed-Solomon erasure coding, content-defined deduplication, chunking, and Raft consensus metadata synchronization.",
        "category": "showcase",
        "tags": ["#Golang", "#DistributedSystems", "#OpenSource"],
        "facts": {
            "tech": "Go, Raft, Erasure Coding",
            "feature": "Survives disk failures with zero data loss via RS-erasure chunking",
        },
    },
    {
        "title": "Real-Time Enterprise Analytics Pipeline",
        "url": "https://github.com/aman179102",
        "summary": "Low-latency streaming analytics engine processing millions of events using FastAPI, Kafka, Redis streams, TimescaleDB, and OpenTelemetry instrumentation.",
        "category": "showcase",
        "tags": ["#Python", "#FastAPI", "#SystemDesign"],
        "facts": {
            "tech": "FastAPI, TimescaleDB, Kafka, WebSockets",
            "feature": "Sub-millisecond event ingestion with real-time live push",
        },
    },
    {
        "title": "PodVoice Studio: Local-First AI Podcast Engine",
        "url": "https://github.com/aman179102",
        "summary": "Private, local-first studio for multi-speaker AI podcast generation, audio processing, and script synthesis running entirely on-device.",
        "category": "showcase",
        "tags": ["#AI", "#OpenSource", "#Python"],
        "facts": {
            "tech": "Python, Speech AI, Local-first GUI",
            "feature": "100% private on-device podcast generation",
        },
    },
)


def _parse_pubdate(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(timezone.utc)
    raw = raw.strip()
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            dt = datetime.strptime(raw.replace("Z", "+00:00"), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            continue
    return datetime.now(timezone.utc)


def _fetch_feed(client: httpx.Client, spec: FeedSpec) -> list[StoryItem]:
    items: list[StoryItem] = []
    try:
        resp = client.get(spec.url, timeout=12.0)
        if resp.status_code != 200:
            return []
        root = ET.fromstring(resp.content)
    except Exception as exc:
        logger.debug("Failed fetching %s: %s", spec.name, exc)
        return []

    # Handle RSS 2.0
    channel = root.find("channel")
    if channel is not None:
        for entry in channel.findall("item")[: spec.limit]:
            title = strip_html(entry.findtext("title") or "")
            link = (entry.findtext("link") or "").strip()
            desc = strip_html(entry.findtext("description") or "")
            pub = _parse_pubdate(entry.findtext("pubDate"))
            if title and link:
                items.append(
                    StoryItem(
                        source=spec.name,
                        title=title,
                        url=canonical_url(link),
                        summary=desc,
                        published_at=pub,
                        category=spec.category,
                        score=spec.weight,
                    )
                )
        return items

    # Handle Atom
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for entry in root.findall("atom:entry", ns)[: spec.limit] or root.findall("entry")[: spec.limit]:
        title = strip_html(entry.findtext("atom:title", namespaces=ns) or entry.findtext("title") or "")
        link_elem = entry.find("atom:link", ns) or entry.find("link")
        link = link_elem.attrib.get("href", "") if link_elem is not None else ""
        summary = strip_html(entry.findtext("atom:summary", namespaces=ns) or entry.findtext("summary") or "")
        pub = _parse_pubdate(entry.findtext("atom:updated", namespaces=ns) or entry.findtext("updated"))
        if title and link:
            items.append(
                StoryItem(
                    source=spec.name,
                    title=title,
                    url=canonical_url(link),
                    summary=summary,
                    published_at=pub,
                    category=spec.category,
                    score=spec.weight,
                )
            )
    return items


def fetch_all_stories(settings: Settings, include_showcase: bool = False) -> list[Story]:
    """Fetch stories from feeds concurrently."""
    stories: list[Story] = []

    if include_showcase:
        proj = random.choice(PERSONAL_PROJECTS)
        item = StoryItem(
            source=settings.author_name,
            title=proj["title"],
            url=proj["url"],
            summary=proj["summary"],
            category="showcase",
            source_kind="showcase",
            score=1.5,
            extra={"tags": proj["tags"]},
        )
        return [Story(item=item, sources=[SourceRef(settings.author_name, proj["url"])], facts=proj["facts"])]

    with httpx.Client(headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; x64) AppleWebKit/537.36"}) as client:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(_fetch_feed, client, spec) for spec in FEEDS]
            for fut in concurrent.futures.as_completed(futures):
                try:
                    for item in fut.result():
                        stories.append(
                            Story(
                                item=item,
                                sources=[SourceRef(item.source, item.url)],
                            )
                        )
                except Exception as exc:
                    logger.debug("Error collecting feed items: %s", exc)

    return stories
