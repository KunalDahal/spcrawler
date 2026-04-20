from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable

AsyncHandler = Callable[["Event"], Awaitable[None]]


class E:
    SESSION_CREATED   = "session.created"
    SESSION_FINISHED  = "session.finished"

    SEARCH_START      = "search.start"
    SEARCH_TURN_DONE  = "search.turn_done"
    SEARCH_COMPLETE   = "search.complete"

    SEARCH_CANDIDATES = "search.candidates"
    LLM_NAVIGATE      = "llm.navigate"
    LLM_SCORE         = "llm.score"
    LLM_VERIFY_LIVE   = "llm.verify_live"
    LLM_AD_CHECK      = "llm.ad_check"
    LLM_CLASSIFY      = "llm.classify"

    CRAWL_TREE_START  = "crawl.tree_start"
    CRAWL_TREE_DONE   = "crawl.tree_done"
    CRAWL_PAGE_START  = "crawl.page_start"
    CRAWL_PAGE_DONE   = "crawl.page_done"
    CRAWL_PAGE_FAIL   = "crawl.page_fail"
    CRAWL_AD_DETECTED = "crawl.ad_detected"
    CRAWL_AD_HANDLED  = "crawl.ad_handled"

    DB_TREES_REGISTERED = "db.trees_registered"
    DB_NODE_UPSERTED    = "db.node_upserted"
    DB_STREAM_RECORDED  = "db.stream_recorded"

    STREAM_FOUND    = "stream.found"
    STREAM_REJECTED = "stream.rejected"

    ERROR = "error"


@dataclass
class Event:
    type:       str
    session_id: str
    data:       dict
    ts: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def as_dict(self) -> dict:
        return asdict(self)


class EventBus:
    def __init__(self, maxsize: int = 2_000) -> None:
        self._maxsize   = maxsize
        self._queue:    asyncio.Queue[Event | None] | None = None
        self._handlers: list[AsyncHandler]                 = []
        self._task:     asyncio.Task | None                = None
        self._loop:     asyncio.AbstractEventLoop | None   = None

    def subscribe(self, handler: AsyncHandler) -> "EventBus":
        self._handlers.append(handler)
        return self

    async def start(self) -> None:
        self._loop  = asyncio.get_running_loop()
        self._queue = asyncio.Queue(maxsize=self._maxsize)
        self._task  = asyncio.create_task(self._consume(), name="spcrawler-eventbus")

    async def stop(self) -> None:
        if self._task is None or self._queue is None:
            return
        await self._queue.put(None)
        await self._task
        self._task = None

    def emit(self, event: Event) -> None:
        if self._queue is None:
            return
        if self._loop is not None and self._loop.is_running():
            try:
                self._loop.call_soon_threadsafe(self._queue.put_nowait, event)
            except (asyncio.QueueFull, RuntimeError):
                pass
        else:
            try:
                self._queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def _consume(self) -> None:
        assert self._queue is not None
        while True:
            event = await self._queue.get()
            self._queue.task_done()
            if event is None:
                break
            for handler in self._handlers:
                try:
                    await handler(event)
                except Exception:
                    pass