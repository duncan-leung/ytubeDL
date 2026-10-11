"""Fast YouTube listing extraction from initial page HTML (ytInitialData).

Extracts latest videos, shorts, and playlist items without headless browser or heavy scraping.
"""
from __future__ import annotations

import http.cookiejar
import json
import re
from typing import Any, Iterator, List, Optional
import requests

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def extract_yt_initial_data(html: str) -> Optional[dict[str, Any]]:
    """Parse `var ytInitialData = {...};` from channel/playlist HTML."""
    m = re.search(r"var\s+ytInitialData\s*=\s*", html)
    if not m:
        return None
    start = m.end()
    while start < len(html) and html[start] in " \t\n\r":
        start += 1
    if start >= len(html) or html[start] != "{":
        return None
    depth = 0
    in_str: Optional[str] = None
    escape = False
    for i in range(start, len(html)):
        ch = html[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == in_str:
                in_str = None
            continue
        if ch in ('"', "'"):
            in_str = ch
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                blob = html[start : i + 1]
                try:
                    return json.loads(blob)
                except json.JSONDecodeError:
                    return None
    return None


def walk_json(obj: Any) -> Iterator[Any]:
    """Recursively yield all dictionary and list nodes in JSON tree."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk_json(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk_json(v)


def _thumb_last(renderer: dict[str, Any]) -> str:
    thumbs = (renderer.get("thumbnail") or {}).get("thumbnails") or []
    return thumbs[-1]["url"] if thumbs else ""


def _title_from_renderer(renderer: dict[str, Any]) -> str:
    title = renderer.get("title") or {}
    runs = title.get("runs")
    if isinstance(runs, list) and runs:
        t = runs[0].get("text")
        if t:
            return str(t)
    st = title.get("simpleText")
    if st:
        return str(st)
    if isinstance(title.get("content"), str):
        return title["content"]
    return str(renderer.get("videoId") or "")


def as_video_dict(renderer: dict[str, Any]) -> dict[str, Any]:
    vid = renderer.get("videoId") or renderer.get("contentId")
    return {
        "videoId": str(vid or ""),
        "title": _title_from_renderer(renderer),
        "thumbnail_url": _thumb_last(renderer),
        "url": f"https://www.youtube.com/watch?v={vid}" if vid else "",
    }


def as_short_dict(item: dict[str, Any]) -> dict[str, Any]:
    vid = str(item.get("videoId") or item.get("contentId") or "")
    thumb = ""
    thumbs = (item.get("thumbnail") or {}).get("thumbnails")
    if isinstance(thumbs, list) and thumbs:
        thumb = str(thumbs[-1].get("url") or "")
    return {
        "videoId": vid,
        "title": vid,
        "thumbnail_url": thumb,
        "url": f"https://www.youtube.com/watch?v={vid}" if vid else "",
    }


def _iter_video_renderers(data: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for node in walk_json(data):
        vr = node.get("videoRenderer")
        if isinstance(vr, dict) and vr.get("videoId"):
            yield vr


def _iter_grid_video_renderers(data: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for node in walk_json(data):
        gv = node.get("gridVideoRenderer")
        if isinstance(gv, dict) and gv.get("videoId"):
            yield gv


def _iter_playlist_video_renderers(data: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for node in walk_json(data):
        vr = node.get("playlistVideoRenderer")
        if isinstance(vr, dict) and vr.get("videoId"):
            yield vr


def _iter_reel_watch_endpoints(data: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for node in walk_json(data):
        rw = node.get("reelWatchEndpoint")
        if isinstance(rw, dict) and rw.get("videoId"):
            yield rw


def _iter_lockup_view_items(
    data: dict[str, Any], *, shorts_tab: bool
) -> Iterator[dict[str, Any]]:
    for node in walk_json(data):
        ri = node.get("richItemRenderer")
        if not isinstance(ri, dict):
            continue
        lv = (ri.get("content") or {}).get("lockupViewModel")
        if not isinstance(lv, dict):
            continue
        cid = lv.get("contentId")
        if not cid or len(str(cid)) != 11:
            continue
        ctype = str(lv.get("contentType") or "")
        if "PLAYLIST" in ctype:
            continue
        if shorts_tab:
            if "SHORT" not in ctype:
                continue
        else:
            if "SHORT" in ctype:
                continue
            if ctype and "VIDEO" not in ctype:
                continue
        md = (lv.get("metadata") or {}).get("lockupMetadataViewModel") or {}
        title_obj = md.get("title") or {}
        title_text = (
            title_obj.get("content")
            if isinstance(title_obj.get("content"), str)
            else str(cid)
        )
        sources = (
            ((lv.get("contentImage") or {}).get("thumbnailViewModel") or {})
            .get("image", {})
            .get("sources")
            or []
        )
        thumb_url = str(sources[-1].get("url") or "") if sources else ""
        yield {
            "videoId": str(cid),
            "title": title_text,
            "thumbnail_url": thumb_url,
            "url": f"https://www.youtube.com/watch?v={cid}",
        }


def collect_items_from_yt_data(
    data: Optional[dict[str, Any]],
    *,
    listing_kind: str,
    limit: int = 0,
) -> List[dict[str, Any]]:
    """Extract video dicts from ytInitialData.

    listing_kind: 'videos' | 'shorts' | 'playlist'
    """
    if not data:
        return []

    out: List[dict[str, Any]] = []
    seen: set[str] = set()

    def add(item: dict[str, Any]) -> None:
        vid = str(item.get("videoId") or "")
        if not vid or vid in seen:
            return
        seen.add(vid)
        out.append(item)

    if listing_kind == "playlist":
        for vr in _iter_playlist_video_renderers(data):
            add(as_video_dict(vr))
    elif listing_kind == "shorts":
        for reel in _iter_reel_watch_endpoints(data):
            add(as_short_dict(reel))
        for lv in _iter_lockup_view_items(data, shorts_tab=True):
            add(lv)
    else:  # 'videos'
        for vr in _iter_video_renderers(data):
            add(as_video_dict(vr))
        for gv in _iter_grid_video_renderers(data):
            add(as_video_dict(gv))
        for lv in _iter_lockup_view_items(data, shorts_tab=False):
            add(lv)

    if limit and limit > 0:
        return out[:limit]
    return out


def normalize_tab_url(channel_url: str, content_type: str) -> str:
    """Ensure channel URL points to the right tab or playlist endpoint."""
    u = channel_url.strip()
    if "list=" in u:
        lid = u.split("list=", 1)[1].split("&")[0]
        return f"https://www.youtube.com/playlist?list={lid}"
    base = u.rstrip("/")
    for suffix in ("/videos", "/shorts", "/streams", "/playlists", "/featured"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    if content_type == "shorts":
        return f"{base}/shorts"
    elif content_type == "playlist":
        return u
    return f"{base}/videos"


def _load_netscape_cookies(cookie_path: str) -> http.cookiejar.MozillaCookieJar:
    jar = http.cookiejar.MozillaCookieJar(cookie_path)
    jar.load(ignore_discard=True, ignore_expires=True)
    return jar


def fetch_first_page_videos(
    url: str,
    content_type: str = "videos",
    cookie_path: Optional[str] = None,
    limit: int = 0,
) -> List[dict[str, Any]]:
    """Fetch video items from the first HTML page load using ytInitialData."""
    target_url = normalize_tab_url(url, content_type)
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
    cookies = None
    if cookie_path:
        try:
            cookies = _load_netscape_cookies(cookie_path)
        except Exception:
            cookies = None

    params = {"ucbcb": 1} if "youtube.com" in target_url else None
    try:
        r = requests.get(
            target_url,
            headers=headers,
            cookies=cookies,
            params=params,
            timeout=(15, 30),
        )
        r.raise_for_status()
        html = r.text
    except Exception as e:
        print(f"[youtube_extract] HTTP request failed for {target_url}: {e}")
        return []

    data = extract_yt_initial_data(html)
    if not data:
        print(f"[youtube_extract] ytInitialData not found in HTML for {target_url}")
        return []

    listing_kind = "playlist" if content_type == "playlist" or "list=" in url else (
        "shorts" if content_type == "shorts" else "videos"
    )
    return collect_items_from_yt_data(data, listing_kind=listing_kind, limit=limit)
