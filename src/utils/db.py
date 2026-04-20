from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pymongo import MongoClient, DESCENDING
from pymongo.collection import Collection

from .config import Config
from .constants import PIRACY_SCORE_THRESHOLD


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tree_col_name(session_id: str, index: int) -> str:
    """
    Deterministic collection name for one parent-URL tree.
    Format:  tree_<session_id>_<zero-padded index>
    """
    return f"tree_{session_id}_{index:02d}"


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

class Database:
    def __init__(self, cfg: Config) -> None:
        self._client = MongoClient(cfg.mongo_uri)
        self._db     = self._client[cfg.db_name]

    # ── Low-level helpers ──────────────────────────────────────────────────

    def _col(self, name: str) -> Collection:
        return self._db[name]

    def _indexed_col(self, name: str) -> Collection:
        """Return a collection with standard indexes pre-created."""
        c = self._db[name]
        c.create_index("url",                         background=True)
        c.create_index([("score",  DESCENDING)],      background=True)
        c.create_index([("depth",  1)],               background=True)
        c.create_index([("crawled_at", DESCENDING)],  background=True)
        return c

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    # ── Sessions ───────────────────────────────────────────────────────────

    def create_session(self, keyword: str) -> str:
        doc = {
            "keyword":       keyword,
            "status":        "running",
            "started_at":    self._now(),
            "finished_at":   None,
            "streams_found": 0,
            "pages_crawled": 0,
            "flagged_count": 0,
            # Populated by register_parent_trees once LLM picks are ready.
            "parent_trees":  [],
        }
        result = self._db["sessions"].insert_one(doc)
        return str(result.inserted_id)

    # ── Parent-tree registration ───────────────────────────────────────────

    def register_parent_trees(
        self,
        session_id:  str,
        parent_urls: list[str],
    ) -> dict[str, str]:
        """
        Called once per session, right after the LLM picks the N candidate
        (parent) URLs from the DDGS results.

        For every parent URL:
          • a dedicated MongoDB collection is created
          • a seed document (depth=0, status="pending") is inserted so the
            parent URL is always visible in its own tree even before crawling

        Returns a mapping  {parent_url: collection_name}  that the scraper
        uses to route upsert_node calls to the correct collection.
        """
        from bson import ObjectId

        trees:   list[dict]      = []
        mapping: dict[str, str]  = {}

        for idx, url in enumerate(parent_urls):
            col_name = _tree_col_name(session_id, idx)
            trees.append({
                "index":      idx,
                "parent_url": url,
                "collection": col_name,
            })
            mapping[url] = col_name

            # Seed the parent node so it exists at depth=0 immediately.
            self._indexed_col(col_name).replace_one(
                {"url": url},
                {
                    "url":          url,
                    "depth":        0,          # ← root of this sub-tree
                    "parent_url":   None,
                    "keyword":      "",         # filled in when crawled
                    "title":        "",
                    "text_snippet": "",
                    "links_found":  [],
                    "iframes":      [],
                    "stream_urls":  [],
                    "cdn_headers":  {},
                    "score":        0,
                    "crawled_at":   None,       # None = not yet crawled
                    "status":       "pending",
                },
                upsert=True,
            )

        self._db["sessions"].update_one(
            {"_id": ObjectId(session_id)},
            {"$set": {"parent_trees": trees}},
        )
        return mapping

    # ── Session helpers ────────────────────────────────────────────────────

    def update_session(self, session_id: str, **fields) -> None:
        from bson import ObjectId
        self._db["sessions"].update_one(
            {"_id": ObjectId(session_id)},
            {"$set": fields},
        )

    def finish_session(self, session_id: str, streams_found: int = 0) -> None:
        self.update_session(
            session_id,
            status        = "done",
            finished_at   = self._now(),
            streams_found = streams_found,
        )

    def get_session(self, session_id: str) -> dict | None:
        from bson import ObjectId
        doc = self._db["sessions"].find_one({"_id": ObjectId(session_id)})
        if doc:
            doc["_id"] = str(doc["_id"])
        return doc

    def list_sessions(self, limit: int = 20) -> list[dict]:
        docs = list(
            self._db["sessions"]
            .find(
                {},
                {"_id": 1, "keyword": 1, "status": 1, "started_at": 1,
                 "streams_found": 1, "pages_crawled": 1, "flagged_count": 1},
            )
            .sort("started_at", DESCENDING)
            .limit(limit)
        )
        for d in docs:
            d["_id"] = str(d["_id"])
        return docs

    # ── Tree-collection lookups ────────────────────────────────────────────

    def _get_tree_meta(self, session_id: str) -> list[dict]:
        from bson import ObjectId
        doc = self._db["sessions"].find_one({"_id": ObjectId(session_id)})
        return (doc or {}).get("parent_trees", [])

    def get_tree_col_name(self, session_id: str, parent_url: str) -> str | None:
        """Return the collection name for a specific parent URL, or None."""
        for tree in self._get_tree_meta(session_id):
            if tree["parent_url"] == parent_url:
                return tree["collection"]
        return None

    def get_all_tree_col_names(self, session_id: str) -> list[str]:
        """All tree-collection names registered for this session."""
        return [t["collection"] for t in self._get_tree_meta(session_id)]

    # ── Page / node upsert ─────────────────────────────────────────────────

    def upsert_node(
        self,
        session_id:    str,
        node:          dict,
        tree_col_name: str,
    ) -> None:
        """
        Write (or overwrite) a crawled node into its parent-URL tree
        collection.

        ``node`` must contain at least:
            url, depth, parent_url, score, title, crawled_at

        ``tree_col_name`` must come from ``register_parent_trees`` or
        ``get_tree_col_name``.
        """
        node.setdefault("status", "crawled")
        self._indexed_col(tree_col_name).replace_one(
            {"url": node["url"]},
            node,
            upsert=True,
        )
        self._refresh_session_stats(session_id)

    # ── Session-level aggregate stats ──────────────────────────────────────

    def _refresh_session_stats(self, session_id: str) -> None:
        """
        Recompute pages_crawled and flagged_count by scanning every tree
        collection and push the totals back into the session document.
        """
        pages_crawled = 0
        flagged_count = 0
        for col_name in self.get_all_tree_col_names(session_id):
            col = self._col(col_name)
            # Only count nodes that have actually been crawled.
            pages_crawled += col.count_documents({"crawled_at": {"$ne": None}})
            flagged_count += col.count_documents(
                {"score": {"$gte": PIRACY_SCORE_THRESHOLD}}
            )
        self.update_session(
            session_id,
            pages_crawled=pages_crawled,
            flagged_count=flagged_count,
        )

    # ── Cross-tree page queries ────────────────────────────────────────────

    def get_all_pages(self, session_id: str) -> list[dict]:
        """Merge and return pages from every tree in this session."""
        result: list[dict] = []
        for col_name in self.get_all_tree_col_names(session_id):
            result.extend(self._col(col_name).find({}, {"_id": 0}))
        return result

    def get_flagged_pages(self, session_id: str) -> list[dict]:
        """All pages whose score is at or above PIRACY_SCORE_THRESHOLD, sorted."""
        pages: list[dict] = []
        for col_name in self.get_all_tree_col_names(session_id):
            pages.extend(
                self._col(col_name).find(
                    {"score": {"$gte": PIRACY_SCORE_THRESHOLD}},
                    {"_id": 0},
                )
            )
        return sorted(pages, key=lambda p: p.get("score", 0), reverse=True)

    def get_recent_pages(self, session_id: str, limit: int = 50) -> list[dict]:
        all_pages = [
            p for p in self.get_all_pages(session_id)
            if p.get("crawled_at")
        ]
        return sorted(
            all_pages,
            key=lambda p: p["crawled_at"],
            reverse=True,
        )[:limit]

    # ── Single-tree queries ────────────────────────────────────────────────

    def get_tree_pages(
        self,
        session_id:    str,
        tree_col_name: str,
        *,
        sort_by_depth: bool = True,
    ) -> list[dict]:
        """
        Return every page stored in one specific parent-URL tree.
        Sorted by depth ascending by default (breadth-first view).
        """
        sort_field = "depth" if sort_by_depth else "crawled_at"
        return list(
            self._col(tree_col_name)
            .find({}, {"_id": 0})
            .sort(sort_field, 1)
        )

    def get_tree_summary(self, session_id: str) -> list[dict]:
        """
        Return a high-level summary for each registered tree:
            parent_url, collection, total_pages, flagged, max_depth, streams
        Useful for dashboards / debugging.
        """
        summaries = []
        for tree in self._get_tree_meta(session_id):
            col       = self._col(tree["collection"])
            total     = col.count_documents({})
            flagged   = col.count_documents({"score": {"$gte": PIRACY_SCORE_THRESHOLD}})
            depth_doc = col.find_one({}, {"depth": 1}, sort=[("depth", DESCENDING)])
            max_depth = (depth_doc or {}).get("depth", 0)
            summaries.append({
                "index":       tree["index"],
                "parent_url":  tree["parent_url"],
                "collection":  tree["collection"],
                "total_pages": total,
                "flagged":     flagged,
                "max_depth":   max_depth,
            })
        return summaries

    # ── Streams ───────────────────────────────────────────────────────────

    def record_stream(
        self,
        session_id:  str,
        stream_url:  str,
        source_url:  str,
        keyword:     str,
        stream_type: str = "unknown",
        score:       int = 0,
    ) -> str:
        col = self._db["streams"]
        col.create_index(
            [("session_id", 1), ("stream_url", 1)],
            unique     = True,
            background = True,
        )
        doc = {
            "session_id":    session_id,
            "keyword":       keyword,
            "stream_url":    stream_url,
            "source_url":    source_url,
            "stream_type":   stream_type,
            "score":         score,
            "discovered_at": self._now(),
        }
        try:
            result = col.insert_one(doc)
            from bson import ObjectId
            self._db["sessions"].update_one(
                {"_id": ObjectId(session_id)},
                {"$inc": {"streams_found": 1}},
            )
            return str(result.inserted_id)
        except Exception:
            existing = col.find_one(
                {"session_id": session_id, "stream_url": stream_url},
                {"_id": 1},
            )
            return str(existing["_id"]) if existing else ""

    def get_streams(self, session_id: str) -> list[dict]:
        return list(
            self._db["streams"]
            .find({"session_id": session_id}, {"_id": 0})
            .sort("discovered_at", DESCENDING)
        )

    def get_all_streams(self, limit: int = 100) -> list[dict]:
        return list(
            self._db["streams"]
            .find({}, {"_id": 0})
            .sort("discovered_at", DESCENDING)
            .limit(limit)
        )

    # ── Logs ──────────────────────────────────────────────────────────────

    def log(self, session_id: str, level: str, message: str) -> None:
        self._db["logs"].insert_one({
            "session_id": session_id,
            "level":      level,
            "message":    message,
            "ts":         self._now(),
        })

    def get_logs(
        self,
        session_id: str,
        since_ts:   str | None = None,
        limit:      int        = 100,
    ) -> list[dict]:
        query: dict[str, Any] = {"session_id": session_id}
        if since_ts:
            query["ts"] = {"$gt": since_ts}
        return list(
            self._db["logs"]
            .find(query, {"_id": 0})
            .sort("ts", 1)
            .limit(limit)
        )

    def close(self) -> None:
        self._client.close()