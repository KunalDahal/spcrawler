from __future__ import annotations

import re
from urllib.parse import urlparse

from ..log import get_logger

log = get_logger("spcrawler.check")

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
    r"stream",
    r"live",
    r"embed",
    r"player",
    r"watch",
    r"tv/",
    r"/ch/",
    r"channel",
]

# ── Definite NON-stream indicators (image / tracker / ad / static) ────────────
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
    r"/ads?/",
    r"/track(er|ing)?/",
    r"/pixel",
    r"/beacon",
    r"/analytics",
    r"/stat(s|istics)?",
    r"\.gif\?",
]

_SHORT_VIDEO_DOMAINS = [
    "youtube.com", "youtu.be", "vimeo.com", "dailymotion.com",
    "twitch.tv",                    
    "tiktok.com", "instagram.com",
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
    if parsed.scheme in ("rtmp", "rtmpe", "rtmps", "rtmpt", "rtmpte",
                         "acestream", "sopcast"):
        log.debug("live-stream scheme: %s", url[:80])
        return True

    ext = _path_ext(url)
    if ext in _IMAGE_EXTENSIONS:
        return False
    if ext in _LIVE_EXTENSIONS:
        log.debug("live-stream ext %s: %s", ext, url[:80])
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
            log.debug("live-stream path pattern '%s': %s", pat, url[:80])
            return True
    for kw in _LIVE_DOMAIN_KEYWORDS:
        if kw in domain:
            log.debug("live-stream domain keyword '%s': %s", kw, url[:80])
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
            log.debug("live-stream iframe pattern '%s': %s", pat, src[:80])
            return True0
    for kw in _LIVE_DOMAIN_KEYWORDS:
        if kw in domain:
            return True

    return False


def filter_live_stream_iframes(iframes: list[str]) -> list[str]:
    result = [src for src in iframes if is_live_stream_iframe(src)]
    log.debug("iframe filter: %d/%d passed", len(result), len(iframes))
    return result


def filter_live_stream_urls(urls: list[str]) -> list[str]:
    result = [u for u in urls if is_live_stream_url(u)]
    log.debug("url filter: %d/%d passed", len(result), len(urls))
    return result


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