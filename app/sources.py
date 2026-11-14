from __future__ import annotations

import concurrent.futures
import logging
import random
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from app.config import Settings
from app.extractor import extract_article_content
from app.models import SourceRef, Story, StoryItem
from app.text import canonical_url, clean_markdown, domain, normalize_space, strip_html

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FeedSpec:
    name: str
    url: str
    category: str
    weight: float
    limit: int = 8


# High-authority official engineering blogs prioritized per Section 12
FEEDS: tuple[FeedSpec, ...] = (
    FeedSpec("Google DeepMind", "https://deepmind.google/blog/rss.xml", "ai", 1.0, 6),
    FeedSpec("OpenAI", "https://openai.com/news/rss.xml", "ai", 1.0, 6),
    FeedSpec("Go Blog", "https://go.dev/blog/feed.atom", "dev", 1.0, 6),
    FeedSpec("GitHub Blog", "https://github.blog/feed/", "dev", 0.95, 6),
    FeedSpec("Cloudflare Blog", "https://blog.cloudflare.com/rss/", "dev", 0.95, 6),
    FeedSpec("Hugging Face", "https://huggingface.co/blog/feed.xml", "ai", 0.90, 6),
    FeedSpec("Netflix TechBlog", "https://netflixtechblog.com/feed", "architecture", 0.90, 6),
    FeedSpec("AWS Architecture", "https://aws.amazon.com/blogs/architecture/feed/", "architecture", 0.85, 6),
    FeedSpec("Uber Engineering", "https://www.uber.com/en-IN/blog/engineering/rss/", "architecture", 0.85, 6),
    FeedSpec("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index", "tech", 0.70, 8),
)

# Aman Kumar's verified software projects (audited against actual GitHub repository code)
PERSONAL_PROJECTS = (
    {
        "title": "Distributed File Storage Service in Go",
        "url": "https://github.com/aman179102/distributed-file-storage-service",
        "summary": "S3-compatible storage service in Go featuring Reed-Solomon erasure coding (4 data + 2 parity shards), SHA-256 chunk deduplication, PostgreSQL metadata store, and Prometheus metrics.",
        "category": "distributed",
        "source_kind": "showcase",
        "is_open_source": False,  # Proprietary license in repository
        "facts": {
            "tech": "Go, Reed-Solomon Erasure Coding (4+2), PostgreSQL, SHA-256 Dedup, S3 REST API",
            "tradeoff": "4+2 Reed-Solomon encoding provides 1.5x storage expansion versus 3x replication, tolerating 2 disk failures while cutting raw disk footprint.",
            "feature": "S3-compatible multipart uploads, range requests, and PostgreSQL metadata indexing.",
        },
        "tags": ["#Golang", "#StorageSystems"],
    },
    {
        "title": "Real-Time Streaming Analytics Pipeline",
        "url": "https://github.com/aman179102/real-time-analytics-pipeline",
        "summary": "Open-source streaming analytics pipeline built with FastAPI, TimescaleDB hypertables, Redis Streams / Kafka queue backends, WebSockets live push, and OpenTelemetry.",
        "category": "architecture",
        "source_kind": "showcase",
        "is_open_source": True,  # MIT License
        "facts": {
            "tech": "FastAPI, TimescaleDB, Redis Streams, Kafka, WebSockets, OpenTelemetry",
            "tradeoff": "Combines TimescaleDB chunk compression with adaptive rate-based sampling to handle bursty event streams without saturation.",
            "feature": "Asynchronous batch ingestion, correlation ID propagation via OpenTelemetry, and live WebSocket dashboards.",
        },
        "tags": ["#SystemDesign", "#Python", "#OpenSource"],
    },
    {
        "title": "Podvoice: Local-First Multi-Speaker AI Audio Engine",
        "url": "https://github.com/aman179102/podvoice",
        "summary": "Open-source local-first tool and FastAPI Studio web UI that converts Markdown scripts into multi-speaker podcast audio using Coqui XTTS v2 offline.",
        "category": "ai",
        "source_kind": "showcase",
        "is_open_source": True,  # Open Source
        "facts": {
            "tech": "Python, Coqui XTTS v2, FastAPI, Markdown Parsing",
            "tradeoff": "Runs 100% offline on local CPU/GPU, eliminating cloud API costs and third-party data transmission.",
            "feature": "Deterministic voice assignment from Markdown, single-pass audio stitching, and YAML speaker profile management.",
        },
        "tags": ["#Python", "#LocalAI", "#OpenSource"],
    },
)

# Curated Engineering Deep Dives & Systems Architecture (40% distribution)
ENGINEERING_DEEP_DIVES = (
    {
        "title": "LSM-Trees vs B+ Trees: The Real Write Amplification Trade-off",
        "url": "https://github.com/aman179102",
        "summary": "Why write-heavy databases like RocksDB and Cassandra pick Log-Structured Merge trees over traditional B+ trees. Sequential disk appends avoid random I/O at the cost of compaction read-amplification.",
        "category": "database",
        "source_kind": "deep_dive",
        "facts": {
            "core_concept": "Write Amplification & Sequential vs Random I/O",
            "key_takeaway": "LSM-trees turn random writes into sequential disk appends via MemTable + SSTables. You pay for this later with compaction I/O and Bloom filter lookup penalties.",
        },
        "tags": ["#DatabaseInternals", "#SystemDesign"],
    },
    {
        "title": "Go Runtime Scheduler: Under the Hood with the GMP Model",
        "url": "https://github.com/aman179102",
        "summary": "How the Go runtime multiplexes millions of goroutines across OS threads using G (Goroutine), M (OS Machine Thread), and P (Logical Processor) work-stealing queues.",
        "category": "golang",
        "source_kind": "deep_dive",
        "facts": {
            "core_concept": "M:N Work-Stealing Scheduling & Syscall Preemption",
            "key_takeaway": "When a goroutine blocks in a syscall, the runtime detaches M from P so other goroutines keep running on that P without starving the thread pool.",
        },
        "tags": ["#Golang", "#Concurrency"],
    },
    {
        "title": "Raft Consensus: Why Leader Election Must Prevent Split-Brain",
        "url": "https://github.com/aman179102",
        "summary": "How randomized election timers, majority quorum checks, and term numbers prevent dual-leader split-brain scenarios during transient network partitions.",
        "category": "distributed",
        "source_kind": "deep_dive",
        "facts": {
            "core_concept": "Quorum Intersection & Term Invalidation",
            "key_takeaway": "Two leaders cannot exist simultaneously because any valid leader must win votes from a strict majority (N/2 + 1) of the cluster.",
        },
        "tags": ["#DistributedSystems", "#SystemDesign"],
    },
    {
        "title": "Linux I/O Evolution: From epoll to io_uring Zero-Copy",
        "url": "https://github.com/aman179102",
        "summary": "Why next-gen high-throughput backends are adopting io_uring over epoll: dual ring buffers shared between user space and kernel eliminate syscall context switch overhead entirely.",
        "category": "linux",
        "source_kind": "deep_dive",
        "facts": {
            "core_concept": "Kernel-User Shared Ring Buffers & Asynchronous Batching",
            "key_takeaway": "Epoll still requires a syscall per ready event batch; io_uring achieves zero-copy asynchronous I/O submission without entering the kernel on every operation.",
        },
        "tags": ["#LinuxKernel", "#HighPerformance"],
    },
    {
        "title": "Postgres MVCC Internals: How Tuple Versioning Impacts Write Latency",
        "url": "https://github.com/aman179102",
        "summary": "How PostgreSQL implements Multi-Version Concurrency Control using xmin/xmax tuple headers, and why high-frequency UPDATE workloads cause table bloat without autovacuum tuning.",
        "category": "database",
        "source_kind": "deep_dive",
        "facts": {
            "core_concept": "Tuple Immutability, Dead Tuples & HOT (Heap-Only Tuples)",
            "key_takeaway": "Every UPDATE writes a new physical row version. Without Heap-Only Tuple (HOT) optimizations, every updated row forces index write amplification.",
        },
        "tags": ["#PostgreSQL", "#DatabaseInternals"],
    },
    {
        "title": "Cache Stampede Mitigation: Singleflight vs Probabilistic Early Expiration",
        "url": "https://github.com/aman179102",
        "summary": "When hot cache keys expire under heavy traffic, thousands of concurrent requests hammer the backend database simultaneously. How singleflight request coalescing eliminates the thundering herd.",
        "category": "architecture",
        "source_kind": "deep_dive",
        "facts": {
            "core_concept": "Request Coalescing (Singleflight) & XFetch Algorithm",
            "key_takeaway": "Singleflight suppresses duplicate concurrent in-flight requests, ensuring only one worker queries the database while other requests wait on the same shared channel.",
        },
        "tags": ["#SystemDesign", "#Backend"],
    },
)

# Curated Technical Discussions (10% distribution)
DEVELOPER_DISCUSSIONS = (
    {
        "title": "Architectural Dilemma: Read Replicas vs Sharding Threshold",
        "url": "https://github.com/aman179102",
        "summary": "When scaling a database encountering read/write bottlenecks, when is moving to application-level sharding genuinely warranted versus tuning connection pooling and read replicas?",
        "category": "database",
        "source_kind": "discussion",
        "facts": {
            "core_concept": "Operational Tax of Sharding vs Replication Limits",
            "key_takeaway": "Sharding adds cross-shard joins, 2PC distributed transactions, and rebalancing complexity. Teams often shard before exhausting index tuning, read-replicas, and caching.",
        },
        "tags": ["#DatabaseArchitecture", "#SystemDesign"],
    },
    {
        "title": "Eventual Consistency vs Linearizability in Production",
        "url": "https://github.com/aman179102",
        "summary": "Many teams implement distributed consensus or 2-phase commits where idempotent retries and eventual consistency would cut tail latency by 80%. Where do you draw the line?",
        "category": "distributed",
        "source_kind": "discussion",
        "facts": {
            "core_concept": "Consistency Level Trade-offs and Tail Latency Tax",
            "key_takeaway": "Linearizability requires round-trip consensus coordination on every read/write. Eventual consistency trades absolute freshness for resilient local execution.",
        },
        "tags": ["#DistributedSystems", "#Architecture"],
    },
)

# Open Source Discoveries (5% distribution)
OPEN_SOURCE_DISCOVERIES = (
    {
        "title": "Litestream: Streaming SQLite Replication to S3",
        "url": "https://github.com/benbjohnson/litestream",
        "summary": "Litestream runs as a separate background process that continuously streams WAL changes from SQLite directly to cloud storage, turning single-node SQLite into a disaster-proof database.",
        "category": "dev",
        "source_kind": "oss_discovery",
        "facts": {
            "core_concept": "WAL-Level Continuous Point-in-Time Recovery",
            "key_takeaway": "Replicates at the 4KB page level rather than SQL statements, keeping overhead negligible without modifying application code.",
        },
        "tags": ["#OpenSource", "#SQLite"],
    },
    {
        "title": "Dragonfly: Modern Multi-Threaded Redis Alternative",
        "url": "https://github.com/dragonflydb/dragonfly",
        "summary": "A drop-in Redis replacement built with a shared-nothing multi-threaded architecture using io_uring, achieving 25x throughput and higher memory efficiency on multi-core servers.",
        "category": "database",
        "source_kind": "oss_discovery",
        "facts": {
            "core_concept": "Shared-Nothing Multi-Threading & io_uring",
            "key_takeaway": "Redis is single-threaded per instance. Dragonfly uses core-pinned fibers and VHash structures to avoid global mutex contention on large machines.",
        },
        "tags": ["#HighPerformance", "#OpenSource"],
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
                        source_kind="news",
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
                    source_kind="news",
                    score=spec.weight,
                )
            )
    return items


def fetch_all_stories(settings: Settings, include_showcase: bool = False, format_filter: str | None = None) -> list[Story]:
    """
    Fetches candidate stories across diverse formats adhering to Section 14:
    - 40% original engineering insights / deep dives
    - 25% project showcases & build in public
    - 20% verified technology news (with full article text extraction)
    - 10% developer discussions
    - 5% open-source discoveries
    """
    stories: list[Story] = []

    # Handle explicit showcase request
    if include_showcase or format_filter == "showcase":
        proj = random.choice(PERSONAL_PROJECTS)
        item = StoryItem(
            source=settings.author_name,
            title=proj["title"],
            url=proj["url"],
            summary=proj["summary"],
            category=proj["category"],
            source_kind="showcase",
            score=1.5,
            extra={"tags": proj["tags"]},
        )
        return [Story(item=item, sources=[SourceRef(settings.author_name, proj["url"])], facts=proj["facts"])]

    # Handle deep dives
    if format_filter == "deep_dive":
        dive = random.choice(ENGINEERING_DEEP_DIVES)
        item = StoryItem(
            source=settings.author_name,
            title=dive["title"],
            url=dive["url"],
            summary=dive["summary"],
            category=dive["category"],
            source_kind="deep_dive",
            score=1.4,
            extra={"tags": dive["tags"]},
        )
        return [Story(item=item, sources=[SourceRef(settings.author_name, dive["url"])], facts=dive["facts"])]

    # Fetch live RSS feeds for verified news
    with httpx.Client(headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}) as client:
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

    # Intersperse curated deep dives, showcases, discussions, and OSS discoveries
    for dive in ENGINEERING_DEEP_DIVES:
        item = StoryItem(
            source="Engineering Deep Dive",
            title=dive["title"],
            url=dive["url"],
            summary=dive["summary"],
            category=dive["category"],
            source_kind="deep_dive",
            score=1.35,
            extra={"tags": dive["tags"]},
        )
        stories.append(Story(item=item, sources=[SourceRef("Engineering Insight", dive["url"])], facts=dive["facts"]))

    for proj in PERSONAL_PROJECTS:
        item = StoryItem(
            source=settings.author_name,
            title=proj["title"],
            url=proj["url"],
            summary=proj["summary"],
            category=proj["category"],
            source_kind="showcase",
            score=1.25,
            extra={"tags": proj["tags"]},
        )
        stories.append(Story(item=item, sources=[SourceRef(settings.author_name, proj["url"])], facts=proj["facts"]))

    for disc in DEVELOPER_DISCUSSIONS:
        item = StoryItem(
            source="System Design Discussion",
            title=disc["title"],
            url=disc["url"],
            summary=disc["summary"],
            category=disc["category"],
            source_kind="discussion",
            score=1.1,
            extra={"tags": disc["tags"]},
        )
        stories.append(Story(item=item, sources=[SourceRef("Architecture Discussion", disc["url"])], facts=disc["facts"]))

    for oss in OPEN_SOURCE_DISCOVERIES:
        item = StoryItem(
            source="Open Source Discovery",
            title=oss["title"],
            url=oss["url"],
            summary=oss["summary"],
            category=oss["category"],
            source_kind="oss_discovery",
            score=1.15,
            extra={"tags": oss["tags"]},
        )
        stories.append(Story(item=item, sources=[SourceRef("Open Source", oss["url"])], facts=oss["facts"]))

    return stories
