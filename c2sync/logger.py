import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FILE_PATH = Path('./.c2sync/session.log')

_prefix = "Global"


class _PrefixFilter(logging.Filter):
    def filter(self, record):
        record.prefix = _prefix
        return True


def setup_logging():
    root = logging.getLogger("c2sync")
    root.setLevel(logging.INFO)
    root.handlers.clear()

    if not LOG_FILE_PATH.parent.exists():
        return

    handler = RotatingFileHandler(filename=LOG_FILE_PATH)
    formatter = logging.Formatter("%(levelname)s [%(prefix)s]: %(message)s")
    handler.setFormatter(formatter)
    handler.addFilter(_PrefixFilter())
    root.addHandler(handler)


def set_log_context(context: str = "Global") -> logging.Logger:
    global _prefix
    _prefix = context
    return logging.getLogger("c2sync")


def get_logger() -> logging.Logger:
    return logging.getLogger("c2sync")