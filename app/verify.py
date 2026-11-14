from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.ai import generate_json
from app.config import Settings

logger = logging.getLogger(__name__)

VERIFICATION_SYSTEM_PROMPT = """You are a Principal Software Engineer and Technical Fact-Checker.
Your task is to verify an automated draft post against the full text extracted from the original source article.

EVIDENCE-BACKED FACT CHECKING RULES:
1. Deconstruct the candidate draft into individual material factual claims (numbers, versions, benchmarks, product names, capabilities, licenses, limitations).
2. For every factual claim, you MUST locate and provide the EXACT quote from the source article that confirms it.
3. If any claim is an inference or analysis, label it as "ANALYSIS". Analysis is allowed only if it does not contradict facts.
4. If a claim makes a specific factual statement (e.g., version number, benchmark, release status) that is NOT substantiated by the source text, list it under "unsupported_claims".
5. If a claim contradicts the source text, list it under "contradicted_claims".

Return a JSON object strictly following this schema:
{
  "claims": [
    {
      "claim": "Claim text from draft",
      "exact_quote_from_source": "Verbatim quote from article text or empty if unsupported",
      "status": "VERIFIED" or "UNSUPPORTED" or "CONTRADICTED" or "ANALYSIS"
    }
  ],
  "unsupported_claims": ["list of any claims that lack source evidence"],
  "contradicted_claims": ["list of any claims that directly contradict the article"],
  "confidence_score": 0.95,
  "rejection_reason": "Detailed reason if rejected, else empty string"
}"""


@dataclass
class VerificationResult:
    passed: bool
    confidence_score: float
    verified_claims: list[dict[str, str]] = field(default_factory=list)
    unsubstantiated_claims: list[str] = field(default_factory=list)
    contradicted_claims: list[str] = field(default_factory=list)
    rejection_reason: str = ""


def _normalize_for_match(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def verify_draft_against_source(
    draft_body: str,
    article_title: str,
    article_text: str,
    settings: Settings,
) -> VerificationResult:
    """
    Rigorously verifies every material factual claim against the extracted source text.
    Uses evidence-backed quote verification: ensures quotes actually exist in source.
    STRICTLY FAIL-CLOSED: Rejects if evidence is insufficient or service fails.
    """
    if not draft_body.strip():
        return VerificationResult(
            passed=False,
            confidence_score=0.0,
            rejection_reason="Draft body is empty.",
        )

    # If source text is missing or too short, we cannot substantiate claims
    if not article_text or len(article_text.split()) < 75:
        return VerificationResult(
            passed=False,
            confidence_score=0.0,
            rejection_reason="Source article text is unavailable or too brief (< 75 words) to substantiate claims.",
        )

    # Use up to 12,000 characters of substantive article text to avoid ignoring middle/end content
    source_context = article_text[:12000]

    user_payload = {
        "candidate_draft": draft_body,
        "source_title": article_title,
        "source_article_full_text": source_context,
    }

    raw = generate_json(
        settings,
        VERIFICATION_SYSTEM_PROMPT,
        f"Verify this candidate draft against the source article:\n{json.dumps(user_payload, indent=2)}",
    )

    # Fail-closed if model response is missing
    if not raw or not isinstance(raw, dict):
        logger.error("Fact verification model failed or returned invalid output. Rejecting (fail-closed).")
        return VerificationResult(
            passed=False,
            confidence_score=0.0,
            rejection_reason="Fact verification evaluation failed or timed out (fail-closed enforced).",
        )

    unsupported = [str(c) for c in raw.get("unsupported_claims", []) if str(c).strip()]
    contradicted = [str(c) for c in raw.get("contradicted_claims", []) if str(c).strip()]
    claims_list = raw.get("claims", [])
    confidence = float(raw.get("confidence_score", 0.0))
    rejection_reason = str(raw.get("rejection_reason", ""))

    # Cross-verify quotes in article text (Do not treat LLM self-assessment as blind proof)
    normalized_source = _normalize_for_match(source_context)
    verified_claims: list[dict[str, str]] = []

    for item in claims_list:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status", "")).upper()
        claim_text = str(item.get("claim", ""))
        quote = str(item.get("exact_quote_from_source", "")).strip()

        if status == "VERIFIED":
            if quote:
                norm_quote = _normalize_for_match(quote)
                # Check if quote exists in the source text (first 40 chars minimum)
                match_target = norm_quote[:50] if len(norm_quote) >= 50 else norm_quote
                if match_target and match_target in normalized_source:
                    verified_claims.append({"claim": claim_text, "quote": quote})
                else:
                    unsupported.append(f"Claim quote not found in source text: '{quote[:80]}...'")
            else:
                unsupported.append(f"Claim marked verified without supporting quote: '{claim_text}'")
        elif status in ("UNSUPPORTED", "CONTRADICTED"):
            unsupported.append(claim_text)

    # Enforce strict rejection triggers
    if unsupported or contradicted:
        reasons = []
        if unsupported:
            reasons.append(f"Unsupported claims: {'; '.join(unsupported)}")
        if contradicted:
            reasons.append(f"Contradicted claims: {'; '.join(contradicted)}")
        return VerificationResult(
            passed=False,
            confidence_score=min(confidence, 0.5),
            verified_claims=verified_claims,
            unsubstantiated_claims=unsupported,
            contradicted_claims=contradicted,
            rejection_reason=" | ".join(reasons),
        )

    if confidence < 0.85:
        return VerificationResult(
            passed=False,
            confidence_score=confidence,
            verified_claims=verified_claims,
            rejection_reason=f"Verification confidence {confidence:.2f} below required threshold 0.85.",
        )

    return VerificationResult(
        passed=True,
        confidence_score=confidence,
        verified_claims=verified_claims,
        rejection_reason="",
    )
