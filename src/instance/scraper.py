from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
from ddgs import DDGS

from ..log import get_logger, success as log_success
from ..utils.config import Config
from ..utils.db import Database
from ..utils.constants import (
    DDGS_TURNS,
    DDGS_PER_TURN,
    DDGS_TURN_DELAY,
    DDGS_SEARCH_QUERIES,
    HEADLESS_BROWSER,
    USER_AGENT,
    REQUEST_TIMEOUT_MS,
    CRAWL_TIMEOUT_SEC,
    MAX_DEPTH,
    LLM_SCORE_TRIGGER,
    PIRACY_SCORE_THRESHOLD,
    TARGET_STREAM_EXTENSIONS,
    TARGET_STREAM_SCHEMES,
    TARGET_HLS_PATTERNS,
    CDN_HEADERS,
    OFFICIAL_DOMAINS,
    PIRACY_KEYWORDS,
    SUSPICIOUS_DOMAIN_WORDS,
    MAX_DEAD_PAGES_BEFORE_BACKTRACK,
    DEAD_END_SCORE,
    MAX_TOTAL_PAGES,
    MIN_DELAY_BETWEEN_LLM_CALLS,
)
from ..client.model import get_model
from ..client.llm import LLM
from .proxy_manager import ProxyManager
from .check import filter_live_stream_iframes, filter_live_stream_urls, is_live_stream_url

log = get_logger("spcrawler.scraper")

_NAV_RETRIES      = 2
_NAV_RETRY_DELAY  = 3.0
_BETWEEN_DFS_WAIT = 2.0


def _multi_search(keyword: str) -> list[dict]:
    seen: set[str]      = set()
    all_results: list[dict] = []

    for turn in range(DDGS_TURNS):
        query_template = DDGS_SEARCH_QUERIES[turn % len(DDGS_SEARCH_QUERIES)]
        query          = query_template.format(keyword=keyword)

        try:
            with DDGS() as ddgs:
                raw = ddgs.text(query, max_results=DDGS_PER_TURN)
                new = [
                    {"url": r["href"], "title": r.get("title", "")}
                    for r in (raw or [])
                    if r.get("href", "").startswith("http") and r["href"] not in seen
                ]
            for item in new:
                seen.add(item["url"])
            all_results.extend(new)
            log.info(
                "DDGS turn %d/%d  query='%s'  got %d new  total=%d",
                turn + 1, DDGS_TURNS, query, len(new), len(all_results),
            )
        except Exception as exc:
            log.warning("DDGS turn %d failed: %s", turn + 1, exc)

        if turn < DDGS_TURNS - 1:
            time.sleep(DDGS_TURN_DELAY)

    log.info("DDGS complete — %d unique links collected", len(all_results))
    return all_results


def _extract_page_data(result, url: str, keyword: str, parent_url: str | None) -> dict:
    html   = result.html or ""
    raw_md = ""
    if result.markdown:
        try:
            raw_md = result.markdown.raw_markdown
        except AttributeError:
            raw_md = str(result.markdown)

    text_snippet = _extract_prose(raw_md)

    all_links: list[dict] = []
    for group in ("internal", "external"):
        for link in (result.links or {}).get(group, []):
            href = link.get("href", "")
            text = link.get("text", "")
            if href.startswith("http"):
                all_links.append({"url": href, "title": text.strip()})

    seen_urls: set[str] = set()
    links_found: list[dict] = []
    for lnk in all_links:
        if lnk["url"] not in seen_urls:
            seen_urls.add(lnk["url"])
            links_found.append(lnk)
    links_found = links_found[:80]

    iframes = re.findall(r'<iframe[^>]+src=["\'"]([^"\']+)["\']', html, re.IGNORECASE)

    stream_urls: list[str] = []
    for ext in TARGET_STREAM_EXTENSIONS:
        stream_urls += re.findall(
            rf'https?://[^\s"\'<>]+{re.escape(ext)}[^\s"\'<>]*', html
        )
    for scheme in TARGET_STREAM_SCHEMES:
        stream_urls += re.findall(rf'{re.escape(scheme)}[^\s"\'<>]+', html)
    for pat in TARGET_HLS_PATTERNS:
        stream_urls += re.findall(
            rf'https?://[^\s"\'<>]+{re.escape(pat)}[^\s"\'<>]*', html
        )
    if result.network_requests:
        for req in result.network_requests:
            req_url = req.get("url", "")
            if any(x in req_url for x in (".m3u8", ".ts?", "/hls/", "/live/", "rtmp://")):
                stream_urls.append(req_url)
    stream_urls = list(dict.fromkeys(stream_urls))

    cdn_headers: dict = {}
    if result.response_headers:
        for key in CDN_HEADERS:
            val = (result.response_headers.get(key)
                   or result.response_headers.get(key.upper()))
            if val:
                cdn_headers[key] = val

    return {
        "keyword":      keyword,
        "url":          url,
        "parent_url":   parent_url,
        "title":        (result.metadata or {}).get("title", ""),
        "text_snippet": text_snippet,
        "links_found":  links_found,
        "iframes":      iframes,
        "stream_urls":  stream_urls,
        "cdn_headers":  cdn_headers,
        "score":        0,
        "crawled_at":   datetime.now(timezone.utc).isoformat(),
    }


def _extract_prose(markdown: str) -> str:
    lines = markdown.splitlines()
    prose_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        if re.match(r'^https?://', stripped):
            continue
        if re.match(r'^[-*]\s*https?://', stripped):
            continue
        prose_lines.append(stripped)
        if sum(len(l) for l in prose_lines) >= 600:
            break
    return " ".join(prose_lines)[:600].strip()


def _rule_score(page_data: dict) -> int:
    url_lower   = (page_data.get("url") or "").lower()
    text_lower  = (page_data.get("text_snippet") or "").lower()
    title_lower = (page_data.get("title") or "").lower()
    combined    = f"{url_lower} {title_lower} {text_lower}"
    score       = 0

    for domain in OFFICIAL_DOMAINS:
        if domain in url_lower:
            return 0

    if page_data.get("stream_urls"):
        score += 40
        score += min(len(page_data["stream_urls"]) * 12, 35)

    live_iframes = filter_live_stream_iframes(page_data.get("iframes", []))
    if live_iframes:
        score += 30
        score += min(len(live_iframes) * 5, 20)

    for kw in PIRACY_KEYWORDS:
        if kw in combined:
            score += 8

    try:
        domain = urlparse(page_data.get("url", "")).netloc.lower()
        for sw in SUSPICIOUS_DOMAIN_WORDS:
            if sw in domain:
                score += 10
                break
    except Exception:
        pass

    cdn = " ".join(page_data.get("cdn_headers", {}).values()).lower()
    if any(h in cdn for h in ["nginx", "cloudflare", "varnish"]):
        score += 5

    return min(score, 100)


def _stream_type(url: str) -> str:
    ul = url.lower()
    if ".m3u8" in ul or ".mpd" in ul:
        return "m3u8"
    if ul.startswith("rtmp"):
        return "rtmp"
    if ul.startswith("acestream"):
        return "acestream"
    if ul.startswith("sopcast"):
        return "sopcast"
    return "other"


class Scraper:
    def __init__(
        self,
        keyword:    str,
        api_key:    str,
        collection  = None,
        proxy_url:  str = "",
        mongo_uri:  str = "mongodb://localhost:27017",
        db_name:    str = "spcrawler",
    ) -> None:
        self.keyword      = keyword
        self._collection  = collection
        self._visited: set[str] = set()
        self._total_pages = 0

        cfg         = Config(api_key=api_key, db_name=db_name,
                             mongo_uri=mongo_uri, proxy_url=proxy_url)
        self._model = get_model(cfg)
        self._llm   = LLM(self._model)
        self._db    = Database(cfg)
        self._proxy = ProxyManager(cfg)

        self._session_id = self._db.create_session(keyword)
        self._llm_sem    = asyncio.Semaphore(1)

        log.info(
            "Scraper ready  keyword='%s'  session=%s",
            keyword, self._session_id,
        )

    @property
    def session_id(self) -> str:
        return self._session_id

    def get_streams(self) -> list[dict]:
        return self._db.get_streams(self._session_id)

    def run_sync(self) -> list[str]:
        return asyncio.run(self.run())

    async def run(self) -> list[str]:
        sid = self._session_id
        db  = self._db

        db.log(sid, "info", f"Starting multi-turn DDGS search for: '{self.keyword}'")
        all_results = await asyncio.to_thread(_multi_search, self.keyword)

        if not all_results:
            db.log(sid, "error", "DDGS returned 0 results — aborting.")
            db.finish_session(sid)
            return []

        db.log(sid, "info", f"LLM ranking {len(all_results)} links for top piracy candidates...")
        candidates = await self._llm_call(
            self._llm.pick_piracy_urls, all_results, self.keyword, 10
        )

        if not candidates:
            db.log(sid, "info", "LLM found no suspicious URLs — done.")
            db.finish_session(sid)
            return []

        log.info("LLM selected %d candidate(s):", len(candidates))
        for i, u in enumerate(candidates, 1):
            log.info("  %2d. %s", i, u)

        # One dedicated MongoDB collection per parent/candidate URL.
        # register_parent_trees seeds each collection with a depth=0 document.
        tree_map: dict[str, str] = db.register_parent_trees(sid, candidates)

        for idx, start_url in enumerate(candidates, 1):
            if self._total_pages >= MAX_TOTAL_PAGES:
                db.log(sid, "info", f"Page cap ({MAX_TOTAL_PAGES}) reached — stopping.")
                break
            col_name = tree_map[start_url]
            log.info("Candidate %d/%d  col=%s  %s", idx, len(candidates), col_name, start_url)
            db.log(sid, "info", f"Crawling candidate {idx}/{len(candidates)}: {start_url}")
            await self._dfs(start_url, col_name)
            if idx < len(candidates):
                await asyncio.sleep(_BETWEEN_DFS_WAIT)

        streams = self.get_streams()
        db.finish_session(sid, streams_found=len(streams))

        if streams:
            log_success(log, "Stream(s) found for '%s'", self.keyword)
            for s in streams:
                log_success(log, "   [%s]  %s", s["stream_type"], s["stream_url"])
        else:
            log.info("No live streams found for '%s'.", self.keyword)

        return [s["stream_url"] for s in streams]

    def _make_root_node(self, all_results: list[dict]) -> dict:
        links = [{"url": r["url"], "title": r.get("title", "")} for r in all_results]
        return {
            "keyword":      self.keyword,
            "url":          "",
            "parent_url":   None,
            "title":        f"[ROOT] {self.keyword}",
            "text_snippet": "",
            "links_found":  links,
            "iframes":      [],
            "stream_urls":  [],
            "cdn_headers":  {},
            "score":        0,
            "crawled_at":   datetime.now(timezone.utc).isoformat(),
        }

    async def _dfs(self, start_url: str, tree_col_name: str) -> None:
        proxy = self._proxy.get()
        browser_cfg = BrowserConfig(
            headless            = HEADLESS_BROWSER,
            user_agent          = USER_AGENT,
            verbose             = False,
            memory_saving_mode  = True,
            enable_stealth      = True,
            ignore_https_errors = True,
            **({"proxy": proxy["server"]} if proxy else {}),
        )
        run_cfg = CrawlerRunConfig(
            cache_mode               = CacheMode.BYPASS,
            page_timeout             = REQUEST_TIMEOUT_MS,
            screenshot               = False,
            wait_until               = "domcontentloaded",
            process_iframes          = True,
            capture_network_requests = True,
            verbose                  = False,
            word_count_threshold     = 5,
            remove_overlay_elements  = True,
            simulate_user            = True,
        )

        stack: list[tuple[str, int, int, str | None]] = [(start_url, 0, 0, None)]

        async with AsyncWebCrawler(config=browser_cfg) as crawler:
            while stack:
                if self._total_pages >= MAX_TOTAL_PAGES:
                    log.info("Global page cap reached — stopping DFS.")
                    break

                url, depth, dead_streak, parent_url = stack.pop()

                if url in self._visited:
                    continue
                if depth > MAX_DEPTH:
                    continue
                if dead_streak >= MAX_DEAD_PAGES_BEFORE_BACKTRACK:
                    log.info("Dead-streak=%d — pruning branch: %s", dead_streak, url)
                    continue

                self._visited.add(url)
                self._total_pages += 1
                log.info("Crawling  depth=%d  dead=%d  %s", depth, dead_streak, url)

                page_data = await self._crawl_page(crawler, run_cfg, url, parent_url)
                if page_data is None:
                    log.warning("Crawl failed — skipping: %s", url)
                    continue

                # Stamp depth so every stored document carries it.
                page_data["depth"] = depth

                await self._handle_ads(crawler, run_cfg, url, page_data)

                rule  = _rule_score(page_data)
                score = await self._score_page_async(page_data, rule)
                page_data["score"] = score
                self._upsert_node(page_data, tree_col_name)

                _flag = "FLAGGED" if score >= PIRACY_SCORE_THRESHOLD else "clean"
                log.info("%s  score=%d  %s", _flag, score, url)

                found_stream = await self._collect_streams(page_data, score)

                page_is_useful = (
                    found_stream
                    or bool(filter_live_stream_iframes(page_data.get("iframes", [])))
                    or bool(page_data.get("stream_urls"))
                    or score >= DEAD_END_SCORE
                )
                child_dead = 0 if page_is_useful else dead_streak + 1

                if depth < MAX_DEPTH:
                    nav = await self._llm_call(
                        self._llm.navigate,
                        page_data, url, depth, self._visited,
                        self.keyword, rule, dead_streak,
                    )
                    log.info(
                        "Nav action=%s signal=%s reason=%s",
                        nav.get("action"), nav.get("signal"), nav.get("reason"),
                    )

                    if nav.get("action") == "continue":
                        next_urls = [
                            u for u in nav.get("next_urls", [])[:2]
                            if u and u.startswith("http") and u not in self._visited
                        ]
                        for u in reversed(next_urls):
                            stack.append((u, depth + 1, child_dead, url))
                        if next_urls:
                            log.info("Queued %d URL(s): %s", len(next_urls), next_urls)

        log.info("DFS done for: %s", start_url)

    async def _handle_ads(
        self,
        crawler:   AsyncWebCrawler,
        run_cfg:   CrawlerRunConfig,
        url:       str,
        page_data: dict,
    ) -> None:
        try:
            ad_info = await self._llm_call(self._llm.check_ads, page_data)
        except Exception:
            return

        if not ad_info.get("has_ad"):
            return

        wait_sec      = int(ad_info.get("wait_seconds", 0))
        selector_hint = ad_info.get("selector_hint", "")
        action        = ad_info.get("action", "none")

        log.info("Ad detected — action=%s  wait=%ds  button='%s'",
                 action, wait_sec, selector_hint)

        if wait_sec > 0:
            await asyncio.sleep(wait_sec + 1)

        if action in ("skip", "close", "wait_and_skip") and selector_hint:
            js = f"""
                var btns = document.querySelectorAll('button, a, [role=button]');
                for (var b of btns) {{
                    if (b.innerText && b.innerText.toLowerCase().includes(
                            '{selector_hint.lower()}')) {{
                        b.click(); break;
                    }}
                }}
            """
            try:
                skip_cfg = CrawlerRunConfig(
                    cache_mode   = CacheMode.BYPASS,
                    js_code      = js,
                    page_timeout = REQUEST_TIMEOUT_MS,
                    verbose      = False,
                )
                await asyncio.wait_for(
                    crawler.arun(url=url, config=skip_cfg),
                    timeout=CRAWL_TIMEOUT_SEC,
                )
                log.info("Skip button clicked: '%s'", selector_hint)
            except Exception as exc:
                log.warning("Skip click failed: %s", exc)

    async def _collect_streams(self, page_data: dict, score: int) -> bool:
        recorded = False
        sid      = self._session_id
        source   = page_data["url"]
        context  = page_data.get("text_snippet", "")

        candidates: list[str] = []
        candidates += filter_live_stream_urls(page_data.get("stream_urls", []))
        candidates += [
            src for src in filter_live_stream_iframes(page_data.get("iframes", []))
            if is_live_stream_url(src)
        ]

        for stream_url in dict.fromkeys(candidates):
            is_live = await self._llm_call(self._llm.verify_live, stream_url, context)
            if not is_live:
                log.info("Stream rejected (not live): %s", stream_url)
                continue

            stype  = _stream_type(stream_url)
            doc_id = self._db.record_stream(
                session_id  = sid,
                stream_url  = stream_url,
                source_url  = source,
                keyword     = self.keyword,
                stream_type = stype,
                score       = score,
            )
            if doc_id:
                log_success(log, "Stream recorded  [%s]  %s", stype, stream_url)
                recorded = True

        return recorded

    def _upsert_node(self, node: dict, tree_col_name: str) -> None:
        self._db.upsert_node(self._session_id, node, tree_col_name)
        if self._collection is None or isinstance(self._collection, str):
            return
        try:
            self._collection.replace_one(
                {"url": node["url"], "session_id": self._session_id},
                {**node, "session_id": self._session_id},
                upsert=True,
            )
        except Exception as exc:
            log.warning("Failed writing to caller collection: %s", exc)

    async def _score_page_async(self, page_data: dict, rule: int) -> int:
        if rule >= LLM_SCORE_TRIGGER:
            llm_s    = await self._llm_call(self._llm.score_page, page_data, page_data["url"])
            combined = min(100, (rule + llm_s) // 2)
            log.info("Score  rule=%d  llm=%d  combined=%d", rule, llm_s, combined)
            return combined
        return rule

    async def _llm_call(self, fn, *args):
        async with self._llm_sem:
            result = await asyncio.to_thread(fn, *args)
            await asyncio.sleep(MIN_DELAY_BETWEEN_LLM_CALLS)
            return result

    async def _crawl_page(
        self,
        crawler:    AsyncWebCrawler,
        run_cfg:    CrawlerRunConfig,
        url:        str,
        parent_url: str | None,
    ) -> dict | None:
        for attempt in range(1, _NAV_RETRIES + 2):
            try:
                result = await asyncio.wait_for(
                    crawler.arun(url=url, config=run_cfg),
                    timeout=CRAWL_TIMEOUT_SEC,
                )
            except asyncio.TimeoutError:
                log.warning("Timeout crawling: %s", url)
                return None
            except Exception as exc:
                err = str(exc)
                if "navigating" in err.lower() and attempt <= _NAV_RETRIES:
                    log.warning("Still navigating (attempt %d/%d) — retrying: %s",
                                attempt, _NAV_RETRIES + 1, url)
                    await asyncio.sleep(_NAV_RETRY_DELAY)
                    continue
                log.warning("Navigation error on %s: %s", url, exc)
                return None

            if not result.success:
                err = result.error_message or ""
                if "navigating" in err.lower() and attempt <= _NAV_RETRIES:
                    log.warning("Crawl nav error (attempt %d/%d) — retrying: %s",
                                attempt, _NAV_RETRIES + 1, url)
                    await asyncio.sleep(_NAV_RETRY_DELAY)
                    continue
                log.warning("Crawl failed: %s", err)
                return None

            return _extract_page_data(result, url, self.keyword, parent_url)

        log.warning("All retries failed for: %s", url)
        return None