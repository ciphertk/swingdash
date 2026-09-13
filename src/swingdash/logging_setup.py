"""
File logging. Textual owns the terminal, so nothing is logged to stdout.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from swingdash.settings import Paths

_FORMAT = "%(asctime)s %(levelname)-7s %(threadName)s %(name)s: %(message)s"


class RedactingFilter(logging.Filter):
    """Replaces a secret wherever it appears in a formatted log message."""

    def __init__(self, secret: str) -> None:
        super().__init__()
        self._secret = secret

    def filter(self, record: logging.LogRecord) -> bool:
        if self._secret:
            message = record.getMessage()
            if self._secret in message:
                record.msg = message.replace(self._secret, "****")
                record.args = ()
        return True


def configure_logging(paths: Paths, secret: str = "", level: int = logging.INFO) -> None:
    paths.log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        paths.log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(_FORMAT))
    handler.addFilter(RedactingFilter(secret))

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    # The WebSocket client logs every frame at DEBUG/INFO.
    for noisy in ("websocket", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
