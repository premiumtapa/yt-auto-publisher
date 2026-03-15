"""Full test: get AI metadata, then try updating the video with it."""
import os
from dotenv import load_dotenv
import youtube_api
import gemini_ai

load_dotenv()
gemini_ai.configure(os.getenv("GEMINI_API_KEY"))
youtube = youtube_api.authenticate()

# Get the video
private_videos = youtube_api.get_private_videos(youtube)
if not private_videos:
    print("No private videos found")
    exit()

video = private_videos[0]
print(f"Video: {video['title']} ({video['video_id']})")

# Get AI metadata
result = gemini_ai.optimize_video_metadata(video["title"])
print(f"\nAI Title: {result['title']}")
print(f"AI Tags ({len(result['tags'])}): {result['tags']}")

# Test A: AI title + AI description + SIMPLE tags
print("\n--- Test A: AI title + AI description + simple tags ---")
try:
    youtube_api.update_video_metadata(
        youtube, video["video_id"],
        result["title"], result["description"],
        ["construction", "timelapse"],
        video["categoryId"],
    )
    print("SUCCESS!")
except Exception as e:
    print(f"FAILED: {e}")

# Test B: Original title + simple description + AI tags
print("\n--- Test B: Original title + simple desc + AI tags ---")
try:
    youtube_api.update_video_metadata(
        youtube, video["video_id"],
        video["title"], "Simple test description",
        result["tags"],
        video["categoryId"],
    )
    print("SUCCESS!")
except Exception as e:
    print(f"FAILED: {e}")

# Test C: Everything from AI
print("\n--- Test C: Full AI metadata ---")
try:
    youtube_api.update_video_metadata(
        youtube, video["video_id"],
        result["title"], result["description"],
        result["tags"],
        video["categoryId"],
    )
    print("SUCCESS!")
except Exception as e:
    print(f"FAILED: {e}")
