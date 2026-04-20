from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from typing import Callable


LEVELS = {
    "debug":   logging.DEBUG,
    "info":    logging.INFO,
    "warn":    logging.WARNING,
    "warning": logging.WARNING,
    "error":   logging.ERROR,
    "success": logging.INFO,
}

_RESET   = "\033[0m"
_BOLD    = "\033[1m"
_COLOURS = {
    logging.DEBUG:   "\033[36m",   # cyan
    logging.INFO:    "\033[37m",   # white
    logging.WARNING: "\033[33m",   # yellow
    logging.ERROR:   "\033[31m",   # red
    "success":       "\033[32m",   # green
}

PACKAGE_ROOT = "spcrawler"

_external_hooks: list[Callable[[dict], None]] = []


def add_hook(fn: Callable[[dict], None]) -> None:
    _external_hooks.append(fn)


def remove_hook(fn: Callable[[dict], None]) -> None:
    _external_hooks.remove(fn)


class _ColourFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        colour = _COLOURS.get(record.levelno, _COLOURS[logging.INFO])
        if getattr(record, "success", False):
            colour = _COLOURS["success"]

        ts      = datetime.now(timezone.utc).strftime("%H:%M:%S")
        module  = record.name.replace(f"{PACKAGE_ROOT}.", "").replace(PACKAGE_ROOT, "root")
        level   = record.levelname.ljust(7)
        message = record.getMessage()

        return f"{_BOLD}{colour}[{ts}] [{module}] {level}{_RESET}{colour}  {message}{_RESET}"


class _HookHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        if not _external_hooks:
            return
        payload = {
            "ts":      datetime.now(timezone.utc).isoformat(),
            "module":  record.name,
            "level":   "success" if getattr(record, "success", False) else record.levelname.lower(),
            "message": record.getMessage(),
        }
        for fn in _external_hooks:
            try:
                fn(payload)
            except Exception:
                pass


def _build_handler() -> logging.Handler:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(_ColourFormatter())
    return h


_stream_handler = _build_handler()
_hook_handler   = _HookHandler()


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.addHandler(_stream_handler)
        logger.addHandler(_hook_handler)
        logger.propagate = False
    return logger


def setup(level: str = "info") -> None:
    numeric = LEVELS.get(level.lower(), logging.INFO)
    root = logging.getLogger(PACKAGE_ROOT)
    root.setLevel(numeric)
    for handler in (root.handlers or []):
        handler.setLevel(numeric)
    _stream_handler.setLevel(numeric)
    _hook_handler.setLevel(numeric)


def success(logger: logging.Logger, message: str, *args) -> None:
    if args:
        message = message % args
    record = logger.makeRecord(
        logger.name, logging.INFO, "(unknown)", 0,
        message, (), None,
    )
    record.success = True
    logger.handle(record)
