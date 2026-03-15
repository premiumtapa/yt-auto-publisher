"""
Test script for trend-aware metadata generation.
Tests:
  1. Gemini AI with mock trending data  (no YouTube API needed)
  2. Sanitize tags output
Run with: python test_trending.py
"""
import os
import sys
import json
from dotenv import load_dotenv

# Add project root to path
sys.path.insert(0, os.path.dirname(__file__))
load_dotenv()

import gemini_ai

print("=" * 60)
print("  Trend-Aware Metadata Test")
print("=" * 60)

# ── Configure Gemini ──────────────────────────────────────────
api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    print("❌  GEMINI_API_KEY not found in .env")
    sys.exit(1)

gemini_ai.configure(api_key)
print("✅  Gemini configured")

# ── Mock trending data (simulates what YouTube API returns) ───
mock_trending = [
    {
        "title": "10 Gaming Tricks That Will BLOW YOUR MIND in 2025!",
        "tags": ["gaming tricks", "gaming tips", "pro gamer", "gaming 2025", "viral gaming"],
        "view_count": 4_200_000,
    },
    {
        "title": "This Gaming SECRET No One Talks About (MUST WATCH)",
        "tags": ["gaming secret", "gaming tutorial", "gaming hacks", "best gaming tips"],
        "view_count": 2_800_000,
    },
    {
        "title": "I Tried EVERY Gaming Trick for 30 Days - Here's What Happened",
        "tags": ["gaming challenge", "gaming experiment", "30 day challenge", "gaming"],
        "view_count": 1_500_000,
    },
]

# ── Test 1: With trending context ─────────────────────────────
print("\n📊 Test 1: Metadata WITH trending context")
print("-" * 40)
original_title = "Gaming Tips Day 12"
print(f"Original title: '{original_title}'")
print(f"Trend videos provided: {len(mock_trending)}")
print("Calling Gemini... (this may take 5-15 seconds)")

result = gemini_ai.optimize_video_metadata(original_title, trending_videos=mock_trending)

print(f"\n✅ Generated title  : {result['title']}")
print(f"   Title length    : {len(result['title'])} chars (max 100)")
print(f"   Tags count      : {len(result['tags'])}")
print(f"   Tags total chars: {sum(len(t) for t in result['tags'])} (max 400)")
print(f"   Desc length     : {len(result['description'])} chars (max 5000)")
print(f"   Tags preview    : {result['tags'][:6]}")

# Validate limits
assert len(result["title"]) <= 100, f"FAIL: Title too long ({len(result['title'])} chars)"
assert len(result["description"]) <= 5000, "FAIL: Description too long"
assert sum(len(t) for t in result["tags"]) <= 400, "FAIL: Total tags too long"
assert len(result["tags"]) <= 30, "FAIL: Too many tags"
print("✅  All YouTube limit checks PASSED")

# ── Test 2: Without trending context (fallback) ───────────────
print("\n📊 Test 2: Metadata WITHOUT trending context (fallback mode)")
print("-" * 40)
original_title2 = "Cooking Recipe Easy"
print(f"Original title: '{original_title2}'")
print("Calling Gemini...")

result2 = gemini_ai.optimize_video_metadata(original_title2, trending_videos=None)

print(f"\n✅ Generated title  : {result2['title']}")
print(f"   Title length    : {len(result2['title'])} chars")
print(f"   Tags count      : {len(result2['tags'])}")
assert len(result2["title"]) <= 100
print("✅  Fallback mode PASSED")

# ── Summary ───────────────────────────────────────────────────
print("\n" + "=" * 60)
print("  ALL TESTS PASSED ✅")
print("  Trend-aware metadata generation is working correctly.")
print("=" * 60)
