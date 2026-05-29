import pytest
from c2sync import project_manager, git_ops


class MockSerialConnection:
    """Drop-in replacement for serial_interface.SerialConnection.

    Test-configurable via class attributes before the CLI creates an instance:
        MockSerialConnection._running_config = "..."
        MockSerialConnection._synced = False

    All sent commands accumulate in _all_sent for post-call assertions.
    """

    _running_config: str = "hostname MockRouter\n"
    _synced: bool = True
    _all_sent: list[str] = []

    def __init__(self, tty, baudrate=9600, login=False):
        self.tty = tty

    def send_command(self, cmd: str) -> str:
        type(self)._all_sent.append(cmd)
        return "MockRouter#"

    def send_config(self, commands: list[str]):
        type(self)._all_sent.extend(commands)

    def get_running_config(self) -> str:
        return type(self)._running_config

    def is_config_synced(self) -> bool:
        return type(self)._synced

    def enter_config_mode(self):
        pass

    def exit_config_mode(self):
        pass


@pytest.fixture
def mock_serial(monkeypatch):
    """Patch SerialConnection and reset class-level state before each test."""
    MockSerialConnection._running_config = "hostname MockRouter\n"
    MockSerialConnection._synced = True
    MockSerialConnection._all_sent = []
    monkeypatch.setattr("c2sync.serial_interface.SerialConnection", MockSerialConnection)
    return MockSerialConnection


@pytest.fixture
def project_dir(tmp_path, monkeypatch):
    """Empty tmp directory set as CWD — no project initialised yet."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_AUTHOR_NAME", "C2Sync Test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@c2sync.test")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "C2Sync Test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@c2sync.test")
    return tmp_path


@pytest.fixture
def initialized_project(project_dir):
    """CWD is a fully initialised c2sync project with a git repo."""
    project_manager.init_project()
    git_ops.init_repo()
    return project_dir
