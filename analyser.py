import anthropic
import base64
import cv2
import os
from dotenv import load_dotenv

load_dotenv()

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# ── Model selection ────────────────────────────────────────────────────────────
# This runs frequently (AR /ar-analyze every ~3s), so cost matters.
# Default to Haiku 4.5 — ~5x cheaper than Opus ($1/$5 vs $5/$25 per 1M tokens) and
# plenty capable for structured fruit-quality extraction.
# Override in .env with ANALYSER_MODEL=claude-opus-4-8 for maximum quality.
#
# NOTE on prompt caching: deliberately NOT used here. The static instruction is
# ~150 tokens (well below the 4096-token Opus / 2048-token Sonnet cache minimum),
# and the fruit image — the only large part — changes every call, so there is no
# reusable prefix to cache. The cost wins are model choice + image size, below.
DEFAULT_MODEL = os.getenv("ANALYSER_MODEL", "claude-haiku-4-5")

# Max long-edge for the cropped fruit image before sending. Image input tokens
# scale with pixel area (~ width*height / 750), so capping the crop size is the
# single biggest token saver. 512px keeps quality cues (skin, bruising) while
# cutting image tokens ~4-5x vs a full-res crop.
MAX_IMAGE_EDGE = int(os.getenv("ANALYSER_MAX_IMAGE_EDGE", "512"))


def encode_image(cv2_image, max_edge: int = MAX_IMAGE_EDGE):
    """Resize (down only) and JPEG-encode a BGR crop to base64, to limit image tokens."""
    h, w = cv2_image.shape[:2]
    if max(h, w) > max_edge:
        scale = max_edge / float(max(h, w))
        cv2_image = cv2.resize(
            cv2_image, (int(w * scale), int(h * scale)),
            interpolation=cv2.INTER_AREA,
        )
    _, buffer = cv2.imencode('.jpg', cv2_image, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return base64.standard_b64encode(buffer).decode('utf-8')


def analyse_fruit(cropped_image, size_info, dominant_color, fruit_type="fruit", model=None):
    """
    Sends a cropped fruit image to Claude and returns structured analysis.

    model: optional override. Defaults to DEFAULT_MODEL (Haiku 4.5). The AR
    high-frequency path can pass a cheaper model; the legacy /analyse path can
    pass a higher-quality one (e.g. "claude-opus-4-8") if desired.
    """
    image_data = encode_image(cropped_image)
    r, g, b = dominant_color

    prompt = f"""You are an expert fruit quality inspector.

Analyse this {fruit_type} image carefully.

Additional sensor data:
- Estimated size: {size_info['estimated_diameter_cm']} cm diameter
- Dominant color (RGB): R={r}, G={g}, B={b}
- Relative size in frame: {size_info['relative_size_percent']}%

Respond in this EXACT format:

FRUIT_TYPE: [{fruit_type} - confirm or correct if wrong]
QUALITY: [Good / Acceptable / Poor / Bad]
DECAY_STAGE: [Fresh / Early Ripening / Ripe / Overripe / Decaying]
DAYS_REMAINING: [estimated days before unusable, as a number]
SORT_PRIORITY: [1=use immediately, 2=use soon, 3=store, 4=discard]
COLOR_DESCRIPTION: [describe the color and what it indicates]
SIZE_CATEGORY: [Small / Medium / Large]
DEFECTS: [list any visible defects, or 'None']
RECOMMENDATION: [one sentence action recommendation]
CONFIDENCE: [Low / Medium / High]

Base your assessment on visual cues: skin texture, color uniformity,
soft spots, mould, bruising, and overall appearance."""

    response = client.messages.create(
        model=model or DEFAULT_MODEL,
        max_tokens=500,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": image_data,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )

    raw_text = response.content[0].text
    return parse_response(raw_text)


def parse_response(text):
    result = {}
    for line in text.strip().split('\n'):
        if ':' in line:
            key, _, value = line.partition(':')
            result[key.strip()] = value.strip()

    defaults = {
        "FRUIT_TYPE": "Unknown",
        "QUALITY": "Unknown",
        "DECAY_STAGE": "Unknown",
        "DAYS_REMAINING": "?",
        "SORT_PRIORITY": "?",
        "COLOR_DESCRIPTION": "N/A",
        "SIZE_CATEGORY": "Unknown",
        "DEFECTS": "N/A",
        "RECOMMENDATION": "No recommendation",
        "CONFIDENCE": "Low",
    }
    for key, default in defaults.items():
        if key not in result:
            result[key] = default

    return result
