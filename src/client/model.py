from __future__ import annotations

import random
import threading
import time

import requests

from ..utils.config import Config
from ..utils.constants import (
    LLM_MODEL,
    LLM_MAX_TOKENS,
    LLM_TEMPERATURE,
    LLM_MAX_RETRIES,
    MIN_DELAY_BETWEEN_LLM_CALLS,
    LLM_BACKOFF_BASE,
    LLM_BACKOFF_MAX,
)
from ..log import get_logger

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

log = get_logger("spcrawler.model")

_cached_model: "Model | None" = None
_model_lock   = threading.Lock()


def get_model(cfg: Config) -> "Model":
    global _cached_model
    with _model_lock:
        if _cached_model is None:
            _cached_model = Model(cfg)
            log.info("Model instantiated and cached (api_key=…%s)", cfg.api_key[-4:])
        return _cached_model


class Model:
    def __init__(self, cfg: Config) -> None:
        self._api_key        = cfg.api_key
        self._lock           = threading.Lock()
        self._last_call_time = 0.0
        self._call_count     = 0

    def call(self, system_prompt: str, user_message: str) -> str:
        url = f"{_BASE_URL}/{LLM_MODEL}:generateContent?key={self._api_key}"
        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_message}]}],
            "generationConfig": {
                "maxOutputTokens": LLM_MAX_TOKENS,
                "temperature":     LLM_TEMPERATURE,
            },
        }

        with self._lock:
            self._throttle()
            backoff = float(LLM_BACKOFF_BASE)

            for attempt in range(1, LLM_MAX_RETRIES + 1):
                try:
                    resp = requests.post(url, json=payload, timeout=30)

                    if resp.status_code == 429:
                        wait = min(self._retry_after(resp, backoff) + random.uniform(1, 5),
                                   LLM_BACKOFF_MAX)
                        log.warning("Rate-limit 429 — waiting %ds (attempt %d/%d)",
                                    int(wait), attempt, LLM_MAX_RETRIES)
                        time.sleep(wait)
                        backoff = min(backoff * 2, LLM_BACKOFF_MAX)
                        continue

                    if 500 <= resp.status_code < 600:
                        wait = min(backoff + random.uniform(1, 5), LLM_BACKOFF_MAX)
                        log.warning("Server error %d — retry %d/%d in %ds",
                                    resp.status_code, attempt, LLM_MAX_RETRIES, int(wait))
                        time.sleep(wait)
                        backoff = min(backoff * 1.5, LLM_BACKOFF_MAX)
                        continue

                    resp.raise_for_status()
                    data = resp.json()
                    self._last_call_time = time.monotonic()
                    self._call_count    += 1
                    log.debug("LLM call #%d OK", self._call_count)
                    return data["candidates"][0]["content"]["parts"][0]["text"].strip()

                except requests.exceptions.HTTPError as exc:
                    if attempt == LLM_MAX_RETRIES:
                        log.error("LLM failed after %d attempts: %s", LLM_MAX_RETRIES, exc)
                        raise
                    wait = min(backoff + random.uniform(1, 5), LLM_BACKOFF_MAX)
                    log.warning("HTTP error — retry %d/%d in %ds: %s",
                                attempt, LLM_MAX_RETRIES, int(wait), exc)
                    time.sleep(wait)
                    backoff = min(backoff * 2, LLM_BACKOFF_MAX)

                except requests.exceptions.Timeout:
                    log.warning("LLM request timed out (attempt %d/%d)", attempt, LLM_MAX_RETRIES)
                    if attempt == LLM_MAX_RETRIES:
                        raise
                    time.sleep(min(backoff, LLM_BACKOFF_MAX))
                    backoff = min(backoff * 1.5, LLM_BACKOFF_MAX)

                except Exception as exc:
                    log.error("Unexpected LLM error: %s", exc)
                    raise

        raise RuntimeError("[model] call() exhausted retries without returning")

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call_time
        if elapsed < MIN_DELAY_BETWEEN_LLM_CALLS:
            gap = MIN_DELAY_BETWEEN_LLM_CALLS - elapsed
            log.debug("Throttle: waiting %.1fs before next LLM call", gap)
            time.sleep(gap)

    @staticmethod
    def _retry_after(resp: requests.Response, default: float) -> float:
        header = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
        if header:
            try:
                return float(header)
            except ValueError:
                pass
        return default