from __future__ import annotations

LLM_MODEL                   = "gemini-2.5-flash-lite"
LLM_MAX_TOKENS              = 1024
LLM_TEMPERATURE             = 0.2
LLM_MAX_RETRIES             = 5
MIN_DELAY_BETWEEN_LLM_CALLS = 8.0
LLM_BACKOFF_BASE            = 20
LLM_BACKOFF_MAX             = 120

MAX_DEPTH          = 6
REQUEST_TIMEOUT_MS = 30_000
CRAWL_TIMEOUT_SEC  = 45
HEADLESS_BROWSER   = True
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

DDGS_TURNS          = 7
DDGS_PER_TURN       = 15
DDGS_TURN_DELAY     = 3.0
DDGS_SEARCH_QUERIES = [
    "{keyword} live stream free",
    "{keyword} watch online free",
    "{keyword} stream hd free",
    "{keyword} live stream link",
    "{keyword} free stream reddit",
    "{keyword} watch free telegram",
    "{keyword} live free",
]

PIRACY_SCORE_THRESHOLD = 60
LLM_SCORE_TRIGGER      = 40

MAX_DEAD_PAGES_BEFORE_BACKTRACK = 4
DEAD_END_SCORE                  = 15
MAX_TOTAL_PAGES                 = 60

TARGET_STREAM_EXTENSIONS = [".m3u8", ".mpd", ".ts"]
TARGET_STREAM_SCHEMES    = ["rtmp://", "rtmpe://", "rtmps://", "rtmpt://",
                            "acestream://", "sopcast://"]
TARGET_HLS_PATTERNS      = ["/hls/", "/live/", "/stream/", "/playlist",
                            "/chunklist", "/manifest"]

CDN_HEADERS = ["server", "x-powered-by", "via", "x-cache", "cf-ray", "x-served-by"]

VOD_URL_SIGNALS = [
    "upload", "uploaded", "vod", "record", "recorded", "replay",
    "archive", "clip", "highlight", "highlights", "video/",
    "videos/", "storage/", "blob.core", "s3.amazonaws.com",
    "cdn.jwplayer.com/videos", "content.jwplatform",
    "/mp4/", "/mp4?", ".mp4", "static/media",
]

MAX_STREAMS_PER_PLAYER = 5

AD_OVERLAY_JS_CHECKS = [
    "document.querySelector('[id*=skip],[class*=skip],[id*=ad-skip],[class*=ad-skip]')",
    "document.querySelector('.skip-btn,.skip-button,.skipbtn,.ad-skip,.closeBtn')",
]