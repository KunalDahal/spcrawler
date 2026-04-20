# spcrawler

A Python crawler that finds illegal live sports streams. Given a match keyword, it runs multi-turn DuckDuckGo searches, DFS-crawls every result URL with a stealth headless browser, extracts stream URLs from HTML and network traffic, and stores everything in MongoDB.

---

## How it works

1. Runs 7 rounds of DuckDuckGo searches with varied query templates for the target keyword
2. Every URL returned across all search rounds is crawled — no filtering or LLM ranking at this stage
3. Each search result URL gets its own isolated MongoDB collection (one crawl tree per parent URL)
4. DFS-crawls each URL with a stealth headless browser powered by [crawl4ai](https://github.com/unclecode/crawl4ai)
5. At each page: scores it with rule-based heuristics + LLM, detects and dismisses ad overlays, extracts m3u8 / RTMP / iframe stream URLs from HTML and network traffic
6. Verifies each discovered URL is a live feed (not VOD or highlights) via LLM
7. Persists streams, per-parent crawl trees, session metadata to MongoDB

---

## Architecture

The entire system is event-driven. Every significant action emits a structured `Event` object onto an internal async `EventBus`. There is no logging module — all observability is through events, making it straightforward to consume from any language or transport layer.

### Event types

| Event | When it fires |
|---|---|
| `session.created` | Scraper initialised, session written to DB |
| `search.start` | DDGS search begins |
| `search.turn_done` | One DDGS round completed |
| `search.complete` | All search rounds finished |
| `search.candidates` | Full list of URLs to crawl, after deduplication |
| `llm.navigate` | LLM decides next URL(s) to follow |
| `llm.score` | LLM scores a page for piracy likelihood |
| `llm.verify_live` | LLM checks whether a stream URL is live |
| `llm.ad_check` | LLM checks for ad overlay on a page |
| `crawl.tree_start` | DFS begins for one parent URL |
| `crawl.tree_done` | DFS finished for one parent URL |
| `crawl.page_start` | About to fetch a URL |
| `crawl.page_done` | URL fetched, scored, written to DB |
| `crawl.page_fail` | Fetch failed |
| `crawl.ad_detected` | Interstitial ad found on a page |
| `crawl.ad_handled` | Ad dismissed (or skip attempt made) |
| `db.trees_registered` | Per-parent collections created in MongoDB |
| `db.node_upserted` | Page document written to MongoDB |
| `db.stream_recorded` | Stream URL persisted to MongoDB |
| `stream.found` | Live stream confirmed and recorded |
| `stream.rejected` | Stream candidate rejected by LLM |
| `error` | Any internal error with `context` and `error` fields |

---

## Installation

```bash
git clone https://github.com/KunalDahal/spcrawler.git
cd spcrawler
pip install -e .
```

After installation, run the [crawl4ai](https://github.com/unclecode/crawl4ai) browser setup:

```bash
crawl4ai-setup
```

---

## Requirements

- Python 3.11+
- MongoDB (local or remote)
- Gemini API key — get one at [aistudio.google.com](https://aistudio.google.com)

---

## Configuration

Create a `.env` file in your project root:

```env
GEMINI_API_KEY=your_key_here
MONGO_URI=mongodb://localhost:27017
DB_NAME=sports_scraper
PROXY_URL=
```

| Variable | Required | Description |
|---|---|---|
| `GEMINI_API_KEY` | Yes | Gemini API key used for LLM calls |
| `MONGO_URI` | Yes | MongoDB connection string |
| `DB_NAME` | Yes | Database name |
| `PROXY_URL` | No | HTTP/HTTPS proxy passed through to the crawl4ai browser |

---

## Usage

```python
import asyncio
from spcrawler import Scraper

async def main():
    scraper = Scraper(
        keyword   = "RR vs KKR live stream",
        api_key   = "your_gemini_key",
        mongo_uri = "mongodb://localhost:27017",
        db_name   = "sports_scraper",
        proxy_url = "",
    )

    streams = await scraper.run()

    for url in streams:
        print(url)

asyncio.run(main())
```

`scraper.run()` returns a list of verified live stream URLs found for the keyword.

### Synchronous usage

```python
scraper = Scraper(keyword="RR vs KKR live stream", api_key="...", ...)
streams = scraper.run_sync()
```

### Subscribing to events

```python
from spcrawler.events import E, Event

async def handler(event: Event) -> None:
    if event.type == E.STREAM_FOUND:
        print(event.data["stream_url"])
    elif event.type == E.ERROR:
        print(event.data["context"], event.data["error"])

scraper.subscribe(handler)
streams = await scraper.run()
```

Events carry a `session_id`, `ts` (ISO timestamp), `type` string, and a `data` dict. The shape of `data` is consistent per event type, making them easy to forward over WebSocket, Redis, NATS, or any other transport.

### Accessing session data

```python
session_id = scraper.session_id
streams    = scraper.get_streams()
```

---

## What gets stored in MongoDB

| Collection | Contents |
|---|---|
| `sessions` | One document per run — keyword, status, stream count, pages crawled |
| `tree_{session_id}_{index}` | One collection per search result URL — full crawl tree with scores, links, iframes, stream URLs |
| `streams` | Every verified live stream URL with source page, stream type, and score |

Each parent URL from the search results gets its own dedicated collection (`tree_{session_id}_00`, `tree_{session_id}_01`, ...), so crawl trees are fully isolated and individually queryable.

---

## Tuning

All crawler behaviour is controlled through constants in `spcrawler/utils/constants.py`:

| Constant | Default | Description |
|---|---|---|
| `DDGS_TURNS` | 7 | Number of search rounds |
| `DDGS_PER_TURN` | 15 | Results per round |
| `MAX_DEPTH` | 6 | Maximum DFS depth per parent URL |
| `MAX_TOTAL_PAGES` | 60 | Hard page cap across all candidates |
| `PIRACY_SCORE_THRESHOLD` | 60 | Score above which a page is flagged |
| `LLM_SCORE_TRIGGER` | 40 | Rule score above which LLM scoring kicks in |
| `MAX_DEAD_PAGES_BEFORE_BACKTRACK` | 4 | Dead-end streak before a branch is pruned |

---

## Dependencies

- [crawl4ai](https://github.com/unclecode/crawl4ai) — headless browser, stealth crawling, iframe and network capture
- [ddgs](https://pypi.org/project/ddgs/) — DuckDuckGo search
- [pymongo](https://pypi.org/project/pymongo/) — MongoDB client
- [requests](https://pypi.org/project/requests/) — Gemini API calls
- [python-dotenv](https://pypi.org/project/python-dotenv/) — environment variable loading

---

## License

MIT