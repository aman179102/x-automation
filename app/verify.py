from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from app.ai import generate_json
from app.config import Settings

logger = logging.getLogger(__name__)

VERIFICATION_SYSTEM_PROMPT = """You are a rigorous Principal Software Engineer and Technical Fact-Checker.
Your task is to verify an automated draft post against the full text extracted from the original source article.

STRICT VERIFICATION CRITERIA (Zero Tolerance for Hallucinations):
1. FACTUAL GROUNDING: Every claim, statistic, version number, benchmark, model name, and performance metric in the draft MUST be directly substantiated by the source text.
2. NO CONFABULATION: If the draft invents capabilities, release dates, or combines facts with an unrelated topic, it must be REJECTED.
3. ACCURATE SCOPE: The draft must accurately reflect the scope, caveats, and limitations described in the article.
4. ENGINEERING RIGOR: Distinguish between confirmed facts from the article and general engineering commentary.

Return a JSON object strictly adhering to this schema:
{
  "passed": true or false,
  "confidence_score": float between 0.0 and 1.0,
  "verified_facts": ["list of facts confirmed in the source article"],
  "unsubstantiated_claims": ["list of any claims made in the draft that CANNOT be proven from the source"],
  "rejection_reason": "Explanation if passed is false, else empty string"
}"""


@dataclass
class VerificationResult:
    passed: bool
    confidence_score: float
    verified_facts: list[str] = field(default_factory=list)
    unsubstantiated_claims: list[str] = field(default_factory=list)
    rejection_reason: str = ""


def verify_draft_against_source(
    draft_body: str,
    article_title: str,
    article_text: str,
    settings: Settings,
) -> VerificationResult:
    """
    Rigorously verifies the candidate post against the actual source text.
    Rejects any ungrounded claim or fabricated detail.
    """
    if not draft_body.strip():
        return VerificationResult(
            passed=False,
            confidence_score=0.0,
            rejection_reason="Draft body is empty.",
        )

    # If source text is missing or too short, we cannot substantiate the claim
    if not article_text or len(article_text.split()) < 60:
        return VerificationResult(
            passed=False,
            confidence_score=0.0,
            rejection_reason="Source article text is unavailable or insufficient to substantiate claims.",
        )

    user_payload = {
        "candidate_draft": draft_body,
        "source_title": article_title,
        "source_article_excerpt": article_text[:3500],
    }

    raw = generate_json(
        settings,
        VERIFICATION_SYSTEM_PROMPT,
        f"Verify this candidate draft against the source text:\n{json.dumps(user_payload, indent=2)}",
    )

    if not raw or not isinstance(raw, dict):
        logger.warning("Fact verification model did not return valid JSON; failing safe.")
        return VerificationResult(
            passed=False,
            confidence_score=0.5,
            rejection_reason="Fact verification evaluation failed or timed out.",
        )

    passed = bool(raw.get("passed", False))
    score = float(raw.get("confidence_score", 0.0))
    unsubstantiated = [str(c) for c in raw.get("unsubstantiated_claims", []) if str(c).strip()]
    verified = [str(f) for f in raw.get("verified_facts", [])]
    reason = str(raw.get("rejection_reason", ""))

    if unsubstantiated:
        passed = False
        if not reason:
            reason = f"Draft contains unsubstantiated claims: {'; '.join(unsubstantiated)}"

    return VerificationResult(
        passed=passed,
        confidence_score=score,
        verified_facts=verified,
        unsubstantiated_claims=unsubstantiated,
        rejection_reason=reason,
    )
