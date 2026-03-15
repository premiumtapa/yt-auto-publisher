"""
Google Gemini AI Integration
Uses Gemini to analyze a video's original title and generate optimized
YouTube metadata: title, description, and tags.
"""

import json
import re
import logging
import google.generativeai as genai

logger = logging.getLogger(__name__)

# The system prompt that makes Gemini act as a top-level YouTube SEO expert
SYSTEM_PROMPT = """You are a world-class YouTube SEO expert and content strategist with 15+ years of experience growing channels to millions of subscribers.

You will receive:
1. A video's ORIGINAL TITLE
2. (Optional) A list of TOP TRENDING VIDEOS in the same niche with their titles and tags

Your job is to:

1. **Understand the topic** — Deduce what the video is about from the title alone.
2. **Study trends** — If trending videos are provided, analyze their title patterns, power words, hooks, and popular tags. Mirror what is already proven to get views.
3. **Create an optimized title** — Max 100 characters. Catchy, click-worthy, SEO-friendly. Use proven formulas: numbers, power words, curiosity gaps, emotional triggers.
4. **Write a full description** — 1500-2500 characters. Include:
   - A strong hook in the first 2 lines (visible before "Show more")
   - Keyword-rich body explaining what the video covers
   - Trending keywords naturally woven in
   - A "Timestamps" section: "⏱️ Timestamps:\n00:00 - Introduction"
   - Call-to-action: "👍 Like, Subscribe & Hit the Bell!"
   - 3 relevant hashtags at the end
5. **Generate tags** — 15 to 30 relevant tags as a JSON array. Mix broad and specific keywords. Include tags inspired by trending videos.

YOUTUBE LIMITS (strictly follow):
- Title: max 100 characters
- Description: max 5000 characters
- Each tag: max 30 characters, letters/numbers/spaces/hyphens only
- Total tag text: max 400 characters combined

IMPORTANT: Respond ONLY with valid JSON in this exact format (no markdown, no code fences):
{
    "title": "Your Optimized Title Here",
    "description": "Your full description here...",
    "tags": ["tag1", "tag2", "tag3", "..."]
}"""


def configure(api_key: str):
    """Configure the Gemini API with the provided key."""
    genai.configure(api_key=api_key)
    logger.info("Gemini AI configured successfully")


def optimize_video_metadata(original_title: str, trending_videos: list[dict] | None = None) -> dict:
    """
    Analyze the original video title using Gemini AI and generate
    optimized title, description, and tags.

    Args:
        original_title: The current title of the private YouTube video.
        trending_videos: Optional list of trending video dicts with keys:
                         title, tags, view_count — from youtube_api.get_trending_videos().

    Returns:
        dict with keys: title, description, tags
    """
    logger.info(f"Optimizing metadata for: '{original_title}'")
    if trending_videos:
        logger.info(f"Using {len(trending_videos)} trending videos as context")

    model = genai.GenerativeModel(model_name="gemma-3-27b-it")

    # Build the trend context section if we have data
    trend_section = ""
    if trending_videos:
        trend_lines = []
        for i, tv in enumerate(trending_videos, 1):
            views = f"{tv['view_count']:,}" if tv.get('view_count') else "N/A"
            tags_preview = ", ".join(tv.get('tags', [])[:10]) or "(no tags)"
            trend_lines.append(
                f"  {i}. Title: \"{tv['title']}\"\n"
                f"     Views: {views}\n"
                f"     Tags: {tags_preview}"
            )
        trend_section = (
            "\n\nTOP TRENDING VIDEOS IN THIS NICHE (study these for patterns):\n"
            + "\n".join(trend_lines)
            + "\n\nUse the above trend data to inform your title hooks, keywords, and tags."
        )

    user_prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"Original video title: \"{original_title}\""
        f"{trend_section}\n\n"
        f"Now generate the optimized YouTube metadata."
    )

    response = model.generate_content(user_prompt)

    # Parse the JSON response
    response_text = response.text.strip()

    # Strip markdown code fences if Gemini wraps the response
    if response_text.startswith("```"):
        # Remove ```json or ``` at start and ``` at end
        lines = response_text.split("\n")
        # Find first and last ``` lines
        start_idx = 0
        end_idx = len(lines)
        for i, line in enumerate(lines):
            if line.strip().startswith("```"):
                start_idx = i + 1
                break
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip() == "```":
                end_idx = i
                break
        response_text = "\n".join(lines[start_idx:end_idx])

    try:
        result = json.loads(response_text)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Gemini response as JSON: {e}")
        logger.error(f"Raw response: {response_text}")
        # Fallback: return the original title with minimal metadata
        return {
            "title": original_title,
            "description": f"Video about: {original_title}\n\n👍 Like, Subscribe & Hit the Bell!",
            "tags": [original_title.lower()],
        }

    # Validate required keys
    if "title" not in result or "description" not in result or "tags" not in result:
        logger.warning("Gemini response missing required keys, using partial data")
        result.setdefault("title", original_title)
        result.setdefault("description", f"Video about: {original_title}")
        result.setdefault("tags", [original_title.lower()])

    # Ensure tags is a list of strings and sanitize them
    if isinstance(result["tags"], list):
        result["tags"] = [str(tag) for tag in result["tags"]]
    else:
        result["tags"] = [str(result["tags"])]

    result["tags"] = _sanitize_tags(result["tags"])

    logger.info(f"Generated optimized title: '{result['title']}'")
    logger.info(f"Generated {len(result['tags'])} tags")

    return result


def _sanitize_tags(tags: list[str]) -> list[str]:
    """
    Sanitize tags to comply with YouTube's strict requirements:
    - Only allow letters, numbers, spaces, and hyphens
    - Strip all special characters, emojis, unicode
    - Remove empty or duplicate tags
    - Max 15 tags
    - Each tag max 30 characters
    - Total combined tag length under 400 characters
    """
    clean_tags = []
    seen = set()
    total_length = 0
    MAX_TAGS = 15
    MAX_TAG_LENGTH = 30
    MAX_TOTAL_LENGTH = 400

    logger.info(f"Raw tags from AI ({len(tags)}): {tags}")

    for tag in tags:
        if len(clean_tags) >= MAX_TAGS:
            break
        # Only keep letters, numbers, spaces, and hyphens
        tag = re.sub(r'[^a-zA-Z0-9\s\-]', '', str(tag))
        # Collapse multiple spaces
        tag = re.sub(r'\s+', ' ', tag).strip()
        # Skip empty or duplicate tags
        if not tag or tag.lower() in seen:
            continue
        # Limit individual tag length
        if len(tag) > MAX_TAG_LENGTH:
            tag = tag[:MAX_TAG_LENGTH].strip()
        # Check total length
        if total_length + len(tag) > MAX_TOTAL_LENGTH:
            break
        clean_tags.append(tag)
        seen.add(tag.lower())
        total_length += len(tag)

    logger.info(f"Sanitized tags ({len(clean_tags)}, {total_length} chars): {clean_tags}")
    return clean_tags
