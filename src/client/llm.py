from __future__ import annotations

import json
import re

from .model import Model
from . import prompts



class LLM:
    def __init__(self, model: Model) -> None:
        self._model = model

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
            f'  [{lnk.get("text", "").strip()[:50]}] -> {lnk.get("url", "")}'
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
            return {"action": "stop", "next_urls": [], "reason": f"parse error: {exc}", "signal": "none"}

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
        except Exception:
            return 0

    def check_ads(self, page_data: dict) -> dict:
        snippet = (page_data.get("text_snippet") or "")[:600]
        html    = page_data.get("_raw_html", "")  # optional raw HTML if available

        buttons = re.findall(
            r'\b(skip|close|continue|proceed|dismiss|allow|accept|skip\s*ad|'
            r'skip\s*now|skip\s*in|watch\s*now|click\s*here|proceed)\b',
            snippet, re.IGNORECASE,
        )

        # Detect onclick redirect signals in raw HTML
        onclicks: list[str] = []
        if html:
            onclicks = list(dict.fromkeys(
                re.findall(r'onclick=["\'][^"\']{0,120}["\']', html, re.IGNORECASE)
            ))[:5]

        # Detect ad-network redirect URLs
        redirects: list[str] = []
        if html:
            redirect_domains = [
                "adf.ly", "ouo.io", "linkvertise", "sh.st", "bc.vc",
                "gestyy", "shorte.st", "zipansion", "cpmlink",
            ]
            for dom in redirect_domains:
                if dom in html.lower():
                    redirects.append(dom)

        payload = prompts.AD_CHECK_USER.format(
            title     = page_data.get("title", ""),
            snippet   = snippet[:400],
            buttons   = list(dict.fromkeys(b.lower() for b in buttons)),
            redirects = redirects,
            onclicks  = onclicks[:3],
        )
        try:
            raw   = self._model.call(prompts.AD_CHECK_SYSTEM, payload).strip()
            clean = re.sub(r"```[a-z]*|```", "", raw).strip()
            data  = json.loads(clean)
            data.setdefault("has_ad", False)
            data.setdefault("ad_type", "none")
            data.setdefault("action", "none")
            data.setdefault("wait_seconds", 0)
            data.setdefault("selector_hint", "")
            data.setdefault("js_snippet", "")
            return data
        except Exception:
            return {
                "has_ad": False, "ad_type": "none", "action": "none",
                "wait_seconds": 0, "selector_hint": "", "js_snippet": "",
            }

    def verify_live(self, url: str, context: str = "") -> bool:
        payload = f"URL: {url}\nContext: {context[:300]}"
        try:
            raw = self._model.call(prompts.VERIFY_LIVE_SYSTEM, payload).strip().upper()
            return raw.startswith("LIVE") and "NOT" not in raw
        except Exception:
            return False