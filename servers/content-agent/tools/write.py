"""
Content generation tools for the content-agent MCP server.

Requires ANTHROPIC_API_KEY in the environment before starting.
Set it in your shell: export ANTHROPIC_API_KEY=sk-ant-...
"""
from __future__ import annotations

import os
import re
from pathlib import Path

BRANDS_DIR = Path(__file__).resolve().parents[1] / "brands"

_HEADLINE_MAX = 30
_DESC_MAX = 90
_HEADLINE_COUNT = 15
_DESC_COUNT = 4


def _get_client():
    """Return an authenticated Anthropic client, raising a clear error if key is missing."""
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError(
            "anthropic package is not installed. Run: pip install anthropic"
        ) from exc

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. "
            "Set it in your shell before starting ads-mcp: export ANTHROPIC_API_KEY=sk-ant-..."
        )
    return anthropic.Anthropic(api_key=api_key)


def write_google_ad(business_key: str, payload: dict) -> dict:
    """
    Generate RSA headlines and descriptions for a Google Ads campaign using Claude.

    Args:
        business_key: Identifies the brand voice file (brands/{business_key}.md).
        payload: Dict with optional keys:
            - campaignGoal (str)
            - targetAudience (str)
            - keywords (list[str] or comma-separated str)
            - tone (str): professional | urgent | friendly | local
            - keyword (str): single SC-signal keyword
            - city (str)
            - scContext (dict): {query, impressions, ctr, position} from Search Console

    Returns:
        Dict with keys: headlines, descriptions, warnings, model, inputTokens, outputTokens
    """
    # Load brand voice
    brand_file = BRANDS_DIR / f"{business_key}.md"
    brand_context = brand_file.read_text(encoding="utf-8") if brand_file.exists() else ""

    campaign_goal = (payload.get("campaignGoal") or "").strip()
    target_audience = (payload.get("targetAudience") or "").strip()
    keywords = payload.get("keywords", [])
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(",") if k.strip()]
    tone = (payload.get("tone") or "professional").strip()
    keyword = (payload.get("keyword") or "").strip()
    city = (payload.get("city") or "").strip()
    sc_context = payload.get("scContext") or {}

    # Build the SC signal note if provided
    sc_note = ""
    if sc_context:
        q = sc_context.get("query", keyword)
        imp = sc_context.get("impressions", 0)
        ctr_pct = sc_context.get("ctr", 0) * 100
        pos = sc_context.get("position", 0)
        sc_note = (
            f"\n\nSearch Console signal: The keyword '{q}' has {imp:,} impressions, "
            f"{ctr_pct:.1f}% CTR, and avg position {pos:.1f}. "
            "This keyword is getting visibility but low click-through — "
            "write compelling copy that drives more clicks."
        )

    system_prompt = f"""You are an expert Google Ads copywriter for local service businesses.

BRAND CONTEXT:
{brand_context if brand_context else "No brand file found. Use a professional, trustworthy local service tone."}

STRICT RSA CHARACTER LIMITS — NEVER EXCEED THESE:
- Headlines: MAXIMUM {_HEADLINE_MAX} characters each (count every space and character)
- Descriptions: MAXIMUM {_DESC_MAX} characters each (count every space and character)

OUTPUT FORMAT — respond with ONLY this numbered list, no preamble or explanation:

HEADLINES:
1. <headline>
2. <headline>
[continue to {_HEADLINE_COUNT}]

DESCRIPTIONS:
1. <description>
2. <description>
3. <description>
4. <description>

WRITING RULES:
- Every headline MUST be {_HEADLINE_MAX} chars or fewer — count carefully
- Every description MUST be {_DESC_MAX} chars or fewer
- Include city name where it fits naturally within the char limit
- Mix urgency, benefits, social proof, and CTAs across headlines
- No excessive punctuation, no ALL CAPS
- No phrase repetition across headlines"""

    user_prompt_parts = [
        f"Generate {_HEADLINE_COUNT} RSA headlines and {_DESC_COUNT} descriptions.",
        f"Campaign goal: {campaign_goal or 'Generate local service leads'}",
    ]
    if target_audience:
        user_prompt_parts.append(f"Target audience: {target_audience}")
    if keywords:
        user_prompt_parts.append(f"Include keywords: {', '.join(keywords)}")
    if keyword:
        user_prompt_parts.append(f"Primary keyword: {keyword}")
    if city:
        user_prompt_parts.append(f"Location: {city}")
    user_prompt_parts.append(f"Tone: {tone}")
    if sc_note:
        user_prompt_parts.append(sc_note)
    user_prompt_parts.append(
        f"\nRemember: headlines ≤ {_HEADLINE_MAX} chars, descriptions ≤ {_DESC_MAX} chars. "
        "Output ONLY the formatted numbered list."
    )

    user_prompt = "\n".join(user_prompt_parts)

    model = os.environ.get("CONTENT_AGENT_MODEL", "claude-haiku-4-5")
    client = _get_client()

    message = client.messages.create(
        model=model,
        max_tokens=1200,
        messages=[{"role": "user", "content": user_prompt}],
        system=system_prompt,
    )

    raw_text = message.content[0].text if message.content else ""
    raw_headlines, raw_descriptions = _parse_output(raw_text)

    headlines, h_warnings = _validate_and_truncate(raw_headlines, _HEADLINE_MAX, "Headline")
    descriptions, d_warnings = _validate_and_truncate(raw_descriptions, _DESC_MAX, "Description")
    warnings = h_warnings + d_warnings

    return {
        "headlines": headlines[:_HEADLINE_COUNT],
        "descriptions": descriptions[:_DESC_COUNT],
        "warnings": warnings,
        "model": model,
        "inputTokens": message.usage.input_tokens if message.usage else 0,
        "outputTokens": message.usage.output_tokens if message.usage else 0,
    }


def _parse_output(raw: str) -> tuple[list[str], list[str]]:
    """Parse Claude's numbered HEADLINES / DESCRIPTIONS output."""
    headlines: list[str] = []
    descriptions: list[str] = []
    section: str | None = None

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^headlines?:?\s*$", stripped, re.IGNORECASE):
            section = "headlines"
            continue
        if re.match(r"^descriptions?:?\s*$", stripped, re.IGNORECASE):
            section = "descriptions"
            continue
        # Match "1. text" or "1) text"
        m = re.match(r"^\d+[\.\)]\s+(.+)$", stripped)
        if m and section:
            text = m.group(1).strip()
            if section == "headlines":
                headlines.append(text)
            else:
                descriptions.append(text)

    return headlines, descriptions


def _validate_and_truncate(
    items: list[str], max_len: int, label: str
) -> tuple[list[str], list[str]]:
    """Trim items that exceed max_len and collect warnings."""
    result = []
    warnings = []
    for item in items:
        if len(item) > max_len:
            truncated = item[:max_len].rstrip()
            warnings.append(
                f"{label} truncated ({len(item)}→{len(truncated)} chars): '{truncated}'"
            )
            result.append(truncated)
        else:
            result.append(item)
    return result, warnings


# ── Review reply generation ────────────────────────────────────────────────────

_REPLY_MAX = 4096  # Google's limit for review replies


def write_review_reply(business_key: str, payload: dict) -> dict:
    """
    Generate a Google Business review reply using Claude.

    Args:
        business_key: Identifies the brand voice file (brands/{business_key}.md).
        payload: Dict with keys:
            - reviewerName (str): reviewer's display name
            - starRating (str|int): ONE/TWO/THREE/FOUR/FIVE or 1-5
            - reviewText (str): the review body (may be empty for rating-only reviews)
            - existingReply (str, optional): current reply to improve upon
            - tone (str, optional): professional | friendly | apologetic | thankful

    Returns:
        Dict with keys: reply (str), model (str), inputTokens (int), outputTokens (int)
    """
    _STAR_MAP = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5}

    # Load brand voice
    brand_file = BRANDS_DIR / f"{business_key}.md"
    brand_context = brand_file.read_text(encoding="utf-8") if brand_file.exists() else ""

    reviewer_name = payload.get("reviewerName") or "the customer"
    raw_rating = payload.get("starRating", "FIVE")
    star_count = _STAR_MAP.get(str(raw_rating).upper(), raw_rating) if isinstance(raw_rating, str) else int(raw_rating)
    review_text = (payload.get("reviewText") or "").strip()
    existing_reply = (payload.get("existingReply") or "").strip()
    tone = payload.get("tone", "professional and friendly")

    sentiment = (
        "positive" if star_count >= 4
        else "neutral" if star_count == 3
        else "negative"
    )

    system_prompt = (
        "You are an expert at writing professional Google Business review replies for a local service business. "
        "Your replies are warm, concise (2-4 sentences), and authentic. "
        "Never use filler phrases like 'Thank you for your feedback'. "
        "Never promise specific outcomes. Never mention competitor names. "
        "Address the reviewer by first name if available. "
        "For negative reviews: acknowledge the concern, briefly apologize, invite them to contact the business directly. "
        "For positive reviews: express genuine gratitude and reinforce the business's key strengths. "
        "Output ONLY the reply text — no labels, no preamble, no quotes."
    )
    if brand_context:
        system_prompt += f"\n\nBrand voice:\n{brand_context}"

    parts = []
    parts.append(f"Write a {tone} reply for a {star_count}-star ({sentiment}) Google review.")
    parts.append(f"Reviewer name: {reviewer_name}")
    if review_text:
        parts.append(f"Review text: {review_text}")
    else:
        parts.append("Note: This is a rating-only review with no written comment.")
    if existing_reply:
        parts.append(f"Improve upon this existing reply: {existing_reply}")

    user_prompt = "\n".join(parts)

    model = os.environ.get("CONTENT_AGENT_MODEL", "claude-haiku-4-5")
    client = _get_client()

    message = client.messages.create(
        model=model,
        max_tokens=300,
        messages=[{"role": "user", "content": user_prompt}],
        system=system_prompt,
    )

    reply_text = message.content[0].text.strip() if message.content else ""

    # Truncate to Google's limit (rare but possible)
    if len(reply_text) > _REPLY_MAX:
        reply_text = reply_text[:_REPLY_MAX].rstrip()

    return {
        "reply": reply_text,
        "model": model,
        "inputTokens": message.usage.input_tokens if message.usage else 0,
        "outputTokens": message.usage.output_tokens if message.usage else 0,
    }
