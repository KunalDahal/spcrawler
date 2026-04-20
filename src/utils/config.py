from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Config:
    api_key:   str
    db_name:   str
    mongo_uri: str = "mongodb://localhost:27017"
    proxy_url: str = ""