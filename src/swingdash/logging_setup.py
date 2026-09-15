"""
File logging. Textual owns the terminal, so nothing is logged to stdout.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from swingdash.settings import Paths

_FORMAT = "%(asctime)s %(levelname)-7s %(threadName)s %(name)s: %(message)s"


# Secrets to scrub from every log line; `redact` adds one at any time.
_SECRETS: set[str] = set()


def redact(secret: str) -> None:
    """Never write `secret` to the log (e.g. a token obtained after startup)."""
    if secret:
        _SECRETS.add(secret)


class RedactingFilter(logging.Filter):
    """Replaces known secrets wherever they appear in a formatted log message."""

    def filter(self, record: logging.LogRecord) -> bool:
        if _SECRETS:
            message = record.getMessage()
            scrubbed = message
            for secret in _SECRETS:
                scrubbed = scrubbed.replace(secret, "****")
            if scrubbed != message:
                record.msg = scrubbed
                record.args = ()
        return True


def configure_logging(paths: Paths, *secrets: str, level: int = logging.INFO) -> None:
    for secret in secrets:
        redact(secret)
    paths.log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        paths.log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(_FORMAT))
    handler.addFilter(RedactingFilter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    # The WebSocket client logs every frame at DEBUG/INFO.
    for noisy in ("websocket", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
