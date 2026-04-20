import asyncio
import os
from dotenv import load_dotenv

from src import Scraper
from src.events import E, Event

load_dotenv()

API_KEY   = os.getenv("GEMINI_API_KEY", "")
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME   = os.getenv("DB_NAME", "sports_scraper")
PROXY_URL = os.getenv("PROXY_URL", "")

_C_RESET  = "\033[0m"
_C_BOLD   = "\033[1m"
_C_CYAN   = "\033[96m"
_C_BLUE   = "\033[94m"
_C_GREEN  = "\033[92m"
_C_YELLOW = "\033[93m"
_C_RED    = "\033[91m"
_C_PURPLE = "\033[95m"

_TYPE_COLOR = {
    "session": _C_CYAN,
    "search":  _C_BLUE,
    "llm":     _C_PURPLE,
    "crawl":   _C_YELLOW,
    "stream":  _C_GREEN,
    "db":      _C_CYAN,
    "error":   _C_RED,
}


def _color_for(event_type: str) -> str:
    prefix = event_type.split(".")[0]
    return _TYPE_COLOR.get(prefix, _C_RESET)


async def event_handler(event: Event) -> None:
    color = _color_for(event.type)
    d     = event.data

    print(f"\n{color}{_C_BOLD}{event.type}{_C_RESET}  {event.ts}  sid={event.session_id}")

    if event.type == E.SESSION_CREATED:
        print(f"  keyword={d.get('keyword')}  session={d.get('session_id')}")

    elif event.type == E.SESSION_FINISHED:
        print(
            f"  keyword={d.get('keyword')}  streams={d.get('streams_found')}"
            f"  pages={d.get('pages_crawled')}"
        )
        for s in d.get("streams", []):
            print(f"  {_C_GREEN}{s}{_C_RESET}")

    elif event.type == E.SEARCH_START:
        print(f"  keyword={d.get('keyword')}  turns={d.get('turns')}")

    elif event.type == E.SEARCH_TURN_DONE:
        print(
            f"  turn={d.get('turn')}/{d.get('total_turns')}"
            f"  query={d.get('query')}  new={d.get('new_results')}  total={d.get('total')}"
        )

    elif event.type == E.SEARCH_COMPLETE:
        print(f"  total_results={d.get('total_results')}")

    elif event.type == E.SEARCH_CANDIDATES:
        print(f"  total={d.get('total')}")
        for i, url in enumerate(d.get("candidates", [])[:5], 1):
            print(f"  {i}. {url}")
        if d.get("total", 0) > 5:
            print(f"  ... and {d['total'] - 5} more")

    elif event.type == E.LLM_NAVIGATE:
        print(
            f"  url={d.get('url')}  depth={d.get('depth')}"
            f"  action={d.get('action')}  signal={d.get('signal')}"
        )
        print(f"  reason={d.get('reason')}")
        if d.get("next_urls"):
            print(f"  next={d.get('next_urls')}")

    elif event.type == E.LLM_SCORE:
        print(
            f"  url={d.get('url')}  rule={d.get('rule')}"
            f"  llm={d.get('llm')}  combined={d.get('combined')}"
        )

    elif event.type == E.LLM_VERIFY_LIVE:
        print(
            f"  stream={d.get('stream_url')}"
            f"  source={d.get('source_url')}  live={d.get('is_live')}"
        )

    elif event.type == E.LLM_AD_CHECK:
        print(
            f"  url={d.get('url')}  has_ad={d.get('has_ad')}"
            f"  action={d.get('action')}  wait={d.get('wait_seconds')}"
        )

    elif event.type == E.CRAWL_TREE_START:
        print(f"  start={d.get('start_url')}  col={d.get('tree_col')}")

    elif event.type == E.CRAWL_TREE_DONE:
        print(
            f"  start={d.get('start_url')}  col={d.get('tree_col')}"
            f"  pages={d.get('pages_crawled')}"
        )

    elif event.type == E.CRAWL_PAGE_START:
        print(
            f"  url={d.get('url')}  depth={d.get('depth')}"
            f"  dead_streak={d.get('dead_streak')}"
        )

    elif event.type == E.CRAWL_PAGE_DONE:
        print(
            f"  url={d.get('url')}  depth={d.get('depth')}"
            f"  score={d.get('score')}  flagged={d.get('flagged')}"
        )

    elif event.type == E.CRAWL_PAGE_FAIL:
        print(f"  url={d.get('url')}  depth={d.get('depth')}")

    elif event.type == E.CRAWL_AD_DETECTED:
        print(
            f"  url={d.get('url')}  action={d.get('action')}"
            f"  wait={d.get('wait_seconds')}  hint={d.get('selector_hint')}"
        )

    elif event.type == E.CRAWL_AD_HANDLED:
        print(
            f"  url={d.get('url')}  hint={d.get('selector_hint')}"
            f"  success={d.get('success')}"
        )
        if not d.get("success"):
            print(f"  error={d.get('error')}")

    elif event.type == E.DB_TREES_REGISTERED:
        print(f"  count={d.get('count')}")

    elif event.type == E.DB_NODE_UPSERTED:
        print(f"  url={d.get('url')}  tree={d.get('tree_col')}  score={d.get('score')}")

    elif event.type == E.DB_STREAM_RECORDED:
        print(f"  stream={d.get('stream_url')}  source={d.get('source_url')}")

    elif event.type == E.STREAM_FOUND:
        print(
            f"  {_C_GREEN}{_C_BOLD}FOUND{_C_RESET}"
            f"  url={d.get('stream_url')}  type={d.get('stream_type')}"
            f"  score={d.get('score')}"
        )

    elif event.type == E.STREAM_REJECTED:
        print(
            f"  stream={d.get('stream_url')}"
            f"  reason={d.get('reason')}"
        )

    elif event.type == E.ERROR:
        print(f"  {_C_RED}context={d.get('context')}  error={d.get('error')}{_C_RESET}")

    else:
        print(f"  {d}")


async def main() -> None:
    scraper = Scraper(
        keyword   = "NewZealand Vs Bangladesh odi",
        api_key   = API_KEY,
        db_name   = DB_NAME,
        mongo_uri = MONGO_URI,
        proxy_url = PROXY_URL,
    )

    scraper.subscribe(event_handler)

    print(f"{_C_BOLD}{_C_CYAN}spcrawler starting{_C_RESET}")
    print(f"keyword={scraper.keyword}  session={scraper.session_id}")

    m3u8_streams = await scraper.run()

    print(f"\n{_C_BOLD}results{_C_RESET}")
    if m3u8_streams:
        for i, s in enumerate(m3u8_streams, 1):
            print(f"  {i}. {_C_GREEN}{s}{_C_RESET}")
    else:
        print(f"  {_C_YELLOW}no streams found{_C_RESET}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n{_C_YELLOW}interrupted{_C_RESET}")
    except Exception as e:
        print(f"\n{_C_RED}error: {e}{_C_RESET}")