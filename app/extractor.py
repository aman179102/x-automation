from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


@dataclass
class ExtractedArticle:
    url: str
    title: str
    text: str
    published_date: Optional[str] = None
    author: Optional[str] = None
    is_valid: bool = False
    failure_reason: str = ""
    word_count: int = 0


def extract_article_content(url: str, timeout: float = 12.0) -> ExtractedArticle:
    """
    Fetches the original source web page and extracts full readable text,
    metadata, and author info for strict fact verification.
    """
    if not url or not url.startswith("http"):
        return ExtractedArticle(
            url=url,
            title="",
            text="",
            is_valid=False,
            failure_reason="Invalid URL",
        )

    try:
        with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=timeout) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                return ExtractedArticle(
                    url=url,
                    title="",
                    text="",
                    is_valid=False,
                    failure_reason=f"HTTP status {resp.status_code}",
                )
            html = resp.text
    except Exception as exc:
        logger.warning("Article extraction request failed for %s: %s", url, exc)
        return ExtractedArticle(
            url=url,
            title="",
            text="",
            is_valid=False,
            failure_reason=f"Network error: {exc}",
        )

    soup = BeautifulSoup(html, "lxml")

    # Remove non-content tags
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "noscript", "svg", "form"]):
        tag.decompose()

    # Extract title
    title = ""
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        title = str(og_title["content"]).strip()
    elif soup.title and soup.title.string:
        title = soup.title.string.strip()

    # Extract publish date
    pub_date: Optional[str] = None
    for date_attr in ["article:published_time", "og:pubdate", "pubdate"]:
        meta = soup.find("meta", property=date_attr) or soup.find("meta", attrs={"name": date_attr})
        if meta and meta.get("content"):
            pub_date = str(meta["content"]).strip()
            break

    # Locate main article body
    article_elem = (
        soup.find("article")
        or soup.find("main")
        or soup.find(attrs={"role": "main"})
        or soup.find("div", class_=re.compile(r"(post-content|article-body|entry-content|markdown-body)", re.I))
    )

    if article_elem:
        paragraphs = [p.get_text(" ", strip=True) for p in article_elem.find_all(["p", "h2", "h3", "li"])]
    else:
        paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all("p")]

    # Filter out empty or cookie-banner paragraphs
    substantive_paras: list[str] = []
    for p in paragraphs:
        cleaned = re.sub(r"\s+", " ", p).strip()
        if len(cleaned.split()) >= 6 and not any(
            noise in cleaned.lower()
            for noise in ["cookie", "privacy policy", "terms of use", "subscribe to our newsletter", "sign up"]
        ):
            substantive_paras.append(cleaned)

    full_text = "\n\n".join(substantive_paras)
    words = full_text.split()
    word_count = len(words)

    if word_count < 75:
        return ExtractedArticle(
            url=url,
            title=title,
            text=full_text,
            published_date=pub_date,
            is_valid=False,
            failure_reason="Substantive content too short or gated behind auth/paywall",
            word_count=word_count,
        )

    # Truncate to first 2500 words to keep reasoning prompt fast and focused
    if word_count > 2500:
        full_text = " ".join(words[:2500]) + "..."
        word_count = 2500

    return ExtractedArticle(
        url=url,
        title=title,
        text=full_text,
        published_date=pub_date,
        is_valid=True,
        word_count=word_count,
    )
