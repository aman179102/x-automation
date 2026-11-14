import unittest
from bs4 import BeautifulSoup

from app.extractor import ExtractedArticle
from app.models import PostDraft, Story, StoryItem
from app.text import twitter_length
from app.image import _draw_clean_tech_card, validate_image_consistency
from app.config import Settings


class TestPublishingPipeline(unittest.TestCase):
    def test_twitter_length_with_urls(self):
        text = "Check out this project: https://github.com/aman179102/x-automation #OpenSource"
        # URL counts as 23 characters
        eff_len = twitter_length(text)
        self.assertLessEqual(eff_len, 280)

    def test_extracted_article_validation(self):
        valid = ExtractedArticle(
            url="https://example.com/blog/test",
            title="Test",
            text=" ".join(["word"] * 120),
            is_valid=True,
            word_count=120,
        )
        self.assertTrue(valid.is_valid)
        self.assertGreaterEqual(valid.word_count, 75)

        invalid = ExtractedArticle(
            url="https://example.com/blog/short",
            title="Short",
            text="Too short",
            is_valid=False,
            word_count=2,
        )
        self.assertFalse(invalid.is_valid)

    def test_clean_tech_card_rendering(self):
        settings = Settings()
        draft = PostDraft(
            body="Testing visual rendering for database deep dives.",
            image_title="LSM-Trees vs B+ Trees",
            image_facts=["Sequential Disk I/O vs Random Writes"],
            alt_text="Architecture diagram comparing storage engines",
            visual_prompt="minimal dark slate technical architecture",
            category="database",
            format_kind="deep_dive",
            technical_takeaway="Trades compaction read overhead for zero random write penalties.",
        )
        img = _draw_clean_tech_card(draft, settings)
        self.assertEqual(img.size, (1200, 675))


if __name__ == "__main__":
    unittest.main()
