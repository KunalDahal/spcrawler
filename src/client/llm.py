from __future__ import annotations

import json
import re

from .model import Model
from . import prompts
from ..log import get_logger

log = get_logger("spcrawler.llm")


class LLM:
    def __init__(self, model: Model) -> None:
        self._model = model

    def pick_piracy_urls(
        self,
        search_results: list[dict],
        keyword: str,
        n: int = 10,
    ) -> list[str]:
        results_text = "\n".join(
            f"- {r['url']}  |  {r.get('title', '')}" for r in search_results
        )
        payload = prompts.PICK_PIRACY_URLS_USER.format(
            keyword=keyword,
            n=len(search_results),
            results=results_text,
        )
        raw = self._model.call(prompts.PICK_PIRACY_URLS_SYSTEM, payload).strip()
        try:
            clean = re.sub(r"```[a-z]*|```", "", raw).strip()
            urls  = json.loads(clean)
            if isinstance(urls, list):
                return [u for u in urls if isinstance(u, str) and u.startswith("http")][:n]
        except Exception:
            pass
        found = re.findall(r'https?://[^\s"\'<>,\]]+', raw)
        return found[:n]

    def navigate(
        self,
        page_data:    dict,
        current_url:  str,
        depth:        int,
        visited_urls: set[str],
        keyword:      str = "",
        rule_score:   int = 0,
        dead_streak:  int = 0,
    ) -> dict:
        links: list[dict] = page_data.get("links_found", [])
        links_lines = "\n".join(
            f'  [{lnk.get("text", "").strip()[:50]}] → {lnk.get("url", "")}'
            for lnk in links[:30]
        ) or "  (none)"

        payload = prompts.NAVIGATE_USER_TEMPLATE.format(
            keyword      = keyword,
            url          = current_url,
            depth        = depth,
            title        = page_data.get("title", ""),
            snippet      = (page_data.get("text_snippet") or "")[:400],
            link_count   = len(links),
            links_json   = links_lines,
            iframes      = page_data.get("iframes", []),
            scheme_urls  = page_data.get("stream_urls", []),
            visited_json = json.dumps(list(visited_urls)[:30]),
            rule_score   = rule_score,
            dead_streak  = dead_streak,
        )
        raw = self._model.call(prompts.NAVIGATE_SYSTEM, payload)
        try:
            clean = re.sub(r"```[a-z]*|```", "", raw).strip()
            data  = json.loads(clean)
            if "next_url" in data and "next_urls" not in data:
                nxt = data["next_url"]
                data["next_urls"] = [nxt] if nxt else []
            data.setdefault("next_urls", [])
            data.setdefault("signal", "none")
            return data
        except Exception as exc:
            log.warning("navigate parse error: %s | raw: %s", exc, raw[:200])
            return {"action": "stop", "next_urls": [], "reason": "parse error", "signal": "none"}

    def score_page(self, page_data: dict, task_url: str) -> int:
        payload = (
            f"URL: {task_url}\n"
            f"Title: {page_data.get('title', '')}\n"
            f"Text snippet: {(page_data.get('text_snippet') or '')[:400]}\n"
            f"Iframes found: {len(page_data.get('iframes', []))}\n"
            f"Stream URLs: {page_data.get('stream_urls', [])}\n"
            f"CDN headers: {page_data.get('cdn_headers', {})}\n"
        )
        try:
            raw   = self._model.call(prompts.SCORE_SYSTEM, payload)
            match = re.search(r"\d+", raw)
            return max(0, min(100, int(match.group()))) if match else 0
        except Exception as exc:
            log.error("score_page failed: %s", exc)
            return 0

    def check_ads(self, page_data: dict) -> dict:
        snippet = (page_data.get("text_snippet") or "")[:600]
        buttons = re.findall(r'\b(skip|close|continue|proceed|dismiss|allow|accept)\b',
                             snippet, re.IGNORECASE)
        payload = prompts.AD_CHECK_USER.format(
            title   = page_data.get("title", ""),
            snippet = snippet[:400],
            buttons = list(dict.fromkeys(b.lower() for b in buttons)),
        )
        try:
            raw   = self._model.call(prompts.AD_CHECK_SYSTEM, payload).strip()
            clean = re.sub(r"```[a-z]*|```", "", raw).strip()
            data  = json.loads(clean)
            data.setdefault("has_ad", False)
            data.setdefault("action", "none")
            data.setdefault("wait_seconds", 0)
            data.setdefault("selector_hint", "")
            return data
        except Exception as exc:
            log.warning("check_ads parse error: %s", exc)
            return {"has_ad": False, "action": "none", "wait_seconds": 0, "selector_hint": ""}

    def verify_live(self, url: str, context: str = "") -> bool:
        payload = f"URL: {url}\nContext: {context[:300]}"
        try:
            raw = self._model.call(prompts.VERIFY_LIVE_SYSTEM, payload).strip().upper()
            return raw.startswith("LIVE") and "NOT" not in raw
        except Exception as exc:
            log.error("verify_live failed: %s", exc)
            return False