import pytest
from c2sync import project_manager, git_ops, staging_builder


INITIAL_CONFIG = "hostname Router1\ninterface Gi1/0/1\n description OLD\n"


@pytest.fixture
def project_with_device(initialized_project):
    """Initialized project with one device whose config is already pulled and committed."""
    device = project_manager.get_device("router1", "/dev/ttyUSB0")
    device.config_path.write_text(INITIAL_CONFIG)
    git_ops.commit_all("initial pull")
    return device


# ---------------------------------------------------------------------------
# write_device
# ---------------------------------------------------------------------------

def test_write_device_creates_staging_file(project_with_device):
    device = project_with_device
    device.config_path.write_text(INITIAL_CONFIG + " shutdown\n")

    staging_builder.write_device(device)

    assert device.staging_path.exists()


def test_write_device_staging_contains_context(project_with_device):
    device = project_with_device
    device.config_path.write_text(INITIAL_CONFIG + " shutdown\n")

    staging_builder.write_device(device)

    content = device.staging_path.read_text()
    assert "interface Gi1/0/1" in content
    assert "shutdown" in content


def test_write_device_staging_empty_when_no_changes(project_with_device):
    device = project_with_device

    staging_builder.write_device(device)

    assert device.staging_path.read_text() == ""


def test_write_device_handles_missing_config_file(initialized_project):
    """write_device should not crash when the config file has never been pulled."""
    device = project_manager.get_device("router1", "/dev/ttyUSB0")

    # No config file, no commit
    staging_builder.write_device(device)

    # Staging file must not exist (no crash, no partial output)
    assert not device.staging_path.exists()


def test_write_device_overwrites_stale_staging(project_with_device):
    device = project_with_device
    device.staging_path.write_text("stale content")

    # Working copy matches HEAD → staging should be cleared
    staging_builder.write_device(device)

    assert device.staging_path.read_text() == ""


def test_write_device_reflects_multiple_additions(project_with_device):
    device = project_with_device
    device.config_path.write_text(INITIAL_CONFIG + " shutdown\n no description OLD\n")

    staging_builder.write_device(device)

    content = device.staging_path.read_text()
    assert "shutdown" in content
    assert "no description OLD" in content
