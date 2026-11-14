import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.config import Settings
from app.engagement import EngagementCandidate, draft_reply, validate_reply_quality
from app.image import (
    _draw_clean_tech_card,
    render_branded_image,
    validate_image_consistency,
)
from app.models import PostDraft, Story, StoryItem
from app.quality import evaluate_quality_gate
from app.sources import PERSONAL_PROJECTS
from app.text import twitter_length
from app.verify import verify_draft_against_source


class TestSecondPassCorrectness(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(
            author_name="Aman Kumar",
            author_handle="@aman179102",
            author_title="Systems & AI Engineer",
            output_dir=Path("/tmp/test_output"),
        )
        self.settings.output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Quality Gate Fail-Closed Test
    @patch("app.quality.generate_json")
    def test_quality_gate_fail_closed_on_model_error(self, mock_gen):
        """When evaluation model fails or returns None, quality gate must REJECT (fail-closed)."""
        mock_gen.return_value = None  # Model timeout or crash
        draft = PostDraft(
            body="PostgreSQL writes dead tuples on UPDATE without HOT.",
            image_title="Postgres MVCC",
            image_facts=["Dead Tuples"],
            alt_text="Postgres MVCC",
            format_kind="deep_dive",
        )
        res = evaluate_quality_gate(draft, draft.body, [], self.settings)
        self.assertFalse(res.passed)
        self.assertEqual(res.score, 0.0)
        self.assertTrue(any("fail-closed" in i.lower() for i in res.issues))

    @patch("app.quality.generate_json")
    def test_quality_gate_rejects_on_critical_flaws(self, mock_gen):
        """LLM reported critical issues must block approval even if overall score was claimed high."""
        mock_gen.return_value = {
            "scores": {
                "factual_accuracy": 9.0,
                "technical_relevance": 9.0,
                "engineering_value": 8.5,
                "writing_quality": 8.5,
                "audience_relevance": 9.0,
                "non_repetitive": 9.0,
            },
            "overall_score": 8.9,
            "critical_flaws": ["Factual inaccuracy: Model version conflated with competitor."],
            "minor_feedback": [],
            "verdict": "Has severe error.",
        }
        draft = PostDraft(body="Valid body text under budget", image_title="Title", image_facts=["F"], alt_text="Alt")
        res = evaluate_quality_gate(draft, draft.body, [], self.settings)
        self.assertFalse(res.passed)
        self.assertIn("Factual inaccuracy: Model version conflated with competitor.", res.issues)

    # 2. Fact Verification Strict Grounding & Evidence Tests
    @patch("app.verify.generate_json")
    def test_fact_verification_rejects_unsupported_claims(self, mock_gen):
        """Unsupported claims without source quotes must cause verification rejection."""
        mock_gen.return_value = {
            "claims": [
                {
                    "claim": "AlphaProof verified Signal's encryption core.",
                    "exact_quote_from_source": "",
                    "status": "UNSUPPORTED",
                }
            ],
            "unsupported_claims": ["AlphaProof verified Signal's encryption core."],
            "contradicted_claims": [],
            "confidence_score": 0.30,
            "rejection_reason": "Not mentioned in article.",
        }
        article_text = "Google DeepMind solved Olympiad geometry problems with AlphaProof in Lean 4." * 10
        res = verify_draft_against_source(
            "AlphaProof verified Signal's encryption core.",
            "AlphaProof Announcement",
            article_text,
            self.settings,
        )
        self.assertFalse(res.passed)
        self.assertIn("AlphaProof verified Signal's encryption core.", res.unsubstantiated_claims)

    @patch("app.verify.generate_json")
    def test_fact_verification_rejects_low_confidence(self, mock_gen):
        """Confidence score below 0.85 must reject verification."""
        mock_gen.return_value = {
            "claims": [
                {
                    "claim": "Go 1.22 trace overhaul",
                    "exact_quote_from_source": "The runtime/trace package contains a powerful tool",
                    "status": "VERIFIED",
                }
            ],
            "unsupported_claims": [],
            "contradicted_claims": [],
            "confidence_score": 0.70,  # Below 0.85
            "rejection_reason": "",
        }
        article_text = "The runtime/trace package contains a powerful tool for understanding Go programs." * 10
        res = verify_draft_against_source(
            "Go 1.22 trace overhaul",
            "Go Tracing",
            article_text,
            self.settings,
        )
        self.assertFalse(res.passed)
        self.assertIn("below required threshold", res.rejection_reason)

    # 3. Image Semantic Consistency Tests
    def test_image_semantic_consistency_rejects_mismatch(self):
        """Card headline that shares no technical overlap with the post must be rejected."""
        # Create an actual image file
        card_path = self.settings.output_dir / "mismatch_test.jpg"
        draft = PostDraft(
            body="Understanding LSM-tree write amplification in RocksDB.",
            image_title="Frontend CSS Flexbox Grid Tricks",  # Completely unrelated topic
            image_facts=["Flexbox"],
            alt_text="CSS flexbox",
            category="database",
        )
        _draw_clean_tech_card(draft, self.settings).save(str(card_path), "JPEG")

        is_valid, reason = validate_image_consistency(draft, card_path)
        self.assertFalse(is_valid)
        self.assertIn("Semantic mismatch", reason)

    def test_news_image_format_triggers_visual_generation(self):
        """Unified 'news' format must properly route through visual generator."""
        draft = PostDraft(
            body="Cloudflare open sources Kitesurf browser engine under Apache 2.0.",
            image_title="Cloudflare Kitesurf Engine",
            image_facts=["Apache 2.0"],
            alt_text="Kitesurf diagram",
            format_kind="news",
            category="dev",
            has_image=True,
        )
        out_path = self.settings.output_dir / "news_img_test.jpg"
        result_path = render_branded_image(draft, self.settings, out_path)
        self.assertIsNotNone(result_path)
        self.assertTrue(result_path.exists())

    # 4. Personal Projects Exact URL & License Audit Tests
    def test_personal_projects_verified_urls_and_licenses(self):
        """All personal projects must have exact repository URLs and correct licenses."""
        expected_urls = {
            "https://github.com/aman179102/distributed-file-storage-service",
            "https://github.com/aman179102/real-time-analytics-pipeline",
            "https://github.com/aman179102/podvoice",
        }
        actual_urls = {p["url"] for p in PERSONAL_PROJECTS}
        self.assertEqual(expected_urls, actual_urls)

        # Verify distributed-file-storage-service is NOT labeled open source
        storage_proj = next(p for p in PERSONAL_PROJECTS if "storage" in p["title"].lower())
        self.assertFalse(storage_proj["is_open_source"])
        # Ensure Raft is NOT claimed
        self.assertNotIn("raft", storage_proj["facts"]["tech"].lower())

        # Verify analytics pipeline has no fabricated 'sub-millisecond' benchmarks
        analytics_proj = next(p for p in PERSONAL_PROJECTS if "analytics" in p["title"].lower())
        self.assertTrue(analytics_proj["is_open_source"])
        self.assertNotIn("sub-millisecond", analytics_proj["summary"].lower())

    # 5. Engagement Fail-Closed & Validation Tests
    @patch("app.engagement.generate_json")
    def test_draft_reply_disabled_fallback_on_ai_failure(self, mock_gen):
        """When AI generation fails, draft_reply MUST return empty string, never a canned fallback."""
        mock_gen.return_value = None  # AI failed
        reply, reason = draft_reply("What are the bottlenecks of epoll in Linux?", self.settings)
        self.assertEqual(reply, "")
        self.assertIn("failed", reason.lower())

    def test_validate_reply_quality_rejects_canned_phrases(self):
        """Rejects repetitive catchphrases and generic praise."""
        cand = EngagementCandidate(
            tweet_id="123",
            author="torvalds",
            author_id="456",
            text="Discussing Linux memory mapping.",
            likes=10,
            created_at="",
        )
        # Test generic praise rejection
        valid, reason = validate_reply_quality(cand, "Great post! Really agree with your point.", {})
        self.assertFalse(valid)
        self.assertIn("generic opening", reason.lower())

        # Test repetitive canned phrase loop rejection
        valid, reason = validate_reply_quality(
            cand,
            "The hidden cost is tail latency under partial network partitions.",
            {},
        )
        self.assertFalse(valid)
        self.assertIn("repetitive", reason.lower())

    # 6. Telegram 1-Tap Mobile Co-Pilot Tests
    def test_telegram_keyboard_builds_mobile_intent(self):
        """Telegram keyboard must generate accurate Twitter mobile web intent URL."""
        from app.telegram_bot import build_telegram_keyboard, format_telegram_caption

        text = "Testing distributed systems with 4+2 Reed-Solomon."
        kb = build_telegram_keyboard(
            tweet_text=text,
            primary_source_url="https://github.com/aman179102",
            portfolio_url="https://github.com/aman179102",
        )
        self.assertIn("inline_keyboard", kb)
        intent_btn = kb["inline_keyboard"][0][0]
        self.assertIn("x.com/intent/post", intent_btn["url"])
        self.assertIn("Reed-Solomon", intent_btn["url"])

        # Test caption formatting stays under 1024 characters
        draft = PostDraft(body=text, image_title="Storage", image_facts=["RS"], alt_text="Alt", format_kind="deep_dive")
        caption = format_telegram_caption(draft, text, 9.2)
        self.assertLessEqual(len(caption), 1024)
        self.assertIn("DEEP_DIVE", caption)
        self.assertIn("9.2", caption)

    # 7. Satellite Engine Matching & Endorsement Tests
    @patch("app.satellite.generate_json")
    def test_satellite_project_matching_and_endorsement(self, mock_gen):
        """Satellite account matches relevant projects and endorses @aman179102."""
        from app.satellite import match_project_to_tweet, draft_satellite_endorsement

        # Storage matching
        tweet = "Looking for good architecture examples of Reed-Solomon chunk deduplication in distributed storage."
        proj = match_project_to_tweet(tweet)
        self.assertIsNotNone(proj)
        self.assertEqual(proj["name"], "distributed-file-storage-service")

        # Mock AI endorsement
        mock_gen.return_value = {
            "reply": "Deduplication before erasure coding saves disk space. @aman179102 has a clean reference: https://github.com/aman179102/distributed-file-storage-service",
            "reasoning": "Directly matches RS deduplication question.",
        }
        reply, reason = draft_satellite_endorsement(tweet, "sysdesigner", proj, self.settings)
        self.assertIn("@aman179102", reply)
        self.assertIn("distributed-file-storage-service", reply)
        self.assertLessEqual(len(reply), 280)


if __name__ == "__main__":
    unittest.main()
