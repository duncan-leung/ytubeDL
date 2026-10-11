"""YouTube Channel & Playlist URL Classifier and Metadata Resolver."""
import json
import re
import subprocess
from typing import Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse
import requests

from core.bin_helper import find_ytdlp

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

CHANNEL_ID_PATTERNS = [
    r"<meta\s+itemprop=[\"']channelId[\"']\s+content=[\"'](UC[0-9A-Za-z_-]{21,})[\"']",
    r"<meta\s+itemprop=[\"']identifier[\"']\s+content=[\"'](UC[0-9A-Za-z_-]{21,})[\"']",
    r"<link\s+rel=[\"']canonical[\"']\s+href=[\"']https?://(?:www\.)?youtube\.com/channel/(UC[0-9A-Za-z_-]{21,})[\"']",
    r"[\"']channelId[\"']\s*:\s*[\"'](UC[0-9A-Za-z_-]{21,})[\"']",
    r"[\"']externalId[\"']\s*:\s*[\"'](UC[0-9A-Za-z_-]{21,})[\"']",
    r"[\"']browseId[\"']\s*:\s*[\"'](UC[0-9A-Za-z_-]{21,})[\"']",
]


def detect_content_type(url: str) -> Optional[str]:
    """Determine whether URL targets videos, shorts, or a playlist.

    Returns:
        'shorts', 'videos', 'playlist', or None if no content type clue is present.
    """
    clean_url = url.strip()
    parsed = urlparse(clean_url)
    path = parsed.path.lower()
    query = parse_qs(parsed.query)

    # 1. Check for playlist
    if "list" in query or "playlist" in path:
        return "playlist"

    # 2. Check for shorts
    if "/shorts" in path:
        return "shorts"

    # 3. Check for videos
    if "/videos" in path:
        return "videos"

    return None


def validate_channel_url(url: str) -> Tuple[bool, str, Optional[str]]:
    """Validate if URL has explicit content-type clues.

    Returns:
        (is_valid, error_message, content_type)
    """
    raw = url.strip()
    if not raw:
        return False, "Please enter a YouTube URL.", None

    ctype = detect_content_type(raw)
    if not ctype:
        msg = (
            "A sample URL of the content type is required (e.g., .../videos, "
            ".../shorts, or a playlist URL), not the home page of the channel."
        )
        return False, msg, None

    return True, "", ctype


def extract_playlist_id(url: str) -> Optional[str]:
    """Extract playlist ID from query parameters or path."""
    parsed = urlparse(url.strip())
    query = parse_qs(parsed.query)
    if "list" in query and query["list"]:
        return query["list"][0]
    m = re.search(r"list=([A-Za-z0-9_-]+)", url)
    return m.group(1) if m else None


def _clean_title(title: str) -> str:
    """Strip YouTube title suffixes."""
    cleaned = title.strip()
    for suffix in [" - YouTube", " - YouTube Music", " | YouTube"]:
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].strip()
    return cleaned


def resolve_channel_metadata(
    url: str, cookies_file: Optional[str] = None
) -> Dict[str, str]:
    """Resolve channel_id, channel display name, and content type from URL.

    Performs fast webpage source inspection with ranked regex searches,
    falling back to yt-dlp if needed.
    """
    valid, err_msg, content_type = validate_channel_url(url)
    if not valid:
        raise ValueError(err_msg)

    result = {
        "url": url.strip(),
        "content_type": content_type or "videos",
        "channel_id": "",
        "name": "",
        "playlist_id": "",
    }

    # If playlist, grab playlist ID
    if content_type == "playlist":
        pl_id = extract_playlist_id(url)
        if pl_id:
            result["playlist_id"] = pl_id

    # 1. Check if channel_id is already embedded in URL
    m_direct = re.search(r"youtube\.com/channel/(UC[0-9A-Za-z_-]{21,})", url)
    if m_direct:
        result["channel_id"] = m_direct.group(1)

    # 2. Fast HTTP GET to inspect webpage HTML
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
    html_text = ""
    try:
        resp = requests.get(
            url,
            headers=headers,
            allow_redirects=True,
            timeout=(8, 15),
        )
        if resp.status_code == 200:
            html_text = resp.text

            # Check redirected URL for channel ID
            if not result["channel_id"]:
                m_redir = re.search(
                    r"youtube\.com/channel/(UC[0-9A-Za-z_-]{21,})", resp.url
                )
                if m_redir:
                    result["channel_id"] = m_redir.group(1)
    except Exception as e:
        print(f"[channel_resolver] HTTP GET failed for {url}: {e}")

    # 3. Parse channel_id from HTML if still missing
    if not result["channel_id"] and html_text:
        for pattern in CHANNEL_ID_PATTERNS:
            m = re.search(pattern, html_text)
            if m:
                result["channel_id"] = m.group(1)
                break

    # 4. Extract Title / Channel Name from HTML
    if html_text:
        # Check og:title
        m_title = re.search(r"<meta\s+property=[\"']og:title[\"']\s+content=[\"']([^\"']+)[\"']", html_text)
        if m_title:
            result["name"] = _clean_title(m_title.group(1))
        else:
            m_tag = re.search(r"<title>([^<]+)</title>", html_text)
            if m_tag:
                result["name"] = _clean_title(m_tag.group(1))

    # 5. Fallback via yt-dlp if channel_id or name is still empty
    if not result["channel_id"] or not result["name"]:
        ytdlp_bin = find_ytdlp()
        if ytdlp_bin:
            cmd = [
                ytdlp_bin,
                "--dump-single-json",
                "--flat-playlist",
                "--playlist-items",
                "0",
                "--quiet",
                "--no-warnings",
            ]
            if cookies_file:
                cmd.extend(["--cookies", cookies_file])
            cmd.append(url)

            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=20, check=True
                )
                info = json.loads(proc.stdout)
                if not result["channel_id"]:
                    result["channel_id"] = (
                        info.get("channel_id")
                        or info.get("uploader_id")
                        or ""
                    )
                if not result["name"]:
                    result["name"] = (
                        info.get("channel")
                        or info.get("uploader")
                        or info.get("title")
                        or ""
                    )
            except Exception as e:
                print(f"[channel_resolver] yt-dlp fallback failed: {e}")

    # Final cleanup of name
    if not result["name"]:
        # Derive name from handle or path
        m_handle = re.search(r"@([A-Za-z0-9_.-]+)", url)
        if m_handle:
            result["name"] = m_handle.group(1)
        elif result["channel_id"]:
            result["name"] = result["channel_id"]
        else:
            result["name"] = "Untitled Channel"

    return result
