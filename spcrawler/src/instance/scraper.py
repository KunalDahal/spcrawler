from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
from ddgs import DDGS

from ..events import E, Event, EventBus, AsyncHandler
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
    MAX_DEAD_PAGES_BEFORE_BACKTRACK,
    DEAD_END_SCORE,
    MAX_TOTAL_PAGES,
    MIN_DELAY_BETWEEN_LLM_CALLS,
    MAX_STREAMS_PER_PLAYER,
)
from ..client.model import get_model
from ..client.llm import LLM
from .proxy_manager import ProxyManager
from .check import (
    filter_live_stream_iframes,
    filter_live_stream_urls,
    is_live_stream_url,
    is_vod_url,
    extract_players_with_streams,
)

_NAV_RETRIES      = 2
_NAV_RETRY_DELAY  = 3.0
_BETWEEN_DFS_WAIT = 2.0


def _multi_search(
    keyword: str,
    on_turn: "((int, int, str, int, int) -> None) | None" = None,
) -> list[dict]:
    seen:        set[str]   = set()
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
            if on_turn:
                on_turn(turn + 1, DDGS_TURNS, query, len(new), len(all_results))
        except Exception:
            if on_turn:
                on_turn(turn + 1, DDGS_TURNS, query, 0, len(all_results))

        if turn < DDGS_TURNS - 1:
            time.sleep(DDGS_TURN_DELAY)

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

    seen_urls:   set[str]   = set()
    links_found: list[dict] = []
    for lnk in all_links:
        if lnk["url"] not in seen_urls:
            seen_urls.add(lnk["url"])
            links_found.append(lnk)
    links_found = links_found[:80]

    iframes = re.findall(r'<iframe[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE)

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
        "_raw_html":    html[:30_000],
        "score":        0,
        "crawled_at":   datetime.now(timezone.utc).isoformat(),
        "is_ad_page":   False,
        "is_player_page": False,
        "is_official":  False,
        "is_suspicious": False,
    }


def _extract_prose(markdown: str) -> str:
    lines       = markdown.splitlines()
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

    if page_data.get("is_official"):
        return 0

    if page_data.get("stream_urls"):
        score += 40
        score += min(len(page_data["stream_urls"]) * 12, 35)

    live_iframes = filter_live_stream_iframes(page_data.get("iframes", []))
    if live_iframes:
        score += 30
        score += min(len(live_iframes) * 5, 20)

    for kw in (page_data.get("relevant_keywords") or []):
        if kw.lower() in combined:
            score += 8

    if page_data.get("is_suspicious"):
        score += 10

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
        keyword:   str,
        api_key:   str,
        collection = None,
        proxy_url: str = "",
        mongo_uri: str = "mongodb://localhost:27017",
        db_name:   str = "spcrawler",
        seed_url:  str = "",
    ) -> None:
        self.keyword      = keyword
        self._collection  = collection
        self._visited:    set[str] = set()
        self._total_pages = 0
        self._seed_url    = seed_url

        self._bus = EventBus()

        cfg         = Config(api_key=api_key, db_name=db_name,
                             mongo_uri=mongo_uri, proxy_url=proxy_url)
        self._model = get_model(cfg, emit=self._emit_raw)
        self._llm   = LLM(self._model)
        self._db    = Database(cfg, bus=self._bus)
        self._proxy = ProxyManager(cfg)

        self._session_id = self._db.create_session(keyword)
        self._llm_sem    = asyncio.Semaphore(1)

    @property
    def session_id(self) -> str:
        return self._session_id

    def subscribe(self, handler: AsyncHandler) -> "Scraper":
        self._bus.subscribe(handler)
        return self

    def on_event(self, handler: AsyncHandler) -> AsyncHandler:
        self._bus.subscribe(handler)
        return handler

    def get_streams(self) -> list[dict]:
        return self._db.get_streams(self._session_id)

    def run_sync(self) -> list[str]:
        return asyncio.run(self.run())

    async def run(self) -> list[str]:
        await self._bus.start()
        try:
            return await self._run()
        finally:
            await self._bus.stop()

    async def _run(self) -> list[str]:
        sid = self._session_id
        db  = self._db

        self._emit(E.SESSION_CREATED, {"keyword": self.keyword, "session_id": sid})
        self._emit(E.SEARCH_START, {"keyword": self.keyword, "turns": DDGS_TURNS})

        all_results = await asyncio.to_thread(
            _multi_search, self.keyword, self._make_ddgs_hook()
        )

        self._emit(E.SEARCH_COMPLETE, {
            "keyword":       self.keyword,
            "total_results": len(all_results),
        })

        if not all_results:
            db.finish_session(sid)
            self._emit(E.SESSION_FINISHED, {
                "keyword": self.keyword, "streams_found": 0,
                "pages_crawled": 0, "streams": [],
            })
            return []

        candidates = [r["url"] for r in all_results]

        self._emit(E.SEARCH_CANDIDATES, {
            "total":      len(candidates),
            "candidates": candidates,
        })

        tree_map: dict[str, str] = db.register_parent_trees(sid, candidates)

        for idx, start_url in enumerate(candidates, 1):
            if self._total_pages >= MAX_TOTAL_PAGES:
                break
            col_name = tree_map[start_url]
            await self._crawl_tree(start_url, col_name)
            if idx < len(candidates):
                await asyncio.sleep(_BETWEEN_DFS_WAIT)

        streams = self.get_streams()
        db.finish_session(sid, streams_found=len(streams))

        self._emit(E.SESSION_FINISHED, {
            "keyword":       self.keyword,
            "streams_found": len(streams),
            "pages_crawled": self._total_pages,
            "streams":       [s["stream_url"] for s in streams],
        })

        return [s["stream_url"] for s in streams]

    async def _crawl_tree(self, start_url: str, tree_col_name: str) -> None:
        self._emit(E.CRAWL_TREE_START, {
            "start_url":    start_url,
            "tree_col":     tree_col_name,
            "pages_so_far": self._total_pages,
        })

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
                    break

                url, depth, dead_streak, parent_url = stack.pop()

                if url in self._visited:
                    continue
                if depth > MAX_DEPTH:
                    continue

                if dead_streak >= MAX_DEAD_PAGES_BEFORE_BACKTRACK:
                    self._emit(E.CRAWL_PAGE_FAIL, {
                        "url": url, "depth": depth,
                        "tree_col": tree_col_name,
                        "reason": "dead_streak_backtrack",
                    })
                    continue

                self._visited.add(url)
                self._total_pages += 1

                self._emit(E.CRAWL_PAGE_START, {
                    "url":         url,
                    "depth":       depth,
                    "dead_streak": dead_streak,
                    "parent_url":  parent_url,
                    "tree_col":    tree_col_name,
                })

                page_data = await self._crawl_page(crawler, run_cfg, url, parent_url)
                if page_data is None:
                    self._emit(E.CRAWL_PAGE_FAIL, {
                        "url": url, "depth": depth, "tree_col": tree_col_name,
                        "reason": "crawl_failed",
                    })
                    continue

                page_data["depth"] = depth

                classification = await self._llm_call(self._llm.classify_page, page_data)
                page_data["is_official"]        = classification.get("is_official", False)
                page_data["is_suspicious"]      = classification.get("is_suspicious", False)
                page_data["is_player_page"]     = classification.get("is_player_page", False)
                page_data["is_piracy_host"]     = classification.get("is_piracy_host", False)
                page_data["relevant_keywords"]  = classification.get("relevant_keywords", [])

                self._emit(E.LLM_CLASSIFY, {
                    "url":              url,
                    "is_official":      page_data["is_official"],
                    "is_suspicious":    page_data["is_suspicious"],
                    "is_player_page":   page_data["is_player_page"],
                    "is_piracy_host":   page_data["is_piracy_host"],
                    "reason":           classification.get("reason", ""),
                    "tree_col":         tree_col_name,
                })

                if page_data["is_official"]:
                    self._emit(E.CRAWL_PAGE_FAIL, {
                        "url": url, "depth": depth,
                        "tree_col": tree_col_name,
                        "reason": "official_domain_blocked",
                    })
                    continue

                ad_info = await self._handle_ads(crawler, run_cfg, url, page_data)

                if ad_info.get("is_ad_page", False):
                    self._emit(E.CRAWL_PAGE_FAIL, {
                        "url": url, "depth": depth,
                        "tree_col": tree_col_name,
                        "reason": "ad_page_discarded",
                    })
                    continue

                rule  = _rule_score(page_data)
                score = await self._score_page_async(page_data, rule, tree_col_name)
                page_data["score"] = score

                self._upsert_node(page_data, tree_col_name)

                self._emit(E.CRAWL_PAGE_DONE, {
                    "url":            url,
                    "depth":          depth,
                    "score":          score,
                    "flagged":        score >= PIRACY_SCORE_THRESHOLD,
                    "title":          page_data.get("title", ""),
                    "stream_urls":    page_data.get("stream_urls", []),
                    "iframes":        page_data.get("iframes", []),
                    "links_found":    len(page_data.get("links_found", [])),
                    "tree_col":       tree_col_name,
                    "is_player_page": page_data.get("is_player_page", False),
                    "is_suspicious":  page_data.get("is_suspicious", False),
                    "parent_url":     parent_url,
                })

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
                    self._emit(E.LLM_NAVIGATE, {
                        "url":        url,
                        "depth":      depth,
                        "action":     nav.get("action"),
                        "signal":     nav.get("signal"),
                        "reason":     nav.get("reason"),
                        "next_urls":  nav.get("next_urls", []),
                        "tree_col":   tree_col_name,
                        "parent_url": url,
                    })

                    if nav.get("action") == "continue":
                        next_urls = [
                            u for u in nav.get("next_urls", [])[:2]
                            if u and u.startswith("http") and u not in self._visited
                        ]
                        for u in reversed(next_urls):
                            stack.append((u, depth + 1, child_dead, url))

        self._emit(E.CRAWL_TREE_DONE, {
            "start_url":     start_url,
            "tree_col":      tree_col_name,
            "pages_crawled": self._total_pages,
        })

    async def _handle_ads(
        self,
        crawler:   AsyncWebCrawler,
        run_cfg:   CrawlerRunConfig,
        url:       str,
        page_data: dict,
    ) -> dict:
        try:
            ad_info = await self._llm_call(self._llm.check_ads, page_data)
        except Exception:
            return {"has_ad": False, "is_ad_page": False}

        self._emit(E.LLM_AD_CHECK, {
            "url":           url,
            "has_ad":        ad_info.get("has_ad", False),
            "is_ad_page":    ad_info.get("is_ad_page", False),
            "ad_type":       ad_info.get("ad_type", "none"),
            "action":        ad_info.get("action", "none"),
            "wait_seconds":  ad_info.get("wait_seconds", 0),
            "selector_hint": ad_info.get("selector_hint", ""),
        })

        if ad_info.get("is_ad_page", False):
            return ad_info

        if not ad_info.get("has_ad"):
            return ad_info

        wait_sec      = int(ad_info.get("wait_seconds", 0))
        selector_hint = ad_info.get("selector_hint", "")
        action        = ad_info.get("action", "none")
        ad_type       = ad_info.get("ad_type", "none")
        js_snippet    = ad_info.get("js_snippet", "")

        self._emit(E.CRAWL_AD_DETECTED, {
            "url": url, "ad_type": ad_type, "action": action,
            "wait_seconds": wait_sec, "selector_hint": selector_hint,
        })

        if wait_sec > 0:
            await asyncio.sleep(min(wait_sec + 1, 15))

        if action == "click_through" or ad_type in ("onclick_redirect", "fake_play", "redirect_page"):
            dismiss_js = """
                window._origOpen = window.open;
                window.open = function() { return null; };
                var adContainers = document.querySelectorAll(
                    '[id*=ad],[class*=ad],[id*=overlay],[class*=overlay]'
                );
                adContainers.forEach(function(el) {
                    el.removeAttribute('onclick');
                    el.style.display = 'none';
                });
                var player = document.querySelector(
                    'video, iframe[src*=stream], iframe[src*=embed], '
                    + 'iframe[src*=player], [id*=player], [class*=player]'
                );
                if (player) { player.click(); }
            """
        elif action in ("skip", "close", "wait_and_skip"):
            hint_lower = selector_hint.lower().replace('"', '\\"')
            dismiss_js = f"""
                var found = false;
                var selectors = [
                    '[id*=skip],[class*=skip]',
                    '[id*=close],[class*=close]',
                    '.skip-btn,.skip-button,.skipbtn,.ad-skip',
                    '.closeBtn,.close-btn,[aria-label*=close],[aria-label*=skip]',
                    'button,a,[role=button]'
                ];
                for (var s of selectors) {{
                    var els = document.querySelectorAll(s);
                    for (var el of els) {{
                        var txt = (el.innerText || el.textContent || '').toLowerCase();
                        if (txt.includes('{hint_lower}') || txt.includes('skip')
                                || txt.includes('close') || txt.includes('continue')) {{
                            el.click();
                            found = true;
                            break;
                        }}
                    }}
                    if (found) break;
                }}
                var overlays = document.querySelectorAll(
                    '.overlay,.modal,.popup,.ad-overlay,[id*=overlay],[class*=popup]'
                );
                overlays.forEach(function(el) {{ el.style.display = 'none'; }});
            """
        elif js_snippet:
            dismiss_js = js_snippet
        else:
            dismiss_js = ""

        if dismiss_js:
            try:
                skip_cfg = CrawlerRunConfig(
                    cache_mode               = CacheMode.BYPASS,
                    js_code                  = dismiss_js,
                    page_timeout             = REQUEST_TIMEOUT_MS,
                    verbose                  = False,
                    wait_until               = "domcontentloaded",
                    capture_network_requests = True,
                )
                result = await asyncio.wait_for(
                    crawler.arun(url=url, config=skip_cfg),
                    timeout=CRAWL_TIMEOUT_SEC,
                )
                self._emit(E.CRAWL_AD_HANDLED, {
                    "url": url, "ad_type": ad_type,
                    "selector_hint": selector_hint, "success": result.success,
                })
                if result.success and result.network_requests:
                    existing = set(page_data.get("stream_urls", []))
                    for req in result.network_requests:
                        req_url = req.get("url", "")
                        if (req_url not in existing and
                                any(x in req_url for x in
                                    (".m3u8", ".ts?", "/hls/", "/live/", "rtmp://"))):
                            page_data.setdefault("stream_urls", []).append(req_url)
                            existing.add(req_url)
            except Exception as exc:
                self._emit(E.CRAWL_AD_HANDLED, {
                    "url": url, "ad_type": ad_type,
                    "selector_hint": selector_hint,
                    "success": False, "error": str(exc),
                })

        return ad_info

    async def _collect_streams(self, page_data: dict, score: int) -> bool:
        recorded = False
        source   = page_data["url"]
        context  = page_data.get("text_snippet", "")

        player_map = extract_players_with_streams(page_data, max_per_player=MAX_STREAMS_PER_PLAYER)

        if not player_map:
            return False

        for player_id, candidates in player_map.items():
            player_recorded: list[str] = []
            for stream_url in candidates:
                if len(player_recorded) >= MAX_STREAMS_PER_PLAYER:
                    break

                if is_vod_url(stream_url):
                    self._emit(E.STREAM_REJECTED, {
                        "stream_url": stream_url,
                        "source_url": source,
                        "player_id":  player_id,
                        "reason":     "vod_url_pattern",
                    })
                    continue

                is_live = await self._llm_call(self._llm.verify_live, stream_url, context)

                self._emit(E.LLM_VERIFY_LIVE, {
                    "stream_url": stream_url,
                    "source_url": source,
                    "player_id":  player_id,
                    "is_live":    is_live,
                })

                if not is_live:
                    self._emit(E.STREAM_REJECTED, {
                        "stream_url": stream_url,
                        "source_url": source,
                        "player_id":  player_id,
                        "reason":     "llm_rejected",
                    })
                    continue

                stype  = _stream_type(stream_url)
                doc_id = self._db.record_stream(
                    session_id  = self._session_id,
                    stream_url  = stream_url,
                    source_url  = source,
                    keyword     = self.keyword,
                    stream_type = stype,
                    score       = score,
                    player_id   = player_id,
                )
                if doc_id:
                    player_recorded.append(stream_url)
                    self._emit(E.STREAM_FOUND, {
                        "stream_url":  stream_url,
                        "source_url":  source,
                        "stream_type": stype,
                        "score":       score,
                        "player_id":   player_id,
                        "doc_id":      doc_id,
                    })
                    recorded = True

        return recorded

    def _upsert_node(self, node: dict, tree_col_name: str) -> None:
        db_node = {k: v for k, v in node.items() if k != "_raw_html"}
        self._db.upsert_node(self._session_id, db_node, tree_col_name)
        if self._collection is None or isinstance(self._collection, str):
            return
        try:
            self._collection.replace_one(
                {"url": node["url"], "session_id": self._session_id},
                {**db_node, "session_id": self._session_id},
                upsert=True,
            )
        except Exception as exc:
            self._emit(E.ERROR, {
                "context": "upsert_node_caller_collection",
                "error":   str(exc),
            })

    async def _score_page_async(self, page_data: dict, rule: int, tree_col: str) -> int:
        if rule >= LLM_SCORE_TRIGGER:
            llm_s    = await self._llm_call(self._llm.score_page, page_data, page_data["url"])
            combined = min(100, (rule + llm_s) // 2)
            self._emit(E.LLM_SCORE, {
                "url":      page_data["url"],
                "rule":     rule,
                "llm":      llm_s,
                "combined": combined,
                "tree_col": tree_col,
            })
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
                self._emit(E.ERROR, {"context": "crawl_timeout", "url": url})
                return None
            except Exception as exc:
                err = str(exc)
                if "navigating" in err.lower() and attempt <= _NAV_RETRIES:
                    await asyncio.sleep(_NAV_RETRY_DELAY)
                    continue
                self._emit(E.ERROR, {"context": "crawl_exception", "url": url, "error": err})
                return None

            if not result.success:
                err = result.error_message or ""
                if "navigating" in err.lower() and attempt <= _NAV_RETRIES:
                    await asyncio.sleep(_NAV_RETRY_DELAY)
                    continue
                self._emit(E.ERROR, {"context": "crawl_failed", "url": url, "error": err})
                return None

            return _extract_page_data(result, url, self.keyword, parent_url)

        self._emit(E.ERROR, {"context": "crawl_all_retries_failed", "url": url})
        return None

    def _emit(self, event_type: str, data: dict) -> None:
        self._bus.emit(Event(
            type       = event_type,
            session_id = self._session_id,
            data       = data,
        ))

    def _emit_raw(self, event_type: str, data: dict) -> None:
        self._emit(event_type, data)

    def _make_ddgs_hook(self):
        sid = self._session_id
        bus = self._bus

        def on_turn(turn: int, total: int, query: str, new_count: int, total_count: int) -> None:
            bus.emit(Event(
                type       = E.SEARCH_TURN_DONE,
                session_id = sid,
                data       = {
                    "turn":        turn,
                    "total_turns": total,
                    "query":       query,
                    "new_results": new_count,
                    "total":       total_count,
                },
            ))

        return on_turn