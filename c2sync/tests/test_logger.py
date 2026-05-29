import logging
import pytest

from c2sync import logger as log_module


@pytest.fixture(autouse=True)
def reset_logger():
    """Clear handlers and reset prefix between tests."""
    yield
    root = logging.getLogger("c2sync")
    root.handlers.clear()
    log_module._prefix = "Global"


# ---------------------------------------------------------------------------
# setup_logging
# ---------------------------------------------------------------------------

def test_setup_logging_does_not_raise_without_project(project_dir):
    log_module.setup_logging()  # .c2sync/ absent — must not raise


def test_setup_logging_adds_no_handlers_without_project(project_dir):
    log_module.setup_logging()
    assert logging.getLogger("c2sync").handlers == []


def test_setup_logging_adds_handler_when_project_exists(initialized_project):
    log_module.setup_logging()
    assert len(logging.getLogger("c2sync").handlers) == 1


def test_setup_logging_clears_duplicate_handlers(initialized_project):
    log_module.setup_logging()
    log_module.setup_logging()
    assert len(logging.getLogger("c2sync").handlers) == 1


# ---------------------------------------------------------------------------
# set_log_context / get_logger
# ---------------------------------------------------------------------------

def test_set_log_context_updates_prefix(project_dir):
    log_module.set_log_context("router1")
    assert log_module._prefix == "router1"


def test_set_log_context_defaults_to_global(project_dir):
    log_module.set_log_context("router1")
    log_module.set_log_context()
    assert log_module._prefix == "Global"


def test_set_log_context_returns_logger(project_dir):
    result = log_module.set_log_context("router1")
    assert isinstance(result, logging.Logger)
    assert result.name == "c2sync"


def test_get_logger_returns_c2sync_logger():
    result = log_module.get_logger()
    assert isinstance(result, logging.Logger)
    assert result.name == "c2sync"


def test_prefix_injected_into_log_record(initialized_project):
    log_module.setup_logging()
    log_module.set_log_context("switch1")

    log = log_module.get_logger()
    handler = logging.getLogger("c2sync").handlers[0]

    records: list[logging.LogRecord] = []
    original_emit = handler.emit
    handler.emit = lambda r: records.append(r)  # type: ignore

    log.info("test message")
    handler.emit = original_emit

    assert len(records) == 1
    assert records[0].prefix == "switch1"  # type: ignore[attr-defined]
