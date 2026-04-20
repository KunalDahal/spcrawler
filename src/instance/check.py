from __future__ import annotations

import re
from urllib.parse import urlparse

_LIVE_EXTENSIONS = {".m3u8", ".mpd"}

_LIVE_PATH_PATTERNS = [
    r"/hls/",
    r"/live/",
    r"/stream/",
    r"/playlist\.m3u8",
    r"[?&]stream=",
    r"[?&]live=",
    r"/manifest",
    r"/chunklist",
    r"\.ts($|[?#])",
]

_LIVE_DOMAIN_KEYWORDS = [
    "stream", "live", "hls", "cdn", "cast", "broadcast",
    "crackstream", "methstream", "buffstream", "streameast",
    "hesgoal", "rojadirecta", "acestream", "soccerstream",
    "nbabite", "nflbite", "cricfree",
]

_LIVE_IFRAME_SRC_PATTERNS = [
    r"stream", r"live", r"embed", r"player", r"watch", r"tv/", r"/ch/", r"channel",
]

_IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg",
    ".ico", ".bmp", ".tiff",
}

_STATIC_VIDEO_EXTENSIONS = {".mp4", ".webm", ".ogg", ".avi", ".mov", ".mkv"}

_AD_TRACKER_DOMAINS = [
    "doubleclick.net", "googlesyndication.com", "googletagmanager.com",
    "googleadservices.com", "amazon-adsystem.com", "facebook.com/plugins",
    "twitter.com/widgets", "platform.twitter.com", "disqus.com",
    "gravatar.com", "gstatic.com", "fonts.googleapis.com",
    "maps.googleapis.com", "recaptcha.net", "hotjar.com",
    "analytics.google.com", "connect.facebook.net", "adroll.com",
    "outbrain.com", "taboola.com", "adsafeprotected.com",
]

_AD_PATH_PATTERNS = [
    r"/ads?/", r"/track(er|ing)?/", r"/pixel", r"/beacon",
    r"/analytics", r"/stat(s|istics)?", r"\.gif\?",
]

_SHORT_VIDEO_DOMAINS = [
    "youtube.com", "youtu.be", "vimeo.com", "dailymotion.com",
    "twitch.tv", "tiktok.com", "instagram.com",
]

# URL substrings that indicate a stream is VOD/uploaded, not live
_VOD_URL_SIGNALS = [
    "upload", "uploaded", "vod", "record", "recorded", "replay",
    "archive", "clip", "highlight", "highlights", "/video/", "/videos/",
    "storage/", "blob.core", "s3.amazonaws.com",
    "cdn.jwplayer.com/videos", "content.jwplatform",
    "/mp4/", "/mp4?", "static/media",
]


def _path_ext(url: str) -> str:
    path = urlparse(url).path
    dot  = path.rfind(".")
    if dot == -1:
        return ""
    ext = path[dot:].lower()
    return ext.split("?")[0].split("#")[0]


def is_live_stream_url(url: str) -> bool:
    if not url:
        return False

    url_lower = url.lower()
    parsed    = urlparse(url_lower)
    if parsed.scheme in ("data", "blob", "javascript", "mailto"):
        return False
    if parsed.scheme in ("rtmp", "rtmpe", "rtmps", "rtmpt", "rtmpte", "acestream", "sopcast"):
        return True

    ext = _path_ext(url)
    if ext in _IMAGE_EXTENSIONS:
        return False
    if ext in _LIVE_EXTENSIONS:
        return True
    if ext in _STATIC_VIDEO_EXTENSIONS:
        return False

    domain = parsed.netloc
    for ad in _AD_TRACKER_DOMAINS:
        if ad in domain:
            return False
    for vd in _SHORT_VIDEO_DOMAINS:
        if vd in domain:
            return False
    for pat in _LIVE_PATH_PATTERNS:
        if re.search(pat, url_lower):
            return True
    for kw in _LIVE_DOMAIN_KEYWORDS:
        if kw in domain:
            return True

    return False


def is_live_stream_iframe(src: str) -> bool:
    if not src:
        return False

    src_lower = src.lower()
    parsed    = urlparse(src_lower)
    domain    = parsed.netloc
    if parsed.scheme in ("data", "blob", "javascript", "mailto"):
        return False
    for ad in _AD_TRACKER_DOMAINS:
        if ad in domain:
            return False
    for vd in _SHORT_VIDEO_DOMAINS:
        if vd in domain:
            return False
    if is_live_stream_url(src):
        return True

    path_and_query = parsed.path + "?" + (parsed.query or "")
    for pat in _LIVE_IFRAME_SRC_PATTERNS:
        if re.search(pat, path_and_query):
            return True
    for kw in _LIVE_DOMAIN_KEYWORDS:
        if kw in domain:
            return True

    return False


def filter_live_stream_iframes(iframes: list[str]) -> list[str]:
    return [src for src in iframes if is_live_stream_iframe(src)]


def filter_live_stream_urls(urls: list[str]) -> list[str]:
    return [u for u in urls if is_live_stream_url(u)]


def extract_best_live_stream(page_data: dict) -> str | None:
    candidates: list[str] = []
    candidates += filter_live_stream_urls(page_data.get("scheme_urls", []))
    candidates += filter_live_stream_iframes(page_data.get("iframes", []))

    if not candidates:
        return None
    for url in candidates:
        if ".m3u8" in url.lower():
            return url
    for url in candidates:
        if url.lower().startswith("rtmp"):
            return url
    return candidates[0]


def is_vod_url(url: str) -> bool:
    """Return True if a stream URL looks like a recorded/uploaded asset, not live."""
    url_lower = url.lower()
    for signal in _VOD_URL_SIGNALS:
        if signal in url_lower:
            return True
    # Static file hosted on generic object storage with no live path markers
    if re.search(r's3[.-][a-z0-9-]+\.amazonaws\.com', url_lower):
        return True
    return False


def extract_players_with_streams(page_data: dict, max_per_player: int = 5) -> dict:
    """
    Group live stream URLs by player/iframe source so each player_id maps
    to up to `max_per_player` confirmed live stream URLs.

    Returns:
        {
          "player_0": ["https://…m3u8", …],
          "player_1": […],
          …
          "page_streams": […],   # streams not tied to a specific iframe player
        }
    """
    iframes      = page_data.get("iframes", [])
    all_streams  = page_data.get("stream_urls", [])
    network_reqs = page_data.get("network_requests", [])  # optional enrichment

    result: dict[str, list[str]] = {}

    # --- Per-player (iframe) bucket ---
    live_iframes = filter_live_stream_iframes(iframes)
    for idx, iframe_src in enumerate(live_iframes):
        player_key = f"player_{idx}"
        # Seeds: the iframe src itself if it's a stream URL
        bucket: list[str] = []
        if is_live_stream_url(iframe_src) and not is_vod_url(iframe_src):
            bucket.append(iframe_src)
        # Pull network requests that originate from this player domain
        try:
            player_domain = urlparse(iframe_src).netloc.lower()
        except Exception:
            player_domain = ""
        for req in all_streams:
            if is_vod_url(req):
                continue
            if not is_live_stream_url(req):
                continue
            if player_domain and player_domain in req.lower():
                if req not in bucket:
                    bucket.append(req)
            if len(bucket) >= max_per_player:
                break
        if bucket:
            result[player_key] = bucket[:max_per_player]

    # --- Page-level streams not assigned to any player ---
    assigned: set[str] = {u for bucket in result.values() for u in bucket}
    page_bucket: list[str] = []
    for su in all_streams:
        if su in assigned:
            continue
        if is_vod_url(su):
            continue
        if not is_live_stream_url(su):
            continue
        page_bucket.append(su)
        if len(page_bucket) >= max_per_player:
            break

    if page_bucket:
        result["page_streams"] = page_bucket

    return result