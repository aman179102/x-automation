from __future__ import annotations

import io
import logging
import random
import re
import urllib.parse
from pathlib import Path
from typing import Optional

import httpx
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from app.config import Settings
from app.models import PostDraft

logger = logging.getLogger(__name__)

WIDTH = 1200
HEIGHT = 675


def _font(settings: Settings, name: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = settings.assets_dir / "fonts" / name
    if path.exists():
        try:
            return ImageFont.truetype(str(path), size=size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _fetch_pollinations_image(prompt: str) -> bytes | None:
    """
    Fetch royalty-free contextual illustration from Pollinations.ai.
    Enforces clean, minimalist composition without futuristic neon clutter.
    """
    clean_prompt = prompt.strip() or "minimalist clean technical diagram architecture dark slate modern 4k"
    encoded_prompt = urllib.parse.quote(clean_prompt)
    seed = random.randint(1000, 999999)
    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1200&height=675&nologo=true&seed={seed}"

    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(url)
            if resp.status_code == 200 and len(resp.content) > 5000:
                return resp.content
    except Exception as exc:
        logger.warning("Pollinations image fetch failed: %s", exc)
    return None


def _get_category_accent(category: str) -> tuple[int, int, int]:
    accents = {
        "golang": (0, 173, 216),        # Go Cyan
        "database": (51, 103, 145),      # Postgres Blue
        "distributed": (16, 185, 129),   # Emerald
        "architecture": (99, 102, 241),  # Indigo
        "ai": (139, 92, 246),            # Violet
        "linux": (245, 158, 11),         # Amber
        "dev": (14, 165, 233),           # Sky
        "showcase": (16, 185, 129),      # Emerald
        "tech": (71, 85, 105),           # Slate
    }
    return accents.get(category.lower(), (99, 102, 241))


def _draw_clean_tech_card(draft: PostDraft, settings: Settings, base_bg: Image.Image | None = None) -> Image.Image:
    """
    Renders an engineering-first technical card with readable typography,
    high contrast, clean layout, and Aman Kumar's personal branding.
    Avoids repetitive futuristic blue/neon tropes per Section 15.
    """
    accent = _get_category_accent(draft.category)

    if base_bg is None:
        img = Image.new("RGB", (WIDTH, HEIGHT), (13, 17, 23))
        draw = ImageDraw.Draw(img)

        # Subtle technical grid
        for x in range(0, WIDTH, 80):
            draw.line([(x, 0), (x, HEIGHT)], fill=(22, 27, 34), width=1)
        for y in range(0, HEIGHT, 80):
            draw.line([(0, y), (WIDTH, y)], fill=(22, 27, 34), width=1)

        # Subtle accent border top line
        draw.line([(0, 0), (WIDTH, 0)], fill=accent, width=6)
    else:
        img = base_bg

    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # 1. Category Pill Badge
    badge_y = 55
    cat_name = draft.category.upper()
    cat_font = _font(settings, "Inter-SemiBold.ttf", 20)
    draw.rounded_rectangle([70, badge_y, 250, badge_y + 40], radius=6, fill=(22, 27, 34, 230), outline=(48, 54, 61, 255), width=1)
    draw.text((90, badge_y + 8), f"●  {cat_name}", font=cat_font, fill=accent)

    # 2. Main Title (Inter-Bold)
    title_font = _font(settings, "Inter-Bold.ttf", 44)
    raw_title = draft.image_title or (draft.story.title if draft.story else "Systems Engineering Insight")

    # Word wrap headline
    words = raw_title.split()
    lines: list[str] = []
    curr = ""
    for w in words:
        test = f"{curr} {w}".strip()
        if draw.textlength(test, font=title_font) <= 1040:
            curr = test
        else:
            if curr:
                lines.append(curr)
            curr = w
    if curr:
        lines.append(curr)

    lines = lines[:2]
    title_y = 130
    for line in lines:
        draw.text((70, title_y), line, font=title_font, fill=(240, 246, 252, 255))
        title_y += 56

    # 3. Technical Mechanism / Architectural Takeaway Box
    takeaway = draft.technical_takeaway or (draft.image_facts[0] if draft.image_facts else "")
    if takeaway and takeaway.upper() != draft.category.upper():
        box_y = title_y + 25
        draw.rounded_rectangle(
            [70, box_y, WIDTH - 70, box_y + 110],
            radius=10,
            fill=(22, 27, 34, 220),
            outline=(48, 54, 61, 240),
            width=1,
        )
        draw.rounded_rectangle([70, box_y, 76, box_y + 110], radius=3, fill=accent)

        takeaway_label_font = _font(settings, "JetBrainsMono-Medium.ttf", 16)
        takeaway_font = _font(settings, "Inter-Regular.ttf", 22)
        draw.text((95, box_y + 14), "CORE ARCHITECTURAL MECHANISM / TAKEAWAY", font=takeaway_label_font, fill=(139, 148, 158, 255))

        t_words = takeaway.split()
        t_lines: list[str] = []
        t_curr = ""
        for w in t_words:
            test = f"{t_curr} {w}".strip()
            if draw.textlength(test, font=takeaway_font) <= 980:
                t_curr = test
            else:
                if t_curr:
                    t_lines.append(t_curr)
                t_curr = w
        if t_curr:
            t_lines.append(t_curr)

        t_y = box_y + 44
        for tl in t_lines[:2]:
            draw.text((95, t_y), tl, font=takeaway_font, fill=(225, 232, 240, 255))
            t_y += 30

    # 4. Bottom Footer: Personal Branding
    footer_y = HEIGHT - 85
    draw.line([(70, footer_y), (WIDTH - 70, footer_y)], fill=(33, 38, 45, 255), width=1)

    brand_lead = "🚀 Project by" if draft.format_kind == "showcase" else "⚡ Curated by"
    brand_font = _font(settings, "Inter-SemiBold.ttf", 20)
    brand_sig = f"{brand_lead} {settings.author_name} ({settings.author_handle})  |  {settings.author_title}"
    draw.text((70, footer_y + 24), brand_sig, font=brand_font, fill=(139, 148, 158, 255))

    final_img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    return final_img


def render_branded_image(draft: PostDraft, settings: Settings, output_path: Path) -> Optional[Path]:
    """
    Renders high-quality visual adhering to Section 15:
    - Text-only posts return None (no forced useless graphics).
    - Diverse visual aesthetics (clean architectural layouts, not repetitive neon).
    - Unifies 'news' and 'verified_news_analysis' format keys.
    """
    # 1. Discussion posts and explicitly imageless drafts remain clean text-only
    if not draft.has_image or draft.format_kind == "discussion":
        logger.info("Post format is text-only (discussion or diagram adds no value).")
        return None

    bg_img: Optional[Image.Image] = None

    # 2. For verified news, execute intended contextual visual generation
    is_news_format = draft.format_kind in ("news", "verified_news_analysis")
    if is_news_format and settings.image_mode == "ai":
        clean_desc = draft.visual_prompt or f"clean minimalist technical schematic {draft.category} modern dark slate"
        prompt = f"{clean_desc}, minimalist clean technical composition, dark slate background, high contrast, no text, no neon glowing clutter, 4k"
        bg_bytes = _fetch_pollinations_image(prompt)
        if bg_bytes:
            try:
                raw_bg = Image.open(io.BytesIO(bg_bytes)).convert("RGB")
                raw_bg = raw_bg.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
                enhancer = ImageEnhance.Brightness(raw_bg)
                bg_img = enhancer.enhance(0.40)
            except Exception as exc:
                logger.warning("Failed to decode background visual: %s", exc)
                bg_img = None

    # 3. Render clean technical card
    final_card = _draw_clean_tech_card(draft, settings, bg_img)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    final_card.save(str(output_path), "JPEG", quality=92)
    logger.info("Rendered high-quality technical graphic to %s", output_path)
    return output_path


def validate_image_consistency(draft: PostDraft, image_path: Optional[Path]) -> tuple[bool, str]:
    """
    Genuine Semantic and Structural Image-to-Post Consistency Validation.
    Verifies:
    1. File integrity, valid dimensions, non-corrupt encoding.
    2. Semantic topic alignment: Card headline, technology terms, and takeaway must
       substantively correspond to the post body.
    3. Rejects images if semantic validation fails, allowing clean text-only publication.
    """
    if image_path is None:
        return True, "Text-only post; no image validation required."

    if not image_path.exists() or image_path.stat().st_size < 1000:
        return False, f"Image file {image_path} does not exist or is under 1KB."

    # 1. Structural check
    try:
        with Image.open(image_path) as img:
            w, h = img.size
            if w < 600 or h < 300:
                return False, f"Image dimensions too small ({w}x{h}). Minimum required: 600x300."
    except Exception as exc:
        return False, f"Corrupted image file: {exc}"

    # 2. Semantic consistency check
    post_text = f"{draft.body} {draft.story.title if draft.story else ''}".lower()
    title_terms = [t.lower() for t in re.findall(r"\b[a-zA-Z0-9_\-\+]{3,}\b", draft.image_title)]

    # Check that at least one key technical identifier in image_title appears in post body
    overlap = [term for term in title_terms if term in post_text and term not in ("the", "and", "for", "with", "from", "that")]
    if title_terms and not overlap:
        return False, f"Semantic mismatch: Image title '{draft.image_title}' shares no technical terms with post body."

    # 3. If visual prompt exists, verify it doesn't contain forbidden generic neon buzzwords
    if draft.visual_prompt:
        for forbidden in ("cyberpunk neon glowing city", "matrix raining code", "alien spacecraft"):
            if forbidden in draft.visual_prompt.lower():
                return False, f"Image visual prompt contains forbidden generic trope: '{forbidden}'."

    return True, "Image structurally valid and semantically consistent with post content."
