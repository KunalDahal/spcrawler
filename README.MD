# spcrawler

A Python crawler that finds illegal live sports streams. Given a match keyword, it runs multi-turn DuckDuckGo searches, uses a Gemini LLM to rank and navigate piracy sites, extracts stream URLs via a headless browser, and stores everything in MongoDB.

---

## How it works

1. Runs 7 rounds of DuckDuckGo searches with varied query templates for the target keyword
2. Feeds all results to Gemini, which picks the 10 most promising piracy candidate URLs
3. DFS-crawls each candidate with a stealth headless browser powered by [crawl4ai](https://github.com/unclecode/crawl4ai)
4. At each page: scores it with rule-based heuristics + LLM, detects and dismisses ad overlays, extracts m3u8 / RTMP / iframe stream URLs from HTML and network traffic
5. Verifies each discovered URL is a live feed (not VOD or highlights) via LLM
6. Persists streams, crawl tree, session metadata, and logs to MongoDB

---

## Installation

```bash
pip install spcrawler
```

Or install from source:

```bash
git clone https://github.com/KunalDahal/spcrawler.git
cd spcrawler
pip install -e .
```

After installation, run the [crawl4ai](https://github.com/unclecode/crawl4ai) browser setup:

```bash
crawl4ai-setup
```

> crawl4ai handles all headless browser management, stealth configurations, iframe processing, and network request capture. See the [crawl4ai docs](https://github.com/unclecode/crawl4ai) for advanced browser options.

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
LOG_LEVEL=info
```

| Variable | Required | Description |
|---|---|---|
| `GEMINI_API_KEY` | Yes | Gemini API key used for LLM calls |
| `MONGO_URI` | Yes | MongoDB connection string |
| `DB_NAME` | Yes | Database name to store sessions and streams |
| `PROXY_URL` | No | HTTP/HTTPS proxy passed through to the crawl4ai browser |
| `LOG_LEVEL` | No | `debug`, `info`, `warn`, or `error` (default: `info`) |

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
| `pages_{session_id}` | Every crawled page — URL, title, score, links, iframes, stream URLs |
| `streams` | Every verified live stream URL with source page and stream type |
| `logs` | Timestamped log entries per session |

---

## Tuning

All crawler behaviour is controlled through constants in `src/utils/constants.py`:

| Constant | Default | Description |
|---|---|---|
| `DDGS_TURNS` | 7 | Number of search rounds |
| `DDGS_PER_TURN` | 15 | Results per round |
| `MAX_DEPTH` | 6 | Maximum DFS crawl depth |
| `MAX_TOTAL_PAGES` | 60 | Hard page cap across all candidates |
| `PIRACY_SCORE_THRESHOLD` | 60 | Score above which a page is flagged |
| `LLM_SCORE_TRIGGER` | 40 | Rule score above which LLM scoring kicks in |
| `MAX_DEAD_PAGES_BEFORE_BACKTRACK` | 4 | Dead-end streak before a branch is pruned |

---

## Dependencies

- [crawl4ai](https://github.com/unclecode/crawl4ai) — headless browser engine, stealth crawling, iframe and network capture
- [ddgs](https://pypi.org/project/ddgs/) — DuckDuckGo search
- [pymongo](https://pypi.org/project/pymongo/) — MongoDB client
- [requests](https://pypi.org/project/requests/) — Gemini API calls
- [python-dotenv](https://pypi.org/project/python-dotenv/) — environment variable loading

---

## License

MIT