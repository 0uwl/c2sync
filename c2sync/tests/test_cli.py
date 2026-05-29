import pytest
from click.testing import CliRunner

from c2sync.main import cli
from c2sync import project_manager, git_ops


@pytest.fixture
def runner():
    return CliRunner()


def _pull(runner, device="router1", tty="/dev/ttyUSB0", config=None, mock_serial=None):
    """Invoke c2sync pull, optionally overriding the mock running config first."""
    if config is not None and mock_serial is not None:
        mock_serial._running_config = config
    return runner.invoke(cli, ["pull", device, tty])


# ---------------------------------------------------------------------------
# c2sync init
# ---------------------------------------------------------------------------

def test_init_exits_zero(runner, project_dir):
    result = runner.invoke(cli, ["init"])
    assert result.exit_code == 0, result.output


def test_init_creates_project_directory(runner, project_dir):
    runner.invoke(cli, ["init"])
    assert (project_dir / ".c2sync").is_dir()


def test_init_creates_registry(runner, project_dir):
    runner.invoke(cli, ["init"])
    assert (project_dir / ".c2sync" / "registry.json").is_file()


def test_init_creates_git_repo(runner, project_dir):
    runner.invoke(cli, ["init"])
    assert (project_dir / ".git").is_dir()


def test_init_idempotent_exits_zero(runner, project_dir):
    runner.invoke(cli, ["init"])
    result = runner.invoke(cli, ["init"])
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# c2sync pull
# ---------------------------------------------------------------------------

def test_pull_exits_zero(runner, initialized_project, mock_serial):
    result = _pull(runner, mock_serial=mock_serial)
    assert result.exit_code == 0, result.output


def test_pull_creates_config_file(runner, initialized_project, mock_serial):
    _pull(runner, mock_serial=mock_serial)
    assert (initialized_project / ".c2sync" / "router1.config").is_file()


def test_pull_writes_config_content(runner, initialized_project, mock_serial):
    mock_serial._running_config = "hostname PulledRouter\n"
    _pull(runner, mock_serial=mock_serial)
    content = (initialized_project / ".c2sync" / "router1.config").read_text()
    assert "hostname PulledRouter" in content


def test_pull_registers_device(runner, initialized_project, mock_serial):
    _pull(runner, mock_serial=mock_serial)
    device = project_manager.get_device("router1")
    assert device.name == "router1"
    assert device.tty == "/dev/ttyUSB0"


def test_pull_commits_to_git(runner, initialized_project, mock_serial):
    _pull(runner, mock_serial=mock_serial)
    repo = git_ops.get_repo()
    assert "pulled from router1" in repo.head.commit.message


def test_pull_fails_without_tty_for_new_device(runner, initialized_project, mock_serial):
    result = runner.invoke(cli, ["pull", "router1"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# c2sync diff
# ---------------------------------------------------------------------------

def test_diff_exits_zero_with_no_changes(runner, initialized_project, mock_serial):
    _pull(runner, mock_serial=mock_serial)
    result = runner.invoke(cli, ["diff", "router1"])
    assert result.exit_code == 0, result.output


def test_diff_reports_no_staged_changes(runner, initialized_project, mock_serial):
    _pull(runner, mock_serial=mock_serial)
    result = runner.invoke(cli, ["diff", "router1"])
    assert "No staged changes" in result.output


def test_diff_shows_added_command(runner, initialized_project, mock_serial):
    mock_serial._running_config = "hostname MockRouter\ninterface Gi1/0/1\n"
    _pull(runner, mock_serial=mock_serial)

    config_path = initialized_project / ".c2sync" / "router1.config"
    config_path.write_text("hostname MockRouter\ninterface Gi1/0/1\n shutdown\n")

    result = runner.invoke(cli, ["diff", "router1"])
    assert result.exit_code == 0, result.output
    assert "shutdown" in result.output


def test_diff_shows_context_for_added_command(runner, initialized_project, mock_serial):
    mock_serial._running_config = "hostname MockRouter\ninterface Gi1/0/1\n"
    _pull(runner, mock_serial=mock_serial)

    config_path = initialized_project / ".c2sync" / "router1.config"
    config_path.write_text("hostname MockRouter\ninterface Gi1/0/1\n shutdown\n")

    result = runner.invoke(cli, ["diff", "router1"])
    assert "interface Gi1/0/1" in result.output


# ---------------------------------------------------------------------------
# c2sync status
# ---------------------------------------------------------------------------

def test_status_exits_zero(runner, initialized_project, mock_serial):
    _pull(runner, mock_serial=mock_serial)
    result = runner.invoke(cli, ["status", "router1"])
    assert result.exit_code == 0, result.output


def test_status_synced(runner, initialized_project, mock_serial):
    mock_serial._synced = True
    _pull(runner, mock_serial=mock_serial)
    result = runner.invoke(cli, ["status", "router1"])
    assert "SYNCED" in result.output


def test_status_host_pending(runner, initialized_project, mock_serial):
    _pull(runner, mock_serial=mock_serial)
    staging = initialized_project / ".c2sync" / ".router1.staging"
    staging.write_text("interface Gi1/0/1\nshutdown")

    result = runner.invoke(cli, ["status", "router1"])
    assert "HOST_PENDING" in result.output


def test_status_device_pending(runner, initialized_project, mock_serial):
    mock_serial._synced = False
    _pull(runner, mock_serial=mock_serial)
    result = runner.invoke(cli, ["status", "router1"])
    assert "DEVICE_PENDING" in result.output


def test_status_all_devices_when_no_arg(runner, initialized_project, mock_serial):
    _pull(runner, device="router1", tty="/dev/ttyUSB0", mock_serial=mock_serial)
    _pull(runner, device="switch1", tty="/dev/ttyUSB1", mock_serial=mock_serial)

    result = runner.invoke(cli, ["status"])
    assert result.exit_code == 0, result.output
    assert "router1" in result.output
    assert "switch1" in result.output


# ---------------------------------------------------------------------------
# c2sync sync
# ---------------------------------------------------------------------------

def test_sync_exits_zero_with_changes(runner, initialized_project, mock_serial):
    mock_serial._running_config = "hostname MockRouter\ninterface Gi1/0/1\n"
    _pull(runner, mock_serial=mock_serial)

    (initialized_project / ".c2sync" / "router1.config").write_text(
        "hostname MockRouter\ninterface Gi1/0/1\n shutdown\n"
    )

    result = runner.invoke(cli, ["sync", "-y", "router1"])
    assert result.exit_code == 0, result.output


def test_sync_sends_staged_commands_to_device(runner, initialized_project, mock_serial):
    mock_serial._running_config = "hostname MockRouter\ninterface Gi1/0/1\n"
    _pull(runner, mock_serial=mock_serial)

    (initialized_project / ".c2sync" / "router1.config").write_text(
        "hostname MockRouter\ninterface Gi1/0/1\n shutdown\n"
    )

    runner.invoke(cli, ["sync", "-y", "router1"])

    assert "shutdown" in mock_serial._all_sent


def test_sync_aborts_with_no_changes(runner, initialized_project, mock_serial):
    _pull(runner, mock_serial=mock_serial)
    result = runner.invoke(cli, ["sync", "-y", "router1"])
    assert "No changes" in result.output


def test_sync_makes_git_commit(runner, initialized_project, mock_serial):
    mock_serial._running_config = "hostname MockRouter\ninterface Gi1/0/1\n"
    _pull(runner, mock_serial=mock_serial)

    (initialized_project / ".c2sync" / "router1.config").write_text(
        "hostname MockRouter\ninterface Gi1/0/1\n shutdown\n"
    )

    runner.invoke(cli, ["sync", "-y", "router1"])

    repo = git_ops.get_repo()
    assert "sync router1" in repo.head.commit.message


def test_sync_prompts_for_confirmation_without_y(runner, initialized_project, mock_serial):
    mock_serial._running_config = "hostname MockRouter\ninterface Gi1/0/1\n"
    _pull(runner, mock_serial=mock_serial)

    (initialized_project / ".c2sync" / "router1.config").write_text(
        "hostname MockRouter\ninterface Gi1/0/1\n shutdown\n"
    )

    result = runner.invoke(cli, ["sync", "router1"], input="n\n")
    assert "Aborted" in result.output


def test_sync_custom_commit_message(runner, initialized_project, mock_serial):
    mock_serial._running_config = "hostname MockRouter\ninterface Gi1/0/1\n"
    _pull(runner, mock_serial=mock_serial)

    (initialized_project / ".c2sync" / "router1.config").write_text(
        "hostname MockRouter\ninterface Gi1/0/1\n shutdown\n"
    )

    runner.invoke(cli, ["sync", "-y", "-m", "vlan update", "router1"])

    repo = git_ops.get_repo()
    assert "vlan update" in repo.head.commit.message


# ---------------------------------------------------------------------------
# c2sync commit
# ---------------------------------------------------------------------------

def test_commit_exits_zero(runner, initialized_project, mock_serial):
    _pull(runner, mock_serial=mock_serial)
    result = runner.invoke(cli, ["commit", "-y", "router1"])
    assert result.exit_code == 0, result.output


def test_commit_sends_write_memory(runner, initialized_project, mock_serial):
    _pull(runner, mock_serial=mock_serial)
    runner.invoke(cli, ["commit", "-y", "router1"])
    assert "write memory" in mock_serial._all_sent


def test_commit_prompts_without_y(runner, initialized_project, mock_serial):
    _pull(runner, mock_serial=mock_serial)
    result = runner.invoke(cli, ["commit", "router1"], input="n\n")
    assert result.exit_code == 0
    assert "write memory" not in mock_serial._all_sent
