import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import contextvars
from typing import Optional

_logger_var: contextvars.ContextVar[Optional[logging.LoggerAdapter]] = (
    contextvars.ContextVar("logger", default=None)
)

LOG_FILE_PATH = Path('./.c2sync/session.log')

def setup_logging():
    handler = RotatingFileHandler(filename=LOG_FILE_PATH)
    formatter = logging.Formatter("%(levelname)s [%(prefix)s]: %(message)s")
    handler.setFormatter(formatter)

    root = logging.getLogger("c2sync")
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(handler)


def set_log_context(context: str = "Global") -> logging.LoggerAdapter:
    base_logger = logging.getLogger("c2sync")
    adapter = logging.LoggerAdapter(base_logger, {"prefix": f"{context}"})
    _logger_var.set(adapter)
    return adapter


def get_logger() -> logging.LoggerAdapter:
    logger = _logger_var.get()
    if logger is None:
        raise RuntimeError("Logger not initialized")
    return logger