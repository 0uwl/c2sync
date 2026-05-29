import json
import pytest
from pathlib import Path

from c2sync import project_manager


# ---------------------------------------------------------------------------
# init_project
# ---------------------------------------------------------------------------

def test_init_creates_c2sync_directory(project_dir):
    project_manager.init_project()
    assert (project_dir / ".c2sync").is_dir()


def test_init_creates_registry_file(project_dir):
    project_manager.init_project()
    assert (project_dir / ".c2sync" / "registry.json").is_file()


def test_init_registry_starts_empty(project_dir):
    project_manager.init_project()
    raw = json.loads((project_dir / ".c2sync" / "registry.json").read_text())
    assert raw == {}


def test_init_returns_true_first_time(project_dir):
    assert project_manager.init_project() is True


def test_init_returns_false_when_already_initialized(project_dir):
    project_manager.init_project()
    assert project_manager.init_project() is False


# ---------------------------------------------------------------------------
# get_device — upsert behaviour
# ---------------------------------------------------------------------------

def test_get_device_creates_new_entry(project_dir):
    project_manager.init_project()
    device = project_manager.get_device("router1", "/dev/ttyUSB0")
    assert device.name == "router1"
    assert device.tty == "/dev/ttyUSB0"


def test_get_device_returns_existing_entry(project_dir):
    project_manager.init_project()
    project_manager.get_device("router1", "/dev/ttyUSB0")
    device = project_manager.get_device("router1")
    assert device.name == "router1"
    assert device.tty == "/dev/ttyUSB0"


def test_get_device_raises_when_no_tty_for_new_device(project_dir):
    project_manager.init_project()
    with pytest.raises(ValueError, match="TTY_DEVICE required"):
        project_manager.get_device("router1")


def test_get_device_persists_across_calls(project_dir):
    project_manager.init_project()
    project_manager.get_device("router1", "/dev/ttyUSB0")
    project_manager.get_device("switch1", "/dev/ttyUSB1")
    devices = project_manager.get_all_devices()
    names = {d.name for d in devices}
    assert names == {"router1", "switch1"}


# ---------------------------------------------------------------------------
# Registry serialisation
# ---------------------------------------------------------------------------

def test_registry_does_not_store_config_path(project_dir):
    project_manager.init_project()
    project_manager.get_device("router1", "/dev/ttyUSB0")
    raw = json.loads((project_dir / ".c2sync" / "registry.json").read_text())
    assert "config_path" not in raw["router1"]


def test_registry_does_not_store_staging_path(project_dir):
    project_manager.init_project()
    project_manager.get_device("router1", "/dev/ttyUSB0")
    raw = json.loads((project_dir / ".c2sync" / "registry.json").read_text())
    assert "staging_path" not in raw["router1"]


def test_registry_stores_name_and_tty(project_dir):
    project_manager.init_project()
    project_manager.get_device("router1", "/dev/ttyUSB0")
    raw = json.loads((project_dir / ".c2sync" / "registry.json").read_text())
    assert raw["router1"]["name"] == "router1"
    assert raw["router1"]["tty"] == "/dev/ttyUSB0"


def test_load_registry_round_trips(project_dir):
    project_manager.init_project()
    project_manager.get_device("router1", "/dev/ttyUSB0")
    project_manager.get_device("switch1", "/dev/ttyUSB1")
    registry = project_manager.load_registry()
    assert set(registry.keys()) == {"router1", "switch1"}
    assert registry["router1"].tty == "/dev/ttyUSB0"


# ---------------------------------------------------------------------------
# Device path derivation
# ---------------------------------------------------------------------------

def test_device_config_path_derived_from_name(project_dir):
    project_manager.init_project()
    device = project_manager.get_device("myrouter", "/dev/ttyUSB0")
    assert device.config_path == Path(".c2sync/myrouter.config")


def test_device_staging_path_derived_from_name(project_dir):
    project_manager.init_project()
    device = project_manager.get_device("myrouter", "/dev/ttyUSB0")
    assert device.staging_path == Path(".c2sync/.myrouter.staging")


# ---------------------------------------------------------------------------
# read_staging
# ---------------------------------------------------------------------------

def test_read_staging_returns_empty_list_when_file_missing(project_dir):
    project_manager.init_project()
    device = project_manager.get_device("router1", "/dev/ttyUSB0")
    assert project_manager.read_staging(device) == []


def test_read_staging_returns_lines(project_dir):
    project_manager.init_project()
    device = project_manager.get_device("router1", "/dev/ttyUSB0")
    device.staging_path.write_text("interface Gi1/0/1\nshutdown\n")
    assert project_manager.read_staging(device) == ["interface Gi1/0/1", "shutdown"]


def test_read_staging_ignores_trailing_newline(project_dir):
    project_manager.init_project()
    device = project_manager.get_device("router1", "/dev/ttyUSB0")
    device.staging_path.write_text("shutdown\n")
    result = project_manager.read_staging(device)
    assert result == ["shutdown"]


# ---------------------------------------------------------------------------
# get_all_devices
# ---------------------------------------------------------------------------

def test_get_all_devices_empty_when_no_devices(project_dir):
    project_manager.init_project()
    assert project_manager.get_all_devices() == []


def test_get_all_devices_returns_all(project_dir):
    project_manager.init_project()
    project_manager.get_device("r1", "/dev/ttyUSB0")
    project_manager.get_device("r2", "/dev/ttyUSB1")
    devices = project_manager.get_all_devices()
    assert len(devices) == 2
