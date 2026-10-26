"""Logging standar + correlation id (request_id, thread_id).

request_id mengalir via ContextVar (di-set RequestIDMiddleware per request)
dan ditempel ke SETIAP record oleh filter — format log selalu punya kolomnya.
thread_id ditambahkan pemanggil via extra={'thread_id': ...} bila relevan.

setup_logging IDEMPOTEN: tak pernah me-reset handler milik framework
(uvicorn/Phoenix) — hanya menambah milik sendiri bila belum ada.
"""

import logging
import sys

from app.core.request_id import request_id_ctx


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = request_id_ctx.get()
        return True


def setup_logging(level: str = "INFO") -> None:
    lvl = getattr(logging, str(level or "").upper(), None)
    if not isinstance(lvl, int):
        raise ValueError(f"LOG_LEVEL tak dikenal: {level!r} "
                         "(pilih: DEBUG/INFO/WARNING/ERROR/CRITICAL).")
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)-7s | %(request_id)s | "
            "%(name)s | %(message)s"))
        root.addHandler(handler)
    for handler in root.handlers:
        if not any(isinstance(f, _RequestIdFilter) for f in handler.filters):
            handler.addFilter(_RequestIdFilter())
    root.setLevel(lvl)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
