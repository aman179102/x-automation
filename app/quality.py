from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.ai import generate_json
from app.config import Settings
from app.models import PostDraft
from app.text import twitter_length

logger = logging.getLogger(__name__)

QUALITY_GATE_PROMPT = """You are the Senior Technical Editor evaluating an engineering post for @aman179102 before it can be published to X.

The author is a backend, systems, and AI engineer. The account must NEVER read like a generic bot or news-headline rewrites.

EVALUATION CRITERIA (Score each out of 10):
A. Factual Accuracy: Are all facts, numbers, versions, and claims substantiated?
B. Technical Relevance: Is the topic deeply relevant to software engineering, backend, databases, Go/Python, distributed systems, or AI engineering?
C. Original Engineering Value: Does it provide genuine engineering takeaways, architectural trade-offs, or insights rather than mere news reporting?
D. Writing Quality: Is the writing crisp, punchy, conversational, and free of clickbait, buzzwords, or ALL-CAPS?
E. Audience Relevance: Would experienced software engineers, founders, or maintainers genuinely find this worth reading?
F. Non-Repetitive Tone: Does it avoid repetitive filler ("Breaking news", "Developers listen up", generic catchphrases)?

Return JSON strictly following this schema:
{
  "scores": {
    "factual_accuracy": 9.0,
    "technical_relevance": 9.0,
    "engineering_value": 8.5,
    "writing_quality": 9.0,
    "audience_relevance": 9.0,
    "non_repetitive": 9.0
  },
  "overall_score": 8.9,
  "passed": true,
  "issues": ["list of any concerns or false claims"],
  "verdict": "Detailed editorial assessment"
}"""


@dataclass
class QualityGateResult:
    score: float
    passed: bool
    breakdown: dict[str, float] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    verdict: str = ""


def evaluate_quality_gate(
    draft: PostDraft,
    tweet_text: str,
    recent_history: list[dict[str, Any]],
    settings: Settings,
    has_image: bool = True,
) -> QualityGateResult:
    """
    Evaluates the complete post against the 10-point Quality Gate.
    Enforces a strict minimum threshold of 8.0/10.0.
    """
    issues: list[str] = []

    # 1. Platform constraint checks (Criterion J)
    char_len = twitter_length(tweet_text)
    if char_len > 280:
        issues.append(f"Exceeds Twitter 280-char limit (Length: {char_len}).")

    if not draft.body.strip():
        issues.append("Post body is empty.")

    # 2. Check for clickbait and forbidden introductory buzzwords (Criterion D)
    forbidden_openers = [
        r"^breaking\s+news",
        r"^developers,\s+listen\s+up",
        r"^huge\s+announcement",
        r"^in\s+an\s+exciting\s+update",
        r"^game\s+changer",
    ]
    for pattern in forbidden_openers:
        if re.search(pattern, draft.body.strip(), re.IGNORECASE):
            issues.append(f"Forbidden sensational opener detected matching '{pattern}'.")

    # 3. Check for repetitive content against recent history (Criterion H)
    recent_texts = [p.get("text", "").lower() for p in recent_history[-10:]]
    draft_lower = draft.body.lower()
    for past_text in recent_texts:
        # Check similarity of first 30 chars
        if past_text and draft_lower[:35] in past_text:
            issues.append("Post opening is too similar to a recently published post.")
            break

    # 4. Source attribution check (Criterion I) - Required strictly for news
    if draft.story and draft.story.source_kind == "news":
        if "http" not in tweet_text:
            issues.append("Original primary source link is missing from news post.")

    # If algorithmic checks failed catastrophically, reject immediately
    if any("Exceeds Twitter" in i or "Forbidden sensational" in i for i in issues):
        return QualityGateResult(
            score=5.0,
            passed=False,
            breakdown={"compliance": 4.0},
            issues=issues,
            verdict="Rejected due to compliance or format failure.",
        )

    # 5. LLM Senior Editorial Evaluation
    user_payload = {
        "tweet_text": tweet_text,
        "category": draft.category,
        "is_showcase": draft.story.is_showcase if draft.story else False,
        "headline": draft.image_title,
        "recent_post_snippets": [p.get("text", "")[:70] for p in recent_history[-5:]],
    }

    raw = generate_json(
        settings,
        QUALITY_GATE_PROMPT,
        f"Evaluate this tech post for quality gate:\n{json.dumps(user_payload, indent=2)}",
    )

    if not raw or not isinstance(raw, dict):
        logger.warning("Quality gate LLM evaluation failed; using safe heuristic score.")
        base_score = 8.5 if not issues else 6.5
        return QualityGateResult(
            score=base_score,
            passed=len(issues) == 0,
            breakdown={"heuristic": base_score},
            issues=issues,
            verdict="Passed heuristic quality checks." if not issues else "Failed heuristic checks.",
        )

    score = float(raw.get("overall_score", 0.0))
    passed = bool(raw.get("passed", False)) and (score >= 8.0) and (len(issues) == 0)
    scores_dict = raw.get("scores", {})
    llm_issues = [str(x) for x in raw.get("issues", []) if str(x).strip()]
    issues.extend(llm_issues)
    verdict = str(raw.get("verdict", ""))

    return QualityGateResult(
        score=score,
        passed=passed,
        breakdown=scores_dict,
        issues=issues,
        verdict=verdict,
    )
