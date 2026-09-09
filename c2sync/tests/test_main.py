from unittest.mock import MagicMock, patch

import pytest

from netmiko.exceptions import ConfigInvalidException

from c2sync import main as main_module
from c2sync import get_project
from c2sync.state_engine import StateEngine


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    main_module.init(['/dev/ttyUSB0'])
    return get_project()


def _write(path, lines):
    with open(path, 'w') as file:
        file.write('\n'.join(lines) + '\n')


def _mocked_connect(mock_conn):
    return (
        patch('c2sync.connector.ConnectHandler', return_value=mock_conn),
        patch('builtins.input', side_effect=['admin']),
        patch('getpass.getpass', side_effect=['pw', '']),
    )


# ------------------------------------------------------------------
# status / on-demand refresh
# ------------------------------------------------------------------

def test_status_reports_synced_with_no_edits(project, capsys):
    main_module.status([])

    output = capsys.readouterr().out
    assert 'synced' in output
    assert StateEngine(project).state.host_dirty is False


def test_status_stages_changes_made_directly_to_the_edit_file(project, capsys):
    _write(project.EDIT_FILE, ['interface Gi1/0/1', ' shutdown'])

    main_module.status([])

    with open(project.STAGING_FILE) as file:
        assert 'shutdown' in file.read()
    assert StateEngine(project).state.host_dirty is True


def test_status_is_idempotent_across_repeated_calls(project):
    """
    Since there's no watcher, status/sync always recompute from scratch -
    calling it twice in a row must not duplicate staged commands.
    """
    _write(project.EDIT_FILE, ['interface Gi1/0/1', ' shutdown'])

    main_module.status([])
    main_module.status([])

    with open(project.STAGING_FILE) as file:
        content = file.read()

    assert content.count('shutdown') == 1


# ------------------------------------------------------------------
# sync
# ------------------------------------------------------------------

def test_sync_pushes_staged_changes_and_updates_baseline(project):
    _write(project.EDIT_FILE, ['interface Gi1/0/1', ' shutdown'])

    mock_conn = MagicMock()
    mock_conn.check_enable_mode.return_value = True
    mock_conn.send_config_set.return_value = 'interface Gi1/0/1\n shutdown\nend'
    mock_conn.send_command.return_value = 'interface Gi1/0/1\n shutdown\n!'

    patches = _mocked_connect(mock_conn)
    with patches[0], patches[1], patches[2]:
        main_module.sync(['-y'])

    state = StateEngine(project).state
    assert state.host_dirty is False
    assert state.device_dirty is True

    with open(project.STAGING_FILE) as file:
        assert file.read() == ''

    # The baseline now matches what was pushed, so re-checking status
    # shouldn't re-stage the same change.
    main_module.status([])
    with open(project.STAGING_FILE) as file:
        assert file.read() == ''


def test_sync_rejected_by_device_leaves_staging_and_state_intact(project):
    _write(project.EDIT_FILE, ['interface Gi1/0/1', ' bogus-command'])

    mock_conn = MagicMock()
    mock_conn.check_enable_mode.return_value = True
    mock_conn.send_config_set.side_effect = ConfigInvalidException('bad command')

    patches = _mocked_connect(mock_conn)
    with patches[0], patches[1], patches[2]:
        with pytest.raises(SystemExit):
            main_module.sync(['-y'])

    state = StateEngine(project).state
    assert state.host_dirty is True
    assert state.device_dirty is False

    with open(project.STAGING_FILE) as file:
        assert 'bogus-command' in file.read()


def test_sync_with_no_edits_does_not_connect(project):
    with patch('c2sync.connector.ConnectHandler') as mock_handler:
        main_module.sync(['-y'])
        mock_handler.assert_not_called()


# ------------------------------------------------------------------
# commit
# ------------------------------------------------------------------

def test_commit_refuses_while_host_dirty(project):
    _write(project.EDIT_FILE, ['interface Gi1/0/1', ' shutdown'])
    main_module.status([])

    with patch('c2sync.connector.ConnectHandler') as mock_handler:
        with pytest.raises(SystemExit):
            main_module.commit(['-y'])
        mock_handler.assert_not_called()


def test_commit_saves_and_marks_device_clean(project):
    StateEngine(project).mark_device_dirty()

    mock_conn = MagicMock()
    mock_conn.save_config.return_value = 'Building configuration...\n[OK]'

    patches = _mocked_connect(mock_conn)
    with patches[0], patches[1], patches[2]:
        main_module.commit(['-y'])

    assert StateEngine(project).state.device_dirty is False


# ------------------------------------------------------------------
# discard
# ------------------------------------------------------------------

def test_discard_reverts_edit_file_to_baseline(project):
    with open(project.EDIT_FILE) as file:
        baseline = file.read()

    _write(project.EDIT_FILE, ['interface Gi1/0/1', ' shutdown'])
    main_module.status([])
    assert StateEngine(project).state.host_dirty is True

    main_module.discard([])

    with open(project.EDIT_FILE) as file:
        assert file.read() == baseline

    with open(project.STAGING_FILE) as file:
        assert file.read() == ''

    assert StateEngine(project).state.host_dirty is False
