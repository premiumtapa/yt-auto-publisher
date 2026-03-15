"""
YouTube Data API v3 Integration
Handles OAuth2 authentication, listing private videos, updating metadata, and publishing.
"""

import os
import logging
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

# YouTube API scopes — we need full access to manage videos
SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]

TOKEN_FILE = "token.json"


def authenticate(client_secret_file: str = "client_secret.json", token_file: str = TOKEN_FILE):
    """
    Authenticate with YouTube Data API v3 using OAuth2.
    On first run, opens a browser for consent. After that, uses saved token.json.
    Returns an authenticated YouTube API service object.
    """
    creds = None

    # Load existing token if available
    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)
        logger.info(f"Loaded existing YouTube credentials from {token_file}")

    # If no valid creds, run the OAuth2 flow
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Refreshing expired YouTube credentials...")
            creds.refresh(Request())
        else:
            if not os.path.exists(client_secret_file):
                raise FileNotFoundError(
                    f"YouTube OAuth2 client secret file not found: {client_secret_file}\n"
                    f"Download it from Google Cloud Console and place it in the project root."
                )
            logger.info("Starting YouTube OAuth2 authorization flow...")
            flow = InstalledAppFlow.from_client_secrets_file(client_secret_file, SCOPES)
            creds = flow.run_local_server(port=0)

        # Save credentials for next run
        # Ensure directory exists if token_file is inside a folder
        token_dir = os.path.dirname(token_file)
        if token_dir and not os.path.exists(token_dir):
            os.makedirs(token_dir, exist_ok=True)
            
        with open(token_file, "w") as f:
            f.write(creds.to_json())
        logger.info(f"YouTube credentials saved to {token_file}")

    youtube = build("youtube", "v3", credentials=creds)
    logger.info("YouTube API service initialized successfully")
    return youtube


def get_private_videos(youtube) -> list[dict]:
    """
    Fetch all private videos from the authenticated user's channel.
    Returns a list of dicts with: video_id, title, description, tags, categoryId.
    """
    private_videos = []
    page_token = None

    while True:
        # Search for the user's own uploads
        request = youtube.search().list(
            part="id,snippet",
            forMine=True,
            type="video",
            maxResults=50,
            pageToken=page_token,
        )
        response = request.execute()

        # Collect video IDs from this page
        video_ids = [
            item["id"]["videoId"]
            for item in response.get("items", [])
            if item["id"].get("videoId")
        ]

        if video_ids:
            # Get detailed video info including status and full snippet
            videos_request = youtube.videos().list(
                part="snippet,status",
                id=",".join(video_ids),
            )
            videos_response = videos_request.execute()

            for video in videos_response.get("items", []):
                privacy = video["status"]["privacyStatus"]
                if privacy == "private":
                    snippet = video["snippet"]
                    private_videos.append({
                        "video_id": video["id"],
                        "title": snippet.get("title", ""),
                        "description": snippet.get("description", ""),
                        "tags": snippet.get("tags", []),
                        "categoryId": snippet.get("categoryId", "22"),
                    })

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    logger.info(f"Found {len(private_videos)} private video(s)")
    return private_videos


def update_video_metadata(
    youtube,
    video_id: str,
    title: str,
    description: str,
    tags: list[str],
    category_id: str = "22",
) -> dict:
    """
    Update a video's title, description, and tags.
    Returns the updated video resource.
    """
    logger.info(f"Updating video {video_id}:")
    logger.info(f"  Title: {title}")
    logger.info(f"  Description length: {len(description)} chars")
    logger.info(f"  Tags ({len(tags)}): {tags}")
    logger.info(f"  Category: {category_id}")

    request = youtube.videos().update(
        part="snippet",
        body={
            "id": video_id,
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags,
                "categoryId": category_id,
            },
        },
    )
    response = request.execute()
    logger.info(f"Updated metadata for video: {video_id}")
    return response


def set_video_public(youtube, video_id: str) -> dict:
    """
    Change a video's privacy status from private to public.
    Returns the updated video resource.
    """
    request = youtube.videos().update(
        part="status",
        body={
            "id": video_id,
            "status": {
                "privacyStatus": "public",
            },
        },
    )
    response = request.execute()
    logger.info(f"Set video {video_id} to public")
    return response


def get_trending_videos(youtube, query: str, max_results: int = 5) -> list[dict]:
    """
    Search YouTube for the top trending/most-viewed videos matching a query.
    Returns a list of dicts with: title, tags, view_count.

    Used to give Gemini AI real trend context so it can generate metadata
    that mirrors what's already performing well in the niche.

    API quota cost: ~100 units per call (search.list) + 1 unit (videos.list)
    """
    logger.info(f"Searching trending videos for query: '{query}'")
    trending = []

    try:
        # Step 1: Search for top videos by relevance / view count
        search_response = youtube.search().list(
            part="id,snippet",
            q=query,
            type="video",
            order="viewCount",          # most-viewed first
            maxResults=max_results,
            relevanceLanguage="en",     # prefer English results
            safeSearch="none",
        ).execute()

        video_ids = [
            item["id"]["videoId"]
            for item in search_response.get("items", [])
            if item["id"].get("videoId")
        ]

        if not video_ids:
            logger.warning(f"No trending videos found for query: '{query}'")
            return []

        # Step 2: Fetch full snippet (title + tags + view count) for those videos
        videos_response = youtube.videos().list(
            part="snippet,statistics",
            id=",".join(video_ids),
        ).execute()

        for item in videos_response.get("items", []):
            snippet = item.get("snippet", {})
            stats = item.get("statistics", {})
            trending.append({
                "title": snippet.get("title", ""),
                "tags": snippet.get("tags", []),
                "view_count": int(stats.get("viewCount", 0)),
            })

        # Sort by view count descending so top performers come first
        trending.sort(key=lambda x: x["view_count"], reverse=True)
        logger.info(
            f"Found {len(trending)} trending videos for '{query}'. "
            f"Top view count: {trending[0]['view_count']:,}" if trending else ""
        )

    except Exception as e:
        # Trend search is best-effort — never let it break the publish flow
        logger.warning(f"Trend search failed for '{query}': {e}. Continuing without trend data.")
        return []

    return trending
