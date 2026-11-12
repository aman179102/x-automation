from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

_URL_REGEX = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_HTML_TAG_REGEX = re.compile(r"<[^>]+>")
_WHITESPACE_REGEX = re.compile(r"\s+")
_SENTENCE_REGEX = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")


class _HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.reset()
        self.fed = []

    def handle_data(self, d):
        self.fed.append(d)

    def get_data(self):
        return "".join(self.fed)


def strip_html(html: str) -> str:
    if not html:
        return ""
    s = _HTMLStripper()
    s.feed(html)
    return unescape(s.get_data())


def normalize_space(text: str) -> str:
    if not text:
        return ""
    return _WHITESPACE_REGEX.sub(" ", text).strip()


def strip_urls(text: str) -> str:
    return normalize_space(_URL_REGEX.sub("", text))


def clean_markdown(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    text = re.sub(r"[*_`~]", "", text)
    return normalize_space(text)


def split_sentences(text: str) -> list[str]:
    text = normalize_space(text)
    if not text:
        return []
    parts = _SENTENCE_REGEX.split(text)
    return [p.strip() for p in parts if p.strip()]


def truncate_words(text: str, limit: int) -> str:
    words = normalize_space(text).split()
    if len(words) <= limit:
        return text
    return " ".join(words[:limit]) + "..."


def canonical_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        clean_query = [
            (k, v)
            for k, v in parse_qsl(parsed.query, keep_blank_values=True)
            if not k.startswith("utm_") and k not in {"ref", "source", "fbclid"}
        ]
        return urlunparse(
            (
                parsed.scheme.lower(),
                parsed.netloc.lower(),
                parsed.path.rstrip("/"),
                parsed.params,
                urlencode(clean_query),
                "",
            )
        )
    except Exception:
        return url


def domain(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""


def twitter_length(text: str) -> int:
    """
    Computes effective length for Twitter/X:
    - Every URL counts as 23 characters (t.co wrap).
    - Emojis/extended chars count as 2.
    - Standard characters count as 1.
    """
    urls = _URL_REGEX.findall(text)
    text_without_urls = _URL_REGEX.sub("", text)
    length = len(urls) * 23
    for ch in text_without_urls:
        # Check codepoint: outside basic plane or CJK/Emoji counts as 2
        code = ord(ch)
        if code > 0x1100 or code > 0x1F000:
            length += 2
        else:
            length += 1
    return length


def title_fingerprint(title: str) -> str:
    cleaned = re.sub(r"[^\w\s]", "", title.lower())
    words = [w for w in cleaned.split() if len(w) > 3]
    return " ".join(sorted(words[:8]))


def hashtags_for_category(category: str) -> list[str]:
    mapping = {
        "security": ["#CyberSecurity", "#InfoSec"],
        "ai": ["#AI", "#MachineLearning"],
        "dev": ["#SoftwareEngineering", "#DevCommunity"],
        "hardware": ["#Tech", "#Hardware"],
        "bigtech": ["#TechNews", "#Technology"],
        "startups": ["#Startups", "#Tech"],
        "showcase": ["#OpenSource", "#BuildInPublic", "#Golang"],
    }
    return mapping.get(category.lower(), ["#TechNews", "#Developers"])
