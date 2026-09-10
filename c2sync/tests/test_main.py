from unittest.mock import MagicMock, patch

import pytest

from netmiko.exceptions import ConfigInvalidException

from c2sync import git_ops, main as main_module
from c2sync import get_project
from c2sync.state_engine import StateEngine


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Isolate from whatever global config.toml might actually exist on the
    # machine running the tests.
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
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
# credentials
# ------------------------------------------------------------------

def test_connect_uses_env_credentials_without_prompting(project, monkeypatch):
    _write(project.EDIT_FILE, ['interface Gi1/0/1', ' shutdown'])

    monkeypatch.setenv('C2SYNC_USERNAME', 'admin')
    monkeypatch.setenv('C2SYNC_PASSWORD', 'pw')

    mock_conn = MagicMock()
    mock_conn.check_enable_mode.return_value = True
    mock_conn.send_config_set.return_value = 'interface Gi1/0/1\n shutdown\nend'
    mock_conn.send_command.return_value = 'interface Gi1/0/1\n shutdown\n!'

    def _fail_if_prompted(*args, **kwargs):
        raise AssertionError('should not prompt when both env vars are set')

    with patch('c2sync.connector.ConnectHandler', return_value=mock_conn), \
         patch('builtins.input', side_effect=_fail_if_prompted), \
         patch('getpass.getpass', side_effect=_fail_if_prompted):
        main_module.sync(['-y'])

    assert StateEngine(project).state.device_dirty is True


def test_connect_falls_back_to_prompt_when_env_partially_set(project, monkeypatch):
    _write(project.EDIT_FILE, ['interface Gi1/0/1', ' shutdown'])

    monkeypatch.setenv('C2SYNC_USERNAME', 'admin')
    monkeypatch.delenv('C2SYNC_PASSWORD', raising=False)

    mock_conn = MagicMock()
    mock_conn.check_enable_mode.return_value = True
    mock_conn.send_config_set.return_value = 'interface Gi1/0/1\n shutdown\nend'
    mock_conn.send_command.return_value = 'interface Gi1/0/1\n shutdown\n!'

    patches = _mocked_connect(mock_conn)
    with patches[0], patches[1], patches[2]:
        main_module.sync(['-y'])

    assert StateEngine(project).state.device_dirty is True


def test_connect_uses_username_from_global_config(project, monkeypatch):
    _write(project.EDIT_FILE, ['interface Gi1/0/1', ' shutdown'])

    monkeypatch.delenv('C2SYNC_USERNAME', raising=False)
    monkeypatch.delenv('C2SYNC_PASSWORD', raising=False)

    mock_conn = MagicMock()
    mock_conn.check_enable_mode.return_value = True
    mock_conn.send_config_set.return_value = 'interface Gi1/0/1\n shutdown\nend'
    mock_conn.send_command.return_value = 'interface Gi1/0/1\n shutdown\n!'

    def _fail_if_prompted(*args, **kwargs):
        raise AssertionError('should not prompt for username when set in global config')

    with patch('c2sync.connector.ConnectHandler', return_value=mock_conn), \
         patch('c2sync.main.user_config.load', return_value={'username': 'admin'}), \
         patch('builtins.input', side_effect=_fail_if_prompted), \
         patch('getpass.getpass', side_effect=['pw', '']):
        main_module.sync(['-y'])

    assert StateEngine(project).state.device_dirty is True


def test_connect_defaults_prompt_for_unknown_hosts_to_false(project, monkeypatch):
    monkeypatch.setenv('C2SYNC_USERNAME', 'admin')
    monkeypatch.setenv('C2SYNC_PASSWORD', 'pw')

    with patch('c2sync.main.user_config.load', return_value={}), \
         patch('c2sync.main.DeviceInterface') as mock_iface_cls:
        main_module._connect(project)

    assert mock_iface_cls.call_args.kwargs['prompt_for_unknown_hosts'] is False


def test_connect_reads_prompt_for_unknown_hosts_from_global_config(project, monkeypatch):
    monkeypatch.setenv('C2SYNC_USERNAME', 'admin')
    monkeypatch.setenv('C2SYNC_PASSWORD', 'pw')

    with patch('c2sync.main.user_config.load', return_value={'prompt_for_unknown_ssh_hosts': True}), \
         patch('c2sync.main.DeviceInterface') as mock_iface_cls:
        main_module._connect(project)

    assert mock_iface_cls.call_args.kwargs['prompt_for_unknown_hosts'] is True


# ------------------------------------------------------------------
# init / global config
# ------------------------------------------------------------------

def test_init_uses_baudrate_from_global_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    with patch('c2sync.main.user_config.load', return_value={'baudrate': 115200}):
        main_module.init(['/dev/ttyUSB0'])

    assert get_project().BAUDRATE == 115200


def test_init_cli_baudrate_overrides_global_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    with patch('c2sync.main.user_config.load', return_value={'baudrate': 115200}):
        main_module.init(['/dev/ttyUSB0', '57600'])

    assert get_project().BAUDRATE == 57600


def test_init_ssh_creates_an_ssh_transport_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))

    main_module.init(['--ssh', '10.0.0.1', '2222'])

    project = get_project()
    assert project.TRANSPORT == 'ssh'
    assert project.HOST == '10.0.0.1'
    assert project.SSH_PORT == 2222
    assert project.target == '10.0.0.1'


def test_init_ssh_defaults_port_to_22(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))

    main_module.init(['--ssh', '10.0.0.1'])

    assert get_project().SSH_PORT == 22


# ------------------------------------------------------------------
# pull
# ------------------------------------------------------------------

def test_pull_fetches_and_commits_running_config(project):
    mock_conn = MagicMock()
    mock_conn.check_enable_mode.return_value = True
    mock_conn.send_command.return_value = 'hostname Router1\ninterface Gi1/0/1\n'

    patches = _mocked_connect(mock_conn)
    with patches[0], patches[1], patches[2]:
        main_module.pull([])

    with open(project.EDIT_FILE) as file:
        assert file.read() == 'hostname Router1\ninterface Gi1/0/1\n'

    assert StateEngine(project).state.host_dirty is False

    # The pulled config is now the git baseline - status shouldn't restage it.
    main_module.status([])
    with open(project.STAGING_FILE) as file:
        assert file.read() == ''


def test_pull_refuses_when_host_dirty_without_force(project):
    _write(project.EDIT_FILE, ['interface Gi1/0/1', ' shutdown'])
    main_module.status([])
    assert StateEngine(project).state.host_dirty is True

    with patch('c2sync.connector.ConnectHandler') as mock_handler:
        with pytest.raises(SystemExit):
            main_module.pull([])
        mock_handler.assert_not_called()


def test_pull_overwrites_host_dirty_edits_with_force(project):
    _write(project.EDIT_FILE, ['interface Gi1/0/1', ' shutdown'])
    main_module.status([])
    assert StateEngine(project).state.host_dirty is True

    mock_conn = MagicMock()
    mock_conn.check_enable_mode.return_value = True
    mock_conn.send_command.return_value = 'hostname Router1\n'

    patches = _mocked_connect(mock_conn)
    with patches[0], patches[1], patches[2]:
        main_module.pull(['-y'])

    with open(project.EDIT_FILE) as file:
        assert file.read() == 'hostname Router1\n'
    assert StateEngine(project).state.host_dirty is False


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


# ------------------------------------------------------------------
# revert
# ------------------------------------------------------------------

def _sync_known_good(project, config_lines):
    """
    Push config_lines via a normal sync, so it becomes the git baseline
    (HEAD) revert tests recover back to.
    """
    _write(project.EDIT_FILE, config_lines)
    mock_conn = MagicMock()
    mock_conn.check_enable_mode.return_value = True
    mock_conn.send_config_set.return_value = 'ok'
    mock_conn.send_command.return_value = '\n'.join(config_lines) + '\n'

    patches = _mocked_connect(mock_conn)
    with patches[0], patches[1], patches[2]:
        main_module.sync(['-y'])


def test_revert_pushes_diff_between_live_config_and_head_by_default(project):
    _sync_known_good(project, ['interface Gi1/0/1', ' shutdown'])

    # Simulate drift: the device's actual running config no longer has the
    # "shutdown" line HEAD says it should (as if a prior push half-failed),
    # and confirms it after the fix lands.
    revert_conn = MagicMock()
    revert_conn.check_enable_mode.return_value = True
    revert_conn.send_config_set.return_value = 'interface Gi1/0/1\n shutdown\nend'
    revert_conn.send_command.side_effect = [
        'interface Gi1/0/1\n',
        'interface Gi1/0/1\n shutdown\n',
    ]

    patches = _mocked_connect(revert_conn)
    with patches[0], patches[1], patches[2]:
        main_module.revert(['-y'])

    pushed_lines = revert_conn.send_config_set.call_args[0][0]
    assert any('shutdown' in line for line in pushed_lines)

    with open(project.EDIT_FILE) as file:
        assert file.read() == 'interface Gi1/0/1\n shutdown\n'

    assert git_ops.show_at_head(project.PROJECT_DIR, 'device.config') == 'interface Gi1/0/1\n shutdown\n'

    state = StateEngine(project).state
    assert state.host_dirty is False
    assert state.device_dirty is True


def test_revert_to_an_explicit_older_commit(project):
    _sync_known_good(project, ['interface Gi1/0/1', ' description GOOD'])
    good_sha = git_ops.resolve_rev(project.PROJECT_DIR, 'HEAD')

    # Move HEAD forward to a second, different sync.
    _sync_known_good(project, ['interface Gi1/0/1', ' description LATER'])

    # The live device now matches the *later* commit's config, unrelated to
    # what we're reverting to - revert should still target good_sha.
    revert_conn = MagicMock()
    revert_conn.check_enable_mode.return_value = True
    revert_conn.send_config_set.return_value = 'ok'
    revert_conn.send_command.side_effect = [
        'interface Gi1/0/1\n description LATER\n',
        'interface Gi1/0/1\n description GOOD\n',
    ]

    patches = _mocked_connect(revert_conn)
    with patches[0], patches[1], patches[2]:
        main_module.revert([good_sha, '-y'])

    pushed_lines = revert_conn.send_config_set.call_args[0][0]
    assert any('description GOOD' in line for line in pushed_lines)
    assert git_ops.show_at_head(project.PROJECT_DIR, 'device.config') == 'interface Gi1/0/1\n description GOOD\n'


def test_revert_unresolvable_commit_exits_without_connecting(project):
    with patch('c2sync.connector.ConnectHandler') as mock_handler:
        with pytest.raises(SystemExit):
            main_module.revert(['not-a-real-commit', '-y'])
        mock_handler.assert_not_called()


def test_revert_nothing_to_revert_when_live_matches_target(project):
    # HEAD (from init) is an empty device.config.
    mock_conn = MagicMock()
    mock_conn.check_enable_mode.return_value = True
    mock_conn.send_command.return_value = ''

    patches = _mocked_connect(mock_conn)
    with patches[0], patches[1], patches[2]:
        main_module.revert(['-y'])

    mock_conn.send_config_set.assert_not_called()


def test_revert_device_rejects_leaves_git_and_state_untouched(project):
    # _sync_known_good already leaves device_dirty True (a normal sync's own
    # effect) - the point of this test is that a *rejected* revert doesn't
    # change it any further, not that it resets to False.
    _sync_known_good(project, ['interface Gi1/0/1', ' shutdown'])
    head_before = git_ops.resolve_rev(project.PROJECT_DIR, 'HEAD')
    device_dirty_before = StateEngine(project).state.device_dirty

    revert_conn = MagicMock()
    revert_conn.check_enable_mode.return_value = True
    revert_conn.send_command.return_value = 'interface Gi1/0/1\n'
    revert_conn.send_config_set.side_effect = ConfigInvalidException('bad command')

    patches = _mocked_connect(revert_conn)
    with patches[0], patches[1], patches[2]:
        with pytest.raises(SystemExit):
            main_module.revert(['-y'])

    assert git_ops.resolve_rev(project.PROJECT_DIR, 'HEAD') == head_before
    assert StateEngine(project).state.device_dirty is device_dirty_before


def test_revert_prompts_and_aborts_without_dash_y(project):
    _sync_known_good(project, ['interface Gi1/0/1', ' shutdown'])
    head_before = git_ops.resolve_rev(project.PROJECT_DIR, 'HEAD')

    revert_conn = MagicMock()
    revert_conn.check_enable_mode.return_value = True
    revert_conn.send_command.return_value = 'interface Gi1/0/1\n'

    patches = _mocked_connect(revert_conn)
    with patches[0], patches[2], patch('builtins.input', side_effect=['admin', 'n']):
        main_module.revert([])

    revert_conn.send_config_set.assert_not_called()
    assert git_ops.resolve_rev(project.PROJECT_DIR, 'HEAD') == head_before
