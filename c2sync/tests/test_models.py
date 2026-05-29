from pathlib import Path
import pytest

from c2sync.models import Device, DeviceState, ConfigLine, CommandBlock


# ---------------------------------------------------------------------------
# DeviceState
# ---------------------------------------------------------------------------

def test_device_state_values_are_strings():
    assert DeviceState.SYNCED == "SYNCED"
    assert DeviceState.HOST_PENDING == "HOST_PENDING"
    assert DeviceState.DEVICE_PENDING == "DEVICE_PENDING"


def test_device_state_is_str_instance():
    assert isinstance(DeviceState.SYNCED, str)


def test_device_state_works_as_dict_key():
    mapping = {"SYNCED": "green", "HOST_PENDING": "yellow", "DEVICE_PENDING": "red"}
    assert mapping[DeviceState.SYNCED] == "green"
    assert mapping[DeviceState.HOST_PENDING] == "yellow"
    assert mapping[DeviceState.DEVICE_PENDING] == "red"


def test_device_state_all_members_distinct():
    states = {DeviceState.SYNCED, DeviceState.HOST_PENDING, DeviceState.DEVICE_PENDING}
    assert len(states) == 3


# ---------------------------------------------------------------------------
# Device
# ---------------------------------------------------------------------------

def test_device_derives_config_path_from_name():
    device = Device("router1", "/dev/ttyUSB0")
    assert device.config_path == Path(".c2sync/router1.config")


def test_device_derives_staging_path_from_name():
    device = Device("router1", "/dev/ttyUSB0")
    assert device.staging_path == Path(".c2sync/.router1.staging")


def test_device_to_dict_contains_name_and_tty():
    device = Device("router1", "/dev/ttyUSB0")
    d = device.to_dict()
    assert d["name"] == "router1"
    assert d["tty"] == "/dev/ttyUSB0"


def test_device_to_dict_excludes_derived_paths():
    device = Device("router1", "/dev/ttyUSB0")
    d = device.to_dict()
    assert "config_path" not in d
    assert "staging_path" not in d


def test_device_save_config_writes_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".c2sync").mkdir()
    device = Device("router1", "/dev/ttyUSB0")
    device.save_config("hostname Router1\n")
    assert device.config_path.read_text() == "hostname Router1\n"


def test_device_name_and_tty_stored():
    device = Device("switch1", "/dev/ttyS0")
    assert device.name == "switch1"
    assert device.tty == "/dev/ttyS0"


# ---------------------------------------------------------------------------
# ConfigLine / CommandBlock (used by diff engine)
# ---------------------------------------------------------------------------

def test_config_line_fields():
    cl = ConfigLine(index=3, text=" description test", indent=1)
    assert cl.index == 3
    assert cl.text == " description test"
    assert cl.indent == 1


def test_config_line_is_frozen():
    cl = ConfigLine(index=0, text="hostname R1", indent=0)
    with pytest.raises(Exception):
        cl.index = 1  # type: ignore


def test_command_block_fields():
    cb = CommandBlock(context=("interface Gi1/0/1",), command="shutdown")
    assert cb.context == ("interface Gi1/0/1",)
    assert cb.command == "shutdown"
