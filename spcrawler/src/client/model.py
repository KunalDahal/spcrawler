from __future__ import annotations

import random
import threading
import time
from typing import Callable

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

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

_model_cache: dict[str, "Model"] = {}
_model_lock  = threading.Lock()

Emitter = Callable[[str, dict], None]


def _noop(event_type: str, data: dict) -> None:
    pass


def get_model(cfg: Config, emit: Emitter = _noop) -> "Model":
    with _model_lock:
        key = cfg.api_key
        if key not in _model_cache:
            _model_cache[key] = Model(cfg, emit)
        return _model_cache[key]


class Model:
    def __init__(self, cfg: Config, emit: Emitter = _noop) -> None:
        self._api_key        = cfg.api_key
        self._emit           = emit
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
                        wait = min(
                            self._retry_after(resp, backoff) + random.uniform(1, 5),
                            LLM_BACKOFF_MAX,
                        )
                        self._emit("llm.rate_limit", {
                            "wait_seconds": int(wait),
                            "attempt":      attempt,
                            "max_retries":  LLM_MAX_RETRIES,
                        })
                        time.sleep(wait)
                        backoff = min(backoff * 2, LLM_BACKOFF_MAX)
                        continue

                    if 500 <= resp.status_code < 600:
                        wait = min(backoff + random.uniform(1, 5), LLM_BACKOFF_MAX)
                        self._emit("llm.server_error", {
                            "status_code": resp.status_code,
                            "attempt":     attempt,
                            "max_retries": LLM_MAX_RETRIES,
                            "wait_seconds": int(wait),
                        })
                        time.sleep(wait)
                        backoff = min(backoff * 1.5, LLM_BACKOFF_MAX)
                        continue

                    resp.raise_for_status()
                    data = resp.json()
                    self._last_call_time  = time.monotonic()
                    self._call_count     += 1
                    self._emit("llm.call_ok", {"call_count": self._call_count})
                    return data["candidates"][0]["content"]["parts"][0]["text"].strip()

                except requests.exceptions.HTTPError as exc:
                    if attempt == LLM_MAX_RETRIES:
                        self._emit("llm.failed", {
                            "attempts": LLM_MAX_RETRIES,
                            "error":    str(exc),
                        })
                        raise
                    wait = min(backoff + random.uniform(1, 5), LLM_BACKOFF_MAX)
                    self._emit("llm.http_error", {
                        "attempt":      attempt,
                        "max_retries":  LLM_MAX_RETRIES,
                        "wait_seconds": int(wait),
                        "error":        str(exc),
                    })
                    time.sleep(wait)
                    backoff = min(backoff * 2, LLM_BACKOFF_MAX)

                except requests.exceptions.Timeout:
                    self._emit("llm.timeout", {
                        "attempt":     attempt,
                        "max_retries": LLM_MAX_RETRIES,
                    })
                    if attempt == LLM_MAX_RETRIES:
                        raise
                    time.sleep(min(backoff, LLM_BACKOFF_MAX))
                    backoff = min(backoff * 1.5, LLM_BACKOFF_MAX)

                except Exception as exc:
                    self._emit("llm.unexpected_error", {"error": str(exc)})
                    raise

        raise RuntimeError("call() exhausted retries without returning")

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call_time
        if elapsed < MIN_DELAY_BETWEEN_LLM_CALLS:
            time.sleep(MIN_DELAY_BETWEEN_LLM_CALLS - elapsed)

    @staticmethod
    def _retry_after(resp: requests.Response, default: float) -> float:
        header = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
        if header:
            try:
                return float(header)
            except ValueError:
                pass
        return default