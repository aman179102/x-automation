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

QUALITY_GATE_PROMPT = """You are the Senior Technical Editor evaluating an engineering post for @aman179102 before publication to X.

The author is a senior systems and AI engineer. The account must NEVER publish generic news aggregations, sensational clickbait, or unverified claims.

EVALUATION CRITERIA (Score each out of 10.0):
1. factual_accuracy: Are all numbers, metrics, versions, and claims substantiated? (Minimum: 8.5)
2. technical_relevance: Is it deeply relevant to systems, backend, databases, Go/Python, distributed systems, or AI engineering? (Minimum: 8.0)
3. engineering_value: Does it provide genuine architectural trade-offs, internal mechanics, or practical takeaways rather than just rewording headlines? (Minimum: 8.0)
4. writing_quality: Is it crisp, concise, conversational, with zero buzzwords, no ALL-CAPS, and natural phrasing? (Minimum: 8.0)
5. audience_relevance: Would senior software engineers and architects find this valuable? (Minimum: 8.0)
6. non_repetitive: Does it avoid repetitive filler and generic catchphrases? (Minimum: 8.0)

CRITICAL REJECTION TRIGGERS:
- Any unsubstantiated metric, fabricated benchmark, or unverified claim.
- Generic press-release language or pure headline rewrites with no engineering insight.
- Sensationalist hooks ("Breaking news", "Huge announcement", "Game changer").

Return JSON strictly adhering to this schema:
{
  "scores": {
    "factual_accuracy": 9.0,
    "technical_relevance": 9.0,
    "engineering_value": 8.5,
    "writing_quality": 8.5,
    "audience_relevance": 9.0,
    "non_repetitive": 9.0
  },
  "overall_score": 8.8,
  "critical_flaws": ["list of any fatal errors: factual errors, headline rewrites, clickbait"],
  "minor_feedback": ["list of minor suggestions"],
  "verdict": "Detailed editorial assessment"
}"""

# Strict minimum thresholds per criterion
MIN_CRITERIA_THRESHOLDS = {
    "factual_accuracy": 8.5,
    "technical_relevance": 8.0,
    "engineering_value": 8.0,
    "writing_quality": 8.0,
    "audience_relevance": 8.0,
    "non_repetitive": 8.0,
}
MIN_OVERALL_SCORE = 8.5


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
    Rigorously evaluates the candidate post against the 10-point Quality Gate.
    STRICTLY FAIL-CLOSED: If the evaluation model fails or returns invalid data,
    the post is REJECTED (score = 0.0, passed = False).
    """
    issues: list[str] = []

    # 1. Platform constraint checks
    char_len = twitter_length(tweet_text)
    if char_len > 280:
        issues.append(f"Exceeds Twitter 280-char limit (Length: {char_len}).")

    if not draft.body.strip():
        issues.append("Post body is empty.")

    # 2. Check for clickbait and forbidden introductory buzzwords
    forbidden_openers = [
        r"^breaking\s+news",
        r"^developers,\s+listen\s+up",
        r"^huge\s+announcement",
        r"^in\s+an\s+exciting\s+update",
        r"^game\s+changer",
        r"^must-read",
    ]
    for pattern in forbidden_openers:
        if re.search(pattern, draft.body.strip(), re.IGNORECASE):
            issues.append(f"Forbidden sensational opener detected matching '{pattern}'.")

    # 3. Check for repetitive content against recent history
    recent_texts = [p.get("text", "").lower() for p in recent_history[-10:]]
    draft_lower = draft.body.lower()
    for past_text in recent_texts:
        if past_text and draft_lower[:35] in past_text:
            issues.append("Post opening is too similar to a recently published post.")
            break

    # 4. Source attribution check (strictly required for news)
    if draft.story and draft.story.source_kind == "news":
        if "http" not in tweet_text:
            issues.append("Original primary source link is missing from news post.")

    # 5. LLM Senior Editorial Evaluation (FAIL-CLOSED)
    user_payload = {
        "tweet_text": tweet_text,
        "format_kind": draft.format_kind,
        "category": draft.category,
        "is_showcase": draft.story.is_showcase if draft.story else False,
        "headline": draft.image_title,
        "technical_takeaway": draft.technical_takeaway,
        "recent_post_snippets": [p.get("text", "")[:70] for p in recent_history[-5:]],
    }

    raw = generate_json(
        settings,
        QUALITY_GATE_PROMPT,
        f"Evaluate this tech post for quality gate:\n{json.dumps(user_payload, indent=2)}",
    )

    # STRICT FAIL-CLOSED RULE: If LLM fails or times out, NEVER approve!
    if not raw or not isinstance(raw, dict):
        logger.error("Quality gate evaluation model failed or timed out. REJECTING (fail-closed).")
        issues.append("Quality gate evaluator service unavailable or timed out (fail-closed enforced).")
        return QualityGateResult(
            score=0.0,
            passed=False,
            breakdown={},
            issues=issues,
            verdict="Rejected: Evaluation service failed or timed out (fail-closed).",
        )

    # Merge all LLM reported issues BEFORE computing final passed status
    critical_flaws = [str(x) for x in raw.get("critical_flaws", []) if str(x).strip()]
    minor_feedback = [str(x) for x in raw.get("minor_feedback", []) if str(x).strip()]
    issues.extend(critical_flaws)

    scores_dict = raw.get("scores", {})
    if not isinstance(scores_dict, dict):
        scores_dict = {}

    # Validate individual criteria thresholds
    criteria_failures: list[str] = []
    for criterion, min_thresh in MIN_CRITERIA_THRESHOLDS.items():
        val = float(scores_dict.get(criterion, 0.0))
        if val < min_thresh:
            criteria_failures.append(f"{criterion} scored {val:.1f} (minimum required: {min_thresh:.1f})")

    if criteria_failures:
        issues.extend(criteria_failures)

    overall_score = float(raw.get("overall_score", 0.0))
    verdict = str(raw.get("verdict", ""))

    # Final determination: Must have NO critical flaws, meet overall score, and satisfy all criteria
    passed = (
        len(issues) == 0
        and overall_score >= MIN_OVERALL_SCORE
        and len(criteria_failures) == 0
    )

    return QualityGateResult(
        score=overall_score,
        passed=passed,
        breakdown=scores_dict,
        issues=issues,
        verdict=verdict,
    )
