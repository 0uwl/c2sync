import json
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
    # --dir . puts the project directly in the already-isolated tmp_path,
    # matching how a real user would already be cd'd into it - avoids every
    # test needing its own chdir into a NAME-derived subdirectory.
    main_module.init(['myproject', '/dev/ttyUSB0', '--dir', '.'])
    return get_project()


def _write(path, lines):
    with open(path, 'w') as file:
        file.write('\n'.join(lines) + '\n')


def _running_config(text):
    """
    Wrap fixture config text the way a device actually returns it.

    `show running-config` always terminates with a bare `end`, and the
    connector now relies on that to tell a complete read from one cut short
    (see _clean_running_config). Fixtures that stop mid-config are not
    something a device ever produces, so they'd only be testing a state
    that can't happen. `end` is transparent to the differ - diffing '' to
    'end' yields no commands - so adding it changes no diff expectations.
    """
    if text.strip().splitlines()[-1:] == ['end']:
        return text
    return text + 'end\n' if text.endswith('\n') or not text else text + '\nend\n'


def _device_mock(config_after_push, config_before_push=''):
    """
    A mock that answers `show running-config` with the device's *current*
    state rather than one fixed string.

    push reads the device before sending anything (the out-of-band drift
    check), so a mock that always returns the post-push config would make
    every push look like the device had drifted. Starts at
    config_before_push - '' being a freshly-init'd project's empty baseline
    - and switches to config_after_push once commands are applied.
    """
    mock_conn = MagicMock()
    mock_conn.check_enable_mode.return_value = True
    state = {'config': _running_config(config_before_push)}

    def _apply(lines, **kwargs):
        state['config'] = _running_config(config_after_push)
        return 'ok'

    mock_conn.send_config_set.side_effect = _apply
    mock_conn.send_command.side_effect = lambda *args, **kwargs: state['config']
    return mock_conn


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

    mock_conn = _device_mock('interface Gi1/0/1\n shutdown\n!')

    def _fail_if_prompted(*args, **kwargs):
        raise AssertionError('should not prompt when both env vars are set')

    with patch('c2sync.connector.ConnectHandler', return_value=mock_conn), \
         patch('builtins.input', side_effect=_fail_if_prompted), \
         patch('getpass.getpass', side_effect=_fail_if_prompted):
        main_module.push(['-y'])

    assert StateEngine(project).state.device_dirty is True


def test_connect_falls_back_to_prompt_when_env_partially_set(project, monkeypatch):
    _write(project.EDIT_FILE, ['interface Gi1/0/1', ' shutdown'])

    monkeypatch.setenv('C2SYNC_USERNAME', 'admin')
    monkeypatch.delenv('C2SYNC_PASSWORD', raising=False)

    mock_conn = _device_mock('interface Gi1/0/1\n shutdown\n!')

    patches = _mocked_connect(mock_conn)
    with patches[0], patches[1], patches[2]:
        main_module.push(['-y'])

    assert StateEngine(project).state.device_dirty is True


def test_connect_uses_username_from_global_config(project, monkeypatch):
    _write(project.EDIT_FILE, ['interface Gi1/0/1', ' shutdown'])

    monkeypatch.delenv('C2SYNC_USERNAME', raising=False)
    monkeypatch.delenv('C2SYNC_PASSWORD', raising=False)

    mock_conn = _device_mock('interface Gi1/0/1\n shutdown\n!')

    def _fail_if_prompted(*args, **kwargs):
        raise AssertionError('should not prompt for username when set in global config')

    with patch('c2sync.connector.ConnectHandler', return_value=mock_conn), \
         patch('c2sync.main.user_config.load', return_value={'username': 'admin'}), \
         patch('builtins.input', side_effect=_fail_if_prompted), \
         patch('getpass.getpass', side_effect=['pw', '']):
        main_module.push(['-y'])

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
        main_module.init(['myproject', '/dev/ttyUSB0', '--dir', '.'])

    assert get_project().BAUDRATE == 115200


def test_init_cli_baudrate_overrides_global_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    with patch('c2sync.main.user_config.load', return_value={'baudrate': 115200}):
        main_module.init(['myproject', '/dev/ttyUSB0', '57600', '--dir', '.'])

    assert get_project().BAUDRATE == 57600


def test_init_ssh_creates_an_ssh_transport_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))

    main_module.init(['myproject', '--ssh', '10.0.0.1', '2222', '--dir', '.'])

    project = get_project()
    assert project.TRANSPORT == 'ssh'
    assert project.HOST == '10.0.0.1'
    assert project.SSH_PORT == 2222
    assert project.target == '10.0.0.1'


def test_init_ssh_defaults_port_to_22(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))

    main_module.init(['myproject', '--ssh', '10.0.0.1', '--dir', '.'])

    assert get_project().SSH_PORT == 22


# ------------------------------------------------------------------
# init / project layout
# ------------------------------------------------------------------

def test_init_requires_a_name(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit):
        main_module.init([])

    assert list(tmp_path.iterdir()) == []


def test_init_rejects_a_name_containing_a_path_separator(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit):
        main_module.init(['not/allowed', '/dev/ttyUSB0'])

    assert list(tmp_path.iterdir()) == []


def test_init_rejects_dot_and_dotdot_as_a_name(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    for bad_name in ('.', '..'):
        with pytest.raises(SystemExit):
            main_module.init([bad_name, '/dev/ttyUSB0'])

    assert list(tmp_path.iterdir()) == []


def test_init_creates_a_name_derived_subdirectory_by_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))

    main_module.init(['myrouter', '/dev/ttyUSB0'])

    project_dir = tmp_path / 'myrouter'
    assert project_dir.is_dir()
    # device.config, .gitignore, .git and c2sync.toml are all visible at the
    # top level and git-tracked - the whole point of this layout - while
    # only the operational scratch files are tucked away in .c2sync/.
    assert (project_dir / 'device.config').is_file()
    assert (project_dir / '.gitignore').is_file()
    assert (project_dir / '.git').is_dir()
    assert (project_dir / 'c2sync.toml').is_file()
    assert (project_dir / '.c2sync' / 'staging.txt').is_file()
    assert (project_dir / '.c2sync' / 'state.json').is_file()


def test_init_dir_override_creates_the_project_there_instead(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))

    target = tmp_path / 'elsewhere'
    main_module.init(['myrouter', '/dev/ttyUSB0', '--dir', str(target)])

    assert (target / 'device.config').is_file()
    assert not (tmp_path / 'myrouter').exists()


def test_init_refuses_to_overwrite_an_existing_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))

    main_module.init(['myrouter', '/dev/ttyUSB0', '--dir', '.'])
    with open('device.config', 'w') as file:
        file.write('interface Gi1/0/1\n')

    with pytest.raises(SystemExit):
        main_module.init(['myrouter', '/dev/ttyUSB0', '--dir', '.'])

    # The existing project's edits must survive the refused re-init.
    with open('device.config') as file:
        assert file.read() == 'interface Gi1/0/1\n'


def test_init_pull_flag_onboards_an_existing_device_in_one_step(tmp_path, monkeypatch):
    """
    --pull mirrors `git clone`: the device is already configured, so
    onboarding it is `init` immediately followed by a `pull`, done here in
    one step instead of two.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))

    mock_conn = MagicMock()
    mock_conn.check_enable_mode.return_value = True
    mock_conn.send_command.return_value = _running_config('hostname Router1\ninterface Gi1/0/1\n')

    with patch('c2sync.connector.ConnectHandler', return_value=mock_conn), \
         patch('builtins.input', side_effect=['admin']), \
         patch('getpass.getpass', side_effect=['pw', '']):
        main_module.init(['myrouter', '/dev/ttyUSB0', '--dir', '.', '--pull'])

    with open('device.config') as file:
        assert file.read() == _running_config('hostname Router1\ninterface Gi1/0/1\n')

    project = get_project()
    assert StateEngine(project).state.host_dirty is False
    # The pulled config is the git baseline already - a bare device.config
    # from `init` alone would still be an empty-string baseline instead.
    assert git_ops.show_at_head(project.PROJECT_DIR, 'device.config') == _running_config('hostname Router1\ninterface Gi1/0/1\n')


def test_init_without_pull_flag_still_creates_an_empty_baseline(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))

    with patch('c2sync.connector.ConnectHandler') as mock_handler:
        main_module.init(['myrouter', '/dev/ttyUSB0', '--dir', '.'])
        mock_handler.assert_not_called()

    with open('device.config') as file:
        assert file.read() == ''


def test_project_name_is_stored_and_reloaded(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))

    main_module.init(['myrouter', '/dev/ttyUSB0', '--dir', '.'])

    assert get_project().NAME == 'myrouter'


# ------------------------------------------------------------------
# pull
# ------------------------------------------------------------------

def test_pull_fetches_and_commits_running_config(project):
    mock_conn = MagicMock()
    mock_conn.check_enable_mode.return_value = True
    mock_conn.send_command.return_value = _running_config('hostname Router1\ninterface Gi1/0/1\n')

    patches = _mocked_connect(mock_conn)
    with patches[0], patches[1], patches[2]:
        main_module.pull([])

    with open(project.EDIT_FILE) as file:
        assert file.read() == _running_config('hostname Router1\ninterface Gi1/0/1\n')

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


def test_pull_dash_y_does_not_overwrite_host_dirty_edits(project):
    """
    pull has no other prompt -y would otherwise skip, so unlike
    sync/commit it doesn't recognize -y at all here - only --force/-f
    overwrites local edits, never a flag that reads as "just don't ask me
    anything".
    """
    _write(project.EDIT_FILE, ['interface Gi1/0/1', ' shutdown'])
    main_module.status([])
    assert StateEngine(project).state.host_dirty is True

    with patch('c2sync.connector.ConnectHandler') as mock_handler:
        with pytest.raises(SystemExit):
            main_module.pull(['-y'])
        mock_handler.assert_not_called()


def test_pull_overwrites_host_dirty_edits_with_force(project):
    _write(project.EDIT_FILE, ['interface Gi1/0/1', ' shutdown'])
    main_module.status([])
    assert StateEngine(project).state.host_dirty is True

    mock_conn = MagicMock()
    mock_conn.check_enable_mode.return_value = True
    mock_conn.send_command.return_value = _running_config('hostname Router1\n')

    patches = _mocked_connect(mock_conn)
    with patches[0], patches[1], patches[2]:
        main_module.pull(['--force'])

    with open(project.EDIT_FILE) as file:
        assert file.read() == _running_config('hostname Router1\n')
    assert StateEngine(project).state.host_dirty is False


def test_pull_short_dash_f_also_overwrites_host_dirty_edits(project):
    _write(project.EDIT_FILE, ['interface Gi1/0/1', ' shutdown'])
    main_module.status([])

    mock_conn = MagicMock()
    mock_conn.check_enable_mode.return_value = True
    mock_conn.send_command.return_value = _running_config('hostname Router1\n')

    patches = _mocked_connect(mock_conn)
    with patches[0], patches[1], patches[2]:
        main_module.pull(['-f'])

    assert StateEngine(project).state.host_dirty is False


# ------------------------------------------------------------------
# fetch
# ------------------------------------------------------------------

def test_fetch_reports_no_drift_when_device_matches_baseline(project, capsys):
    _sync_known_good(project, ['interface Gi1/0/1', ' description Server'])

    mock_conn = MagicMock()
    mock_conn.check_enable_mode.return_value = True
    mock_conn.send_command.return_value = _running_config('interface Gi1/0/1\n description Server\n')

    patches = _mocked_connect(mock_conn)
    with patches[0], patches[1], patches[2]:
        main_module.fetch([])

    output = capsys.readouterr().out
    assert 'No drift' in output


def test_fetch_reports_drift_without_touching_any_local_state(project, capsys):
    """
    fetch is read-only - it must not write EDIT_FILE, advance the git
    baseline, or touch staging/state, unlike pull or push's own drift
    adoption.
    """
    _sync_known_good(project, ['interface Gi1/0/1', ' description Server'])

    baseline_before = git_ops.show_at_head(project.PROJECT_DIR, 'device.config')
    with open(project.EDIT_FILE) as file:
        edit_file_before = file.read()
    with open(project.STAGING_FILE) as file:
        staging_before = file.read()

    mock_conn = MagicMock()
    mock_conn.check_enable_mode.return_value = True
    mock_conn.send_command.return_value = _running_config('interface Gi1/0/1\n description Colleague\n')

    patches = _mocked_connect(mock_conn)
    with patches[0], patches[1], patches[2]:
        main_module.fetch([])

    output = capsys.readouterr().out
    assert 'description Colleague' in output
    mock_conn.send_config_set.assert_not_called()

    assert git_ops.show_at_head(project.PROJECT_DIR, 'device.config') == baseline_before
    with open(project.EDIT_FILE) as file:
        assert file.read() == edit_file_before
    with open(project.STAGING_FILE) as file:
        assert file.read() == staging_before
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
# push
# ------------------------------------------------------------------

def test_push_pushes_staged_changes_and_updates_baseline(project):
    _write(project.EDIT_FILE, ['interface Gi1/0/1', ' shutdown'])

    mock_conn = _device_mock('interface Gi1/0/1\n shutdown\n!')

    patches = _mocked_connect(mock_conn)
    with patches[0], patches[1], patches[2]:
        main_module.push(['-y'])

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


def test_push_partial_push_moves_baseline_to_what_actually_landed(project):
    """
    apply_config aborts on the first rejected command, but the commands
    before it are already on the device. The baseline has to move to match,
    or `status` reports the landed commands as still-unpushed local edits -
    which is exactly the misleading state this reconciliation exists to fix.
    """
    _write(project.EDIT_FILE, ['interface Gi1/0/1', ' shutdown', ' bogus-command'])

    # ' shutdown' landed; ' bogus-command' is what the device rejected.
    partial_live = 'interface Gi1/0/1\n shutdown\n!\n'

    mock_conn = MagicMock()
    mock_conn.check_enable_mode.return_value = True
    # '' is the drift check reading a device that still matches the empty
    # baseline; partial_live is the reconciliation reading it afterwards.
    mock_conn.send_config_set.side_effect = ConfigInvalidException('bad command')
    mock_conn.send_command.side_effect = [_running_config(''), _running_config(partial_live)]

    patches = _mocked_connect(mock_conn)
    with patches[0], patches[1], patches[2]:
        with pytest.raises(SystemExit):
            main_module.push(['-y'])

    # The baseline is now what the device actually has...
    assert git_ops.show_at_head(project.PROJECT_DIR, project.edit_file_relpath) == _running_config(partial_live)
    # ...while the user's edits are untouched.
    with open(project.EDIT_FILE) as file:
        assert 'bogus-command' in file.read()

    # Landed commands are in running-config but not startup-config.
    state = StateEngine(project).state
    assert state.device_dirty is True
    assert state.host_dirty is True

    # Staging now lists only what still has to be pushed.
    with open(project.STAGING_FILE) as file:
        staged = file.read()
    assert 'bogus-command' in staged
    assert 'shutdown' not in staged


def test_push_rejected_first_command_leaves_state_intact(project):
    """
    The other half of the same path: when nothing landed, the baseline must
    not move and the device must not be marked dirty.
    """
    # Seed a baseline the mock device can return verbatim. A freshly-init'd
    # project's baseline is empty, which no real device ever reports - and
    # "the device still matches the baseline" is the whole premise here.
    git_ops.commit_content(
        project.PROJECT_DIR, project.edit_file_relpath,
        _running_config('interface Gi1/0/1\n'), 'seed baseline')
    _write(project.EDIT_FILE, ['interface Gi1/0/1', ' bogus-command'])

    baseline_before = git_ops.show_at_head(project.PROJECT_DIR, project.edit_file_relpath)

    mock_conn = MagicMock()
    mock_conn.check_enable_mode.return_value = True
    mock_conn.send_config_set.side_effect = ConfigInvalidException('bad command')
    # Device is unchanged - the first command was the rejected one.
    mock_conn.send_command.return_value = _running_config(baseline_before)

    patches = _mocked_connect(mock_conn)
    with patches[0], patches[1], patches[2]:
        with pytest.raises(SystemExit):
            main_module.push(['-y'])

    state = StateEngine(project).state
    assert state.host_dirty is True
    assert state.device_dirty is False
    assert git_ops.show_at_head(project.PROJECT_DIR, project.edit_file_relpath) == _running_config(baseline_before)

    with open(project.STAGING_FILE) as file:
        assert 'bogus-command' in file.read()


def test_push_does_not_roll_back_without_the_flag(project):
    """
    Undoing a partial push sends more config to a device that just rejected
    some, so it must never happen unasked.
    """
    _write(project.EDIT_FILE, ['interface Gi1/0/1', ' shutdown', ' bogus-command'])

    mock_conn = MagicMock()
    mock_conn.check_enable_mode.return_value = True
    mock_conn.send_config_set.side_effect = ConfigInvalidException('bad command')
    mock_conn.send_command.side_effect = [_running_config(''), _running_config('interface Gi1/0/1\n shutdown\n!\n')]

    patches = _mocked_connect(mock_conn)
    with patches[0], patches[1], patches[2]:
        with pytest.raises(SystemExit):
            main_module.push(['-y'])

    # Only the original push was attempted - no correction was sent.
    assert mock_conn.send_config_set.call_count == 1


def test_push_rollback_on_error_pushes_device_back_to_pre_push_baseline(project):
    _write(project.EDIT_FILE, ['interface Gi1/0/1', ' shutdown', ' bogus-command'])

    baseline_before = git_ops.show_at_head(project.PROJECT_DIR, project.edit_file_relpath)
    partial_live = 'interface Gi1/0/1\n shutdown\n!\n'

    mock_conn = MagicMock()
    mock_conn.check_enable_mode.return_value = True
    # First push is rejected; the rollback push that follows succeeds.
    mock_conn.send_config_set.side_effect = [ConfigInvalidException('bad command'), 'ok']
    mock_conn.send_command.side_effect = [
        _running_config(baseline_before),   # drift check: device still matches the baseline
        _running_config(partial_live),      # reconciliation: what actually landed
        _running_config(partial_live),      # rollback: live config to diff against the baseline
        _running_config(baseline_before),   # rollback: state after the correction was applied
    ]

    patches = _mocked_connect(mock_conn)
    with patches[0], patches[1], patches[2]:
        with pytest.raises(SystemExit):
            main_module.push(['-y', '--rollback-on-error'])

    # A correction was actually pushed, and it undid the landed command.
    assert mock_conn.send_config_set.call_count == 2
    rollback_lines = mock_conn.send_config_set.call_args_list[1].args[0]
    assert any('no ' in line for line in rollback_lines)

    # Baseline records the recovered device, edits still untouched.
    assert git_ops.show_at_head(project.PROJECT_DIR, project.edit_file_relpath) == _running_config(baseline_before)
    with open(project.EDIT_FILE) as file:
        assert 'bogus-command' in file.read()

    assert StateEngine(project).state.device_dirty is True


def test_push_rollback_on_error_asks_before_pushing_the_correction(project):
    """
    -y skips the push preview; it must not also silently authorise sending
    a second, corrective batch to a device that is already misbehaving.

    Prompt order here is: the username prompt, the push confirmation, then
    the rollback confirmation - which is the 'n'. The push confirmation now
    comes after connecting, because the preview can only be computed once
    the device has been checked for out-of-band drift.
    """
    _write(project.EDIT_FILE, ['interface Gi1/0/1', ' shutdown', ' bogus-command'])

    mock_conn = MagicMock()
    mock_conn.check_enable_mode.return_value = True
    live = 'interface Gi1/0/1\n shutdown\n!\n'
    mock_conn.send_config_set.side_effect = ConfigInvalidException('bad command')
    mock_conn.send_command.side_effect = [_running_config(''), _running_config(live), _running_config(live)]

    with patch('c2sync.connector.ConnectHandler', return_value=mock_conn), \
         patch('builtins.input', side_effect=['admin', 'y', 'n']), \
         patch('getpass.getpass', side_effect=['pw', '']):
        with pytest.raises(SystemExit):
            main_module.push(['--rollback-on-error'])

    # Declining the rollback prompt means no correction is sent.
    assert mock_conn.send_config_set.call_count == 1


def test_push_detects_out_of_band_change_and_refuses_under_dash_y(project):
    """
    The staged commands are computed offline against the baseline, so a
    device someone else changed makes the preview describe a device that no
    longer exists. -y must not stand in for approving that.
    """
    _sync_known_good(project, ['interface Gi1/0/1', ' description Server'])
    _write(project.EDIT_FILE, ['interface Gi1/0/1', ' description Mine'])

    baseline_before = git_ops.show_at_head(project.PROJECT_DIR, 'device.config')

    mock_conn = MagicMock()
    mock_conn.check_enable_mode.return_value = True
    # Someone else changed the description on the console.
    mock_conn.send_command.return_value = _running_config('interface Gi1/0/1\n description Colleague\n')

    patches = _mocked_connect(mock_conn)
    with patches[0], patches[1], patches[2]:
        with pytest.raises(SystemExit) as excinfo:
            main_module.push(['-y'])

    assert excinfo.value.code == 1
    mock_conn.send_config_set.assert_not_called()
    # Nothing was adopted or committed behind the user's back.
    assert git_ops.show_at_head(project.PROJECT_DIR, 'device.config') == baseline_before


def test_push_declining_the_drift_prompt_sends_nothing(project):
    _sync_known_good(project, ['interface Gi1/0/1', ' description Server'])
    _write(project.EDIT_FILE, ['interface Gi1/0/1', ' description Mine'])

    mock_conn = MagicMock()
    mock_conn.check_enable_mode.return_value = True
    mock_conn.send_command.return_value = _running_config('interface Gi1/0/1\n description Colleague\n')

    with patch('c2sync.connector.ConnectHandler', return_value=mock_conn), \
         patch('builtins.input', side_effect=['admin', 'n']), \
         patch('getpass.getpass', side_effect=['pw', '']):
        with pytest.raises(SystemExit):
            main_module.push([])

    mock_conn.send_config_set.assert_not_called()


def test_push_adopting_drift_recomputes_against_the_live_config(project):
    """
    Adopting moves the baseline to the device without touching EDIT_FILE,
    so the user's edits survive and the recomputed preview is honest about
    also undoing the out-of-band change.
    """
    _sync_known_good(project, ['interface Gi1/0/1', ' description Server'])
    _write(project.EDIT_FILE, ['interface Gi1/0/1', ' description Mine'])

    live = 'interface Gi1/0/1\n description Colleague\n'
    mock_conn = _device_mock('interface Gi1/0/1\n description Mine\n', config_before_push=live)

    patches = _mocked_connect(mock_conn)
    with patches[0], patches[1], patches[2]:
        main_module.push(['-y', '--rebase'])

    pushed = mock_conn.send_config_set.call_args.args[0]
    # Recomputed against the live device, not the stale baseline.
    assert any('description Mine' in line for line in pushed)

    # The user's edits were never clobbered by the adoption.
    with open(project.EDIT_FILE) as file:
        assert 'description Mine' in file.read()

    # The adoption is visible in history rather than silent.
    log = git_ops._run(project.PROJECT_DIR, 'log', '--oneline')
    assert 'adopted out-of-band changes' in log


def test_push_does_not_prompt_when_device_matches_baseline(project):
    """
    The drift check must be invisible when nothing drifted - no extra
    prompt, no extra commit.
    """
    _sync_known_good(project, ['interface Gi1/0/1', ' description Server'])
    commits_before = git_ops._run(project.PROJECT_DIR, 'rev-list', '--count', 'HEAD').strip()

    _write(project.EDIT_FILE, ['interface Gi1/0/1', ' description Mine'])

    mock_conn = _device_mock(
        'interface Gi1/0/1\n description Mine\n',
        config_before_push='interface Gi1/0/1\n description Server\n',
    )

    patches = _mocked_connect(mock_conn)
    with patches[0], patches[1], patches[2]:
        main_module.push(['-y'])

    commits_after = git_ops._run(project.PROJECT_DIR, 'rev-list', '--count', 'HEAD').strip()
    # One new commit for the push itself, none for an adoption.
    assert int(commits_after) == int(commits_before) + 1


def test_push_drift_that_already_matches_edits_sends_nothing(project):
    """
    Someone else made the same change first: adopting leaves nothing to
    push, and that is a success, not an error.
    """
    _sync_known_good(project, ['interface Gi1/0/1', ' description Server'])
    _write(project.EDIT_FILE, ['interface Gi1/0/1', ' description Mine'])

    mock_conn = MagicMock()
    mock_conn.check_enable_mode.return_value = True
    mock_conn.send_command.return_value = _running_config('interface Gi1/0/1\n description Mine\n')

    patches = _mocked_connect(mock_conn)
    with patches[0], patches[1], patches[2]:
        main_module.push(['-y', '--rebase'])

    mock_conn.send_config_set.assert_not_called()
    assert StateEngine(project).state.host_dirty is False


def test_push_with_no_edits_does_not_connect(project):
    with patch('c2sync.connector.ConnectHandler') as mock_handler:
        main_module.push(['-y'])
        mock_handler.assert_not_called()


# ------------------------------------------------------------------
# save
# ------------------------------------------------------------------

def test_save_refuses_while_host_dirty(project):
    _write(project.EDIT_FILE, ['interface Gi1/0/1', ' shutdown'])
    main_module.status([])

    with patch('c2sync.connector.ConnectHandler') as mock_handler:
        with pytest.raises(SystemExit):
            main_module.save(['-y'])
        mock_handler.assert_not_called()


def test_save_persists_and_marks_device_clean(project):
    StateEngine(project).mark_device_dirty()

    mock_conn = MagicMock()
    mock_conn.save_config.return_value = 'Building configuration...\n[OK]'

    patches = _mocked_connect(mock_conn)
    with patches[0], patches[1], patches[2]:
        main_module.save(['-y'])

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
    # Start the mock device at the current baseline, not always empty -
    # otherwise a second call looks to the drift check like the device was
    # wiped out-of-band since the first one.
    current = git_ops.show_at_head(project.PROJECT_DIR, 'device.config') or ''
    mock_conn = _device_mock('\n'.join(config_lines) + '\n', config_before_push=current)

    patches = _mocked_connect(mock_conn)
    with patches[0], patches[1], patches[2]:
        main_module.push(['-y'])


def test_revert_pushes_diff_between_live_config_and_head_by_default(project):
    _sync_known_good(project, ['interface Gi1/0/1', ' shutdown'])

    # Simulate drift: the device's actual running config no longer has the
    # "shutdown" line HEAD says it should (as if a prior push half-failed),
    # and confirms it after the fix lands.
    revert_conn = MagicMock()
    revert_conn.check_enable_mode.return_value = True
    revert_conn.send_config_set.return_value = 'interface Gi1/0/1\n shutdown\nend'
    revert_conn.send_command.side_effect = [
        _running_config('interface Gi1/0/1\n'),
        _running_config('interface Gi1/0/1\n shutdown\n'),
    ]

    patches = _mocked_connect(revert_conn)
    with patches[0], patches[1], patches[2]:
        main_module.revert(['-y'])

    pushed_lines = revert_conn.send_config_set.call_args[0][0]
    assert any('shutdown' in line for line in pushed_lines)

    with open(project.EDIT_FILE) as file:
        assert file.read() == _running_config('interface Gi1/0/1\n shutdown\n')

    assert git_ops.show_at_head(project.PROJECT_DIR, 'device.config') == _running_config('interface Gi1/0/1\n shutdown\n')

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
        _running_config('interface Gi1/0/1\n description LATER\n'),
        _running_config('interface Gi1/0/1\n description GOOD\n'),
    ]

    patches = _mocked_connect(revert_conn)
    with patches[0], patches[1], patches[2]:
        main_module.revert([good_sha, '-y'])

    pushed_lines = revert_conn.send_config_set.call_args[0][0]
    assert any('description GOOD' in line for line in pushed_lines)
    assert git_ops.show_at_head(project.PROJECT_DIR, 'device.config') == _running_config('interface Gi1/0/1\n description GOOD\n')


def test_revert_unresolvable_commit_exits_without_connecting(project):
    with patch('c2sync.connector.ConnectHandler') as mock_handler:
        with pytest.raises(SystemExit):
            main_module.revert(['not-a-real-commit', '-y'])
        mock_handler.assert_not_called()


def test_revert_nothing_to_revert_when_live_matches_target(project):
    # HEAD (from init) is an empty device.config.
    mock_conn = MagicMock()
    mock_conn.check_enable_mode.return_value = True
    mock_conn.send_command.return_value = _running_config('')

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
    revert_conn.send_command.return_value = _running_config('interface Gi1/0/1\n')
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
    revert_conn.send_command.return_value = _running_config('interface Gi1/0/1\n')

    patches = _mocked_connect(revert_conn)
    with patches[0], patches[2], patch('builtins.input', side_effect=['admin', 'n']):
        main_module.revert([])

    revert_conn.send_config_set.assert_not_called()
    assert git_ops.resolve_rev(project.PROJECT_DIR, 'HEAD') == head_before


def test_revert_refuses_when_host_dirty_without_force(project):
    _sync_known_good(project, ['interface Gi1/0/1', ' shutdown'])
    _write(project.EDIT_FILE, ['interface Gi1/0/1', ' shutdown', ' description unpushed'])
    main_module.status([])
    assert StateEngine(project).state.host_dirty is True

    with patch('c2sync.connector.ConnectHandler') as mock_handler:
        with pytest.raises(SystemExit):
            main_module.revert(['-y'])
        mock_handler.assert_not_called()

    # EDIT_FILE must be untouched - refusing must happen before ever
    # connecting or overwriting anything.
    with open(project.EDIT_FILE) as file:
        assert 'description unpushed' in file.read()


def test_revert_overwrites_host_dirty_edits_with_force(project):
    _sync_known_good(project, ['interface Gi1/0/1', ' shutdown'])
    _write(project.EDIT_FILE, ['interface Gi1/0/1', ' shutdown', ' description unpushed'])
    main_module.status([])
    assert StateEngine(project).state.host_dirty is True

    revert_conn = MagicMock()
    revert_conn.check_enable_mode.return_value = True
    revert_conn.send_config_set.return_value = 'ok'
    revert_conn.send_command.side_effect = [
        _running_config('interface Gi1/0/1\n'),
        _running_config('interface Gi1/0/1\n shutdown\n'),
    ]

    patches = _mocked_connect(revert_conn)
    with patches[0], patches[1], patches[2]:
        main_module.revert(['-y', '--force'])

    with open(project.EDIT_FILE) as file:
        assert 'description unpushed' not in file.read()
    assert StateEngine(project).state.host_dirty is False


def test_revert_short_dash_f_also_overwrites_host_dirty_edits(project):
    _sync_known_good(project, ['interface Gi1/0/1', ' shutdown'])
    _write(project.EDIT_FILE, ['interface Gi1/0/1', ' shutdown', ' description unpushed'])
    main_module.status([])

    revert_conn = MagicMock()
    revert_conn.check_enable_mode.return_value = True
    revert_conn.send_config_set.return_value = 'ok'
    revert_conn.send_command.side_effect = [
        _running_config('interface Gi1/0/1\n'),
        _running_config('interface Gi1/0/1\n shutdown\n'),
    ]

    patches = _mocked_connect(revert_conn)
    with patches[0], patches[1], patches[2]:
        main_module.revert(['-y', '-f'])

    assert StateEngine(project).state.host_dirty is False


def test_revert_force_alone_does_not_skip_the_push_confirmation(project):
    """
    --force only concerns the host_dirty overwrite, not the push-preview
    prompt - the two are independent flags on purpose.
    """
    _sync_known_good(project, ['interface Gi1/0/1', ' shutdown'])

    revert_conn = MagicMock()
    revert_conn.check_enable_mode.return_value = True
    revert_conn.send_command.return_value = _running_config('interface Gi1/0/1\n')

    patches = _mocked_connect(revert_conn)
    with patches[0], patches[2], patch('builtins.input', side_effect=['admin', 'n']):
        main_module.revert(['--force'])

    revert_conn.send_config_set.assert_not_called()


def test_revert_disconnects_even_if_fetching_live_config_raises(project):
    """
    Regression test for the disconnect leak a rejected/failed mid-flow call
    used to cause: _connected's try/finally must run disconnect() even when
    get_running_config() itself raises, not just on the expected error paths.
    """
    _sync_known_good(project, ['interface Gi1/0/1', ' shutdown'])

    revert_conn = MagicMock()
    revert_conn.check_enable_mode.return_value = True
    revert_conn.send_command.side_effect = RuntimeError('session dropped')

    patches = _mocked_connect(revert_conn)
    with patches[0], patches[1], patches[2]:
        with pytest.raises(RuntimeError):
            main_module.revert(['-y'])

    revert_conn.disconnect.assert_called_once()


# ------------------------------------------------------------------
# help / unknown commands
# ------------------------------------------------------------------

@pytest.mark.parametrize('argv', [
    ['c2sync'],
    ['c2sync', 'help'],
    ['c2sync', '-h'],
    ['c2sync', '--help'],
])
def test_help_prints_usage_to_stdout_and_exits_cleanly(argv, monkeypatch, capsys):
    monkeypatch.setattr('sys.argv', argv)

    main_module.main()

    captured = capsys.readouterr()
    assert 'Usage:' in captured.out
    assert 'revert' in captured.out
    assert captured.err == ''


ALL_COMMANDS = ['init', 'pull', 'fetch', 'status', 'push', 'save', 'discard', 'revert']


@pytest.mark.parametrize('command', ALL_COMMANDS)
@pytest.mark.parametrize('flag', ['-h', '--help'])
def test_help_flag_after_a_command_prints_its_own_help_without_running_it(
    command, flag, monkeypatch, capsys, tmp_path
):
    # Guards a real footgun: without this, `c2sync init --help` would start a
    # project for a device literally named '--help'.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr('sys.argv', ['c2sync', command, flag])

    main_module.main()

    out = capsys.readouterr().out
    assert f'Usage: c2sync {command}' in out
    # Layout-agnostic: a real `init` run would land in a NAME-derived
    # subdirectory, not tmp_path/.c2sync directly, so the meaningful check
    # is that nothing at all got created in the isolated tmp_path.
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('command', ALL_COMMANDS)
def test_help_subcommand_prints_that_commands_help(command, monkeypatch, capsys):
    monkeypatch.setattr('sys.argv', ['c2sync', 'help', command])

    main_module.main()

    assert f'Usage: c2sync {command}' in capsys.readouterr().out


def test_every_command_has_help_text():
    # A new command added to the dispatch without help text should fail here
    # rather than silently falling back to the top-level usage.
    assert sorted(main_module.COMMAND_HELP) == sorted(ALL_COMMANDS)


@pytest.mark.parametrize('command', ALL_COMMANDS)
def test_help_flag_is_caught_in_any_argument_position(command, monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr('sys.argv', ['c2sync', command, 'somevalue', '--help'])

    main_module.main()

    assert f'Usage: c2sync {command}' in capsys.readouterr().out
    # Layout-agnostic: a real `init` run would land in a NAME-derived
    # subdirectory, not tmp_path/.c2sync directly, so the meaningful check
    # is that nothing at all got created in the isolated tmp_path.
    assert list(tmp_path.iterdir()) == []


def test_help_for_unknown_topic_falls_back_to_top_level_usage(monkeypatch, capsys):
    monkeypatch.setattr('sys.argv', ['c2sync', 'help', 'bogus'])

    main_module.main()

    out = capsys.readouterr().out
    assert 'Usage:' in out
    assert 'Commands:' in out


def test_unknown_command_exits_nonzero_with_usage_on_stderr(monkeypatch, capsys):
    monkeypatch.setattr('sys.argv', ['c2sync', 'bogus'])

    with pytest.raises(SystemExit) as excinfo:
        main_module.main()

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert 'Usage:' in captured.err
    assert 'Usage:' not in captured.out


# ------------------------------------------------------------------
# guards that read host_dirty
#
# Regression guards for a live-device failure: host_dirty used to be a
# persisted flag refreshed only by `status`/`push`, so editing device.config
# and running `pull` straight afterwards found a stale "clean" and silently
# overwrote the edits. Each test below edits the file and runs the command
# immediately - never calling status first - because that ordering is the
# whole bug.
# ------------------------------------------------------------------

def _stale_clean_state(project):
    """Persist the state an older c2sync would have left behind."""
    with open(project.STATE_FILE, 'w') as file:
        json.dump({'host_dirty': False, 'device_dirty': False}, file)


def test_pull_refuses_to_clobber_edits_made_since_the_last_status(project, capsys):
    _sync_known_good(project, ['interface Gi1/0/1', ' description Server'])
    _write(project.EDIT_FILE, ['interface Gi1/0/1', ' description Unpushed'])
    _stale_clean_state(project)

    mock_conn = _device_mock('interface Gi1/0/1\n description Server\n')
    patches = _mocked_connect(mock_conn)
    with patches[0], patches[1], patches[2]:
        with pytest.raises(SystemExit):
            main_module.pull([])

    with open(project.EDIT_FILE) as file:
        assert 'description Unpushed' in file.read()
    assert 'unpushed local edits' in capsys.readouterr().out


def test_pull_force_still_overwrites_those_edits(project):
    _sync_known_good(project, ['interface Gi1/0/1', ' description Server'])
    _write(project.EDIT_FILE, ['interface Gi1/0/1', ' description Unpushed'])
    _stale_clean_state(project)

    mock_conn = _device_mock('interface Gi1/0/1\n description Server\n',
                             config_before_push='interface Gi1/0/1\n description Server\n')
    patches = _mocked_connect(mock_conn)
    with patches[0], patches[1], patches[2]:
        main_module.pull(['--force'])

    with open(project.EDIT_FILE) as file:
        assert 'description Unpushed' not in file.read()


def test_revert_refuses_to_clobber_edits_made_since_the_last_status(project, capsys):
    _sync_known_good(project, ['interface Gi1/0/1', ' description Server'])
    _write(project.EDIT_FILE, ['interface Gi1/0/1', ' description Unpushed'])
    _stale_clean_state(project)

    with patch('c2sync.connector.ConnectHandler') as mock_handler:
        with pytest.raises(SystemExit):
            main_module.revert(['-y'])
        # Refused before opening a session at all.
        mock_handler.assert_not_called()

    with open(project.EDIT_FILE) as file:
        assert 'description Unpushed' in file.read()
    assert 'unpushed local edits' in capsys.readouterr().out


def test_save_refuses_while_edits_are_unpushed(project, capsys):
    _sync_known_good(project, ['interface Gi1/0/1', ' description Server'])
    _write(project.EDIT_FILE, ['interface Gi1/0/1', ' description Unpushed'])
    _stale_clean_state(project)

    with patch('c2sync.connector.ConnectHandler') as mock_handler:
        with pytest.raises(SystemExit):
            main_module.save(['-y'])
        mock_handler.assert_not_called()

    assert 'unpushed local edits' in capsys.readouterr().out


# ------------------------------------------------------------------
# failed push: the baseline must survive an unreadable re-read
# ------------------------------------------------------------------

def test_failed_push_keeps_the_baseline_when_the_reread_is_not_a_config(project, capsys):
    """
    The worst failure found on real hardware. After a rejected command the
    session was still in config mode, so reconciliation's re-read returned
    the device's error text - which was committed as the baseline, replacing
    242 lines with 2. `status` then listed the entire config as outstanding,
    led by `no ^`.

    The connector now refuses to return that; this asserts push leaves the
    last good baseline in place instead of committing whatever came back.
    """
    _sync_known_good(project, ['interface Gi1/0/1', ' description Server'])
    _write(project.EDIT_FILE, ['interface Gi1/0/1', ' description Server', ' bogus-command'])

    baseline_before = git_ops.show_at_head(project.PROJECT_DIR, project.edit_file_relpath)

    mock_conn = MagicMock()
    mock_conn.check_enable_mode.return_value = True
    mock_conn.send_config_set.side_effect = ConfigInvalidException('bad command')
    mock_conn.send_command.side_effect = [
        baseline_before,                                    # drift check: device matches
        "                    ^\n% Invalid input detected at '^' marker.\n",  # the bad re-read
    ]

    patches = _mocked_connect(mock_conn)
    with patches[0], patches[1], patches[2]:
        with pytest.raises(SystemExit):
            main_module.push(['-y'])

    assert git_ops.show_at_head(project.PROJECT_DIR, project.edit_file_relpath) == baseline_before

    output = capsys.readouterr().out
    assert 'pull --force' in output


# ------------------------------------------------------------------
# no terminal to prompt on
# ------------------------------------------------------------------

def test_missing_credentials_without_a_tty_exits_cleanly(project, monkeypatch, capsys):
    _write(project.EDIT_FILE, ['interface Gi1/0/1', ' shutdown'])
    monkeypatch.delenv('C2SYNC_USERNAME', raising=False)
    monkeypatch.delenv('C2SYNC_PASSWORD', raising=False)

    with patch('builtins.input', side_effect=EOFError()):
        with pytest.raises(SystemExit) as exit_info:
            main_module.push(['-y'])

    assert exit_info.value.code == 1
    assert 'C2SYNC_USERNAME' in capsys.readouterr().err


def test_confirmation_without_a_tty_declines_rather_than_proceeding(project):
    """
    "Nobody was there to answer" must never resolve to yes - these prompts
    guard writes to a live device.
    """
    with patch('builtins.input', side_effect=EOFError()):
        assert main_module._confirm('Proceed?') is False
