from __future__ import annotations

from ..utils.config import Config


class ProxyManager:
    def __init__(self, cfg: Config) -> None:
        self._proxy = cfg.proxy_url.strip() if cfg.proxy_url else None

    def get(self) -> dict | None:
        return {"server": self._proxy} if self._proxy else None

    @property
    def active(self) -> bool:
        return self._proxy is not None