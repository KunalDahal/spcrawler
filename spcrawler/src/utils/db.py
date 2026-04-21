from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from pymongo import MongoClient, DESCENDING
from pymongo.collection import Collection

from .config import Config
from .constants import PIRACY_SCORE_THRESHOLD

if TYPE_CHECKING:
    from ..events import EventBus


def _tree_col_name(session_id: str, index: int) -> str:
    return f"tree_{session_id}_{index:02d}"


class Database:
    def __init__(self, cfg: Config, bus: "EventBus | None" = None) -> None:
        self._client = MongoClient(cfg.mongo_uri)
        self._db     = self._client[cfg.db_name]
        self._bus    = bus

    def _col(self, name: str) -> Collection:
        return self._db[name]

    def _indexed_col(self, name: str) -> Collection:
        c = self._db[name]
        c.create_index("url",                        background=True)
        c.create_index([("score",      DESCENDING)], background=True)
        c.create_index([("depth",      1)],          background=True)
        c.create_index([("crawled_at", DESCENDING)], background=True)
        return c

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _emit(self, session_id: str, event_type: str, data: dict) -> None:
        if self._bus is None:
            return
        from ..events import Event
        self._bus.emit(Event(type=event_type, session_id=session_id, data=data))

    def create_session(self, keyword: str) -> str:
        doc = {
            "keyword":       keyword,
            "status":        "running",
            "started_at":    self._now(),
            "finished_at":   None,
            "streams_found": 0,
            "pages_crawled": 0,
            "flagged_count": 0,
            "parent_trees":  [],
        }
        result = self._db["sessions"].insert_one(doc)
        return str(result.inserted_id)

    def register_parent_trees(self, session_id: str, parent_urls: list[str]) -> dict[str, str]:
        from bson import ObjectId
        from ..events import E

        trees:   list[dict]     = []
        mapping: dict[str, str] = {}

        for idx, url in enumerate(parent_urls):
            col_name = _tree_col_name(session_id, idx)
            trees.append({"index": idx, "parent_url": url, "collection": col_name})
            mapping[url] = col_name

            self._indexed_col(col_name).replace_one(
                {"url": url},
                {
                    "url":            url,
                    "depth":          0,
                    "parent_url":     None,
                    "keyword":        "",
                    "title":          "",
                    "text_snippet":   "",
                    "links_found":    [],
                    "iframes":        [],
                    "stream_urls":    [],
                    "cdn_headers":    {},
                    "score":          0,
                    "crawled_at":     None,
                    "status":         "pending",
                    "is_ad_page":     False,
                    "is_player_page": False,
                    "is_official":    False,
                    "is_suspicious":  False,
                },
                upsert=True,
            )

        self._db["sessions"].update_one(
            {"_id": ObjectId(session_id)},
            {"$set": {"parent_trees": trees}},
        )

        self._emit(session_id, E.DB_TREES_REGISTERED, {
            "count":       len(parent_urls),
            "parent_urls": parent_urls,
            "collections": list(mapping.values()),
        })

        return mapping

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

    def _get_tree_meta(self, session_id: str) -> list[dict]:
        from bson import ObjectId
        doc = self._db["sessions"].find_one({"_id": ObjectId(session_id)})
        return (doc or {}).get("parent_trees", [])

    def get_tree_col_name(self, session_id: str, parent_url: str) -> str | None:
        for tree in self._get_tree_meta(session_id):
            if tree["parent_url"] == parent_url:
                return tree["collection"]
        return None

    def get_all_tree_col_names(self, session_id: str) -> list[str]:
        return [t["collection"] for t in self._get_tree_meta(session_id)]

    def upsert_node(self, session_id: str, node: dict, tree_col_name: str) -> None:
        from ..events import E

        node.setdefault("status", "crawled")
        node.setdefault("is_ad_page", False)
        node.setdefault("is_player_page", False)
        node.setdefault("is_official", False)
        node.setdefault("is_suspicious", False)

        self._indexed_col(tree_col_name).replace_one(
            {"url": node["url"]},
            node,
            upsert=True,
        )
        self._refresh_session_stats(session_id)

        self._emit(session_id, E.DB_NODE_UPSERTED, {
            "url":            node.get("url"),
            "depth":          node.get("depth", 0),
            "score":          node.get("score", 0),
            "title":          node.get("title", ""),
            "page_summary":   node.get("page_summary", ""),
            "tree_col":       tree_col_name,
            "stream_urls":    node.get("stream_urls", []),
            "iframes":        node.get("iframes", []),
            "links_found":    node.get("links_found", []),
            "players":        node.get("players", {}),
            "is_player_page": node.get("is_player_page", False),
            "is_ad_page":     node.get("is_ad_page", False),
            "is_suspicious":  node.get("is_suspicious", False),
            "is_piracy_host": node.get("is_piracy_host", False),
            "is_official":    node.get("is_official", False),
            "parent_url":     node.get("parent_url"),
        })

    def _refresh_session_stats(self, session_id: str) -> None:
        pages_crawled = 0
        flagged_count = 0
        for col_name in self.get_all_tree_col_names(session_id):
            col = self._col(col_name)
            pages_crawled += col.count_documents({"crawled_at": {"$ne": None}})
            flagged_count += col.count_documents({"score": {"$gte": PIRACY_SCORE_THRESHOLD}})
        self.update_session(
            session_id,
            pages_crawled=pages_crawled,
            flagged_count=flagged_count,
        )

    def get_all_pages(self, session_id: str) -> list[dict]:
        result: list[dict] = []
        for col_name in self.get_all_tree_col_names(session_id):
            result.extend(self._col(col_name).find({}, {"_id": 0}))
        return result

    def get_flagged_pages(self, session_id: str) -> list[dict]:
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
        all_pages = [p for p in self.get_all_pages(session_id) if p.get("crawled_at")]
        return sorted(all_pages, key=lambda p: p["crawled_at"], reverse=True)[:limit]

    def get_tree_pages(
        self,
        session_id:    str,
        tree_col_name: str,
        *,
        sort_by_depth: bool = True,
    ) -> list[dict]:
        sort_field = "depth" if sort_by_depth else "crawled_at"
        return list(self._col(tree_col_name).find({}, {"_id": 0}).sort(sort_field, 1))

    def get_tree_summary(self, session_id: str) -> list[dict]:
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

    def record_stream(
        self,
        session_id:  str,
        stream_url:  str,
        source_url:  str,
        keyword:     str,
        stream_type: str = "unknown",
        score:       int = 0,
        player_id:   str = "page_streams",
    ) -> str:
        from ..events import E

        col = self._db["streams"]
        col.create_index(
            [("session_id", 1), ("stream_url", 1)],
            unique     = True,
            background = True,
        )
        col.create_index(
            [("session_id", 1), ("source_url", 1), ("player_id", 1)],
            background = True,
        )
        doc = {
            "session_id":    session_id,
            "keyword":       keyword,
            "stream_url":    stream_url,
            "source_url":    source_url,
            "stream_type":   stream_type,
            "score":         score,
            "player_id":     player_id,
            "discovered_at": self._now(),
        }
        doc_id = ""
        try:
            result = col.insert_one(doc)
            from bson import ObjectId
            self._db["sessions"].update_one(
                {"_id": ObjectId(session_id)},
                {"$inc": {"streams_found": 1}},
            )
            doc_id = str(result.inserted_id)
        except Exception:
            existing = col.find_one(
                {"session_id": session_id, "stream_url": stream_url},
                {"_id": 1},
            )
            doc_id = str(existing["_id"]) if existing else ""

        if doc_id:
            self._emit(session_id, E.DB_STREAM_RECORDED, {
                "stream_url":  stream_url,
                "source_url":  source_url,
                "stream_type": stream_type,
                "score":       score,
                "player_id":   player_id,
                "doc_id":      doc_id,
            })

        return doc_id

    def get_streams_by_player(self, session_id: str) -> dict:
        raw = list(
            self._db["streams"]
            .find({"session_id": session_id}, {"_id": 0})
            .sort("discovered_at", DESCENDING)
        )
        grouped: dict = {}
        for doc in raw:
            src = doc.get("source_url", "unknown")
            pid = doc.get("player_id", "page_streams")
            grouped.setdefault(src, {}).setdefault(pid, []).append(doc["stream_url"])
        return grouped

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

    def close(self) -> None:
        self._client.close()
