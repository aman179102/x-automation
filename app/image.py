from __future__ import annotations

import io
import logging
import random
import urllib.parse
from pathlib import Path

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
    """Fetch 100% royalty-free AI image from Pollinations.ai without API key."""
    clean_prompt = prompt.strip() or "cyberpunk digital matrix abstract data cloud server technology dark mode 4k"
    encoded_prompt = urllib.parse.quote(clean_prompt)
    seed = random.randint(1000, 999999)
    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1200&height=675&nologo=true&seed={seed}"

    try:
        with httpx.Client(timeout=25.0) as client:
            resp = client.get(url)
            if resp.status_code == 200 and len(resp.content) > 5000:
                return resp.content
    except Exception as exc:
        logger.warning("Pollinations image fetch failed: %s", exc)
    return None


def _fetch_pexels_image(query: str, api_key: str) -> bytes | None:
    """Fetch high-res royalty-free photo from Pexels API."""
    if not api_key:
        return None
    url = f"https://api.pexels.com/v1/search?query={urllib.parse.quote(query)}&per_page=1&orientation=landscape"
    headers = {"Authorization": api_key}
    try:
        with httpx.Client(timeout=15.0) as client:
            res = client.get(url, headers=headers)
            if res.status_code == 200:
                data = res.json()
                photos = data.get("photos", [])
                if photos:
                    img_url = photos[0]["src"].get("large2x") or photos[0]["src"]["large"]
                    img_res = client.get(img_url)
                    if img_res.status_code == 200:
                        return img_res.content
    except Exception as exc:
        logger.warning("Pexels image fetch failed: %s", exc)
    return None


def _create_fallback_gradient(category: str) -> Image.Image:
    """Create a dark, sleek cyber-tech gradient background."""
    img = Image.new("RGB", (WIDTH, HEIGHT), (11, 15, 25))
    draw = ImageDraw.Draw(img)

    accent_colors = {
        "security": (220, 38, 38),
        "ai": (124, 58, 237),
        "dev": (16, 185, 129),
        "hardware": (245, 158, 11),
        "showcase": (14, 165, 233),
        "tech": (59, 130, 246),
    }
    accent = accent_colors.get(category.lower(), (59, 130, 246))

    # Draw ambient glowing radial ellipses
    for radius in range(400, 50, -50):
        alpha = int(25 * (radius / 400))
        bbox = [100 - radius, 100 - radius, 100 + radius, 100 + radius]
        draw.ellipse(bbox, fill=(accent[0] // 3, accent[1] // 3, accent[2] // 3))

    for radius in range(500, 100, -60):
        bbox = [WIDTH - radius, HEIGHT - radius, WIDTH + radius, HEIGHT + radius]
        draw.ellipse(bbox, fill=(accent[0] // 4, accent[1] // 4, accent[2] // 4))

    return img.filter(ImageFilter.GaussianBlur(40))


def render_branded_image(draft: PostDraft, settings: Settings, output_path: Path) -> Path:
    """
    Renders a high-impact, 100% royalty-free visual card:
    1. Downloads AI / Stock background matching the topic.
    2. Overlays sleek dark glassmorphism banner.
    3. Adds headline and Aman Kumar's personal branding badge for reputation/SEO.
    """
    bg_bytes: bytes | None = None

    # Step 1: Obtain background image
    if settings.image_mode == "stock" and settings.pexels_api_key:
        query = draft.story.category if draft.story else "cyber tech cloud"
        bg_bytes = _fetch_pexels_image(query, settings.pexels_api_key)

    if not bg_bytes and settings.image_mode in ("ai", "stock"):
        # Build prompt tailored to the story
        story_cat = draft.category or "tech"
        headline = draft.image_title or (draft.story.title if draft.story else "Tech Innovation")
        visual_desc = draft.visual_prompt or f"sleek futuristic {story_cat} technology scene, dark aesthetic, glowing neon accents, 3d render"
        ai_prompt = f"{visual_desc}, cinematic lighting, 8k octane render, dark modern minimal composition, no text"
        bg_bytes = _fetch_pollinations_image(ai_prompt)

    if bg_bytes:
        try:
            base_img = Image.open(io.BytesIO(bg_bytes)).convert("RGB")
            base_img = base_img.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
            # Slightly dim the background so text pops clearly
            enhancer = ImageEnhance.Brightness(base_img)
            base_img = enhancer.enhance(0.55)
        except Exception as exc:
            logger.warning("Failed to decode downloaded background: %s", exc)
            base_img = _create_fallback_gradient(draft.category)
    else:
        base_img = _create_fallback_gradient(draft.category)

    # Step 2: Overlay glassmorphism lower card & border
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Subtle top category badge
    badge_y = 50
    cat_text = f"●  {draft.category.upper()}"
    cat_font = _font(settings, "Inter-SemiBold.ttf", 22)
    draw.rounded_rectangle([60, badge_y, 240, badge_y + 44], radius=8, fill=(17, 24, 39, 210), outline=(55, 65, 81, 230), width=1)
    draw.text((80, badge_y + 8), cat_text, font=cat_font, fill=(56, 189, 248, 255))

    # Dark translucent footer card
    card_top = 370
    card_h = 245
    draw.rounded_rectangle(
        [50, card_top, WIDTH - 50, card_top + card_h],
        radius=16,
        fill=(10, 15, 29, 230),
        outline=(51, 65, 85, 220),
        width=2,
    )

    # Headline on the card
    headline_font = _font(settings, "Inter-Bold.ttf", 46)
    headline_text = draft.image_title or (draft.story.title if draft.story else "")
    # Wrap headline if needed
    words = headline_text.split()
    lines: list[str] = []
    curr = ""
    for w in words:
        test = f"{curr} {w}".strip()
        if draw.textlength(test, font=headline_font) <= 1040:
            curr = test
        else:
            if curr:
                lines.append(curr)
            curr = w
    if curr:
        lines.append(curr)

    lines = lines[:2]  # Limit to 2 lines max
    text_y = card_top + 30
    for line in lines:
        draw.text((80, text_y), line, font=headline_font, fill=(248, 250, 252, 255))
        text_y += 56

    # Bottom branding bar
    divider_y = card_top + 165
    draw.line([(80, divider_y), (WIDTH - 80, divider_y)], fill=(51, 65, 85, 180), width=1)

    brand_font = _font(settings, "JetBrainsMono-Medium.ttf", 20)
    brand_handle_font = _font(settings, "Inter-SemiBold.ttf", 20)

    brand_lead = "⚡ Curated by" if not (draft.story and draft.story.is_showcase) else "🚀 Open Source Project by"
    brand_sig = f"{brand_lead} {settings.author_name} ({settings.author_handle}) | {settings.author_title}"
    draw.text((80, divider_y + 22), brand_sig, font=brand_handle_font, fill=(148, 163, 184, 255))

    # Composite overlay on base image
    final_img = Image.alpha_composite(base_img.convert("RGBA"), overlay).convert("RGB")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    final_img.save(str(output_path), "JPEG", quality=92)
    logger.info("Saved branded image to %s", output_path)
    return output_path
