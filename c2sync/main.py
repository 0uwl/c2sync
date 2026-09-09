import getpass
import logging
import os
import sys

from netmiko.exceptions import NetmikoAuthenticationException, NetmikoTimeoutException

from c2sync import Project, get_project, git_ops, init_project, user_config
from c2sync.connector import SerialInterface
from c2sync.differ import Differ
from c2sync.exceptions import ConfigApplyError, ConfigSaveError
from c2sync.state_engine import StateEngine

LOGGER = logging.getLogger(__name__)

USAGE = """
Usage:
c2sync COMMAND

Commands:
    init         Start a C2Sync session in the current working directory
    status       Show whether the local config file has unsynced edits
    sync         Preview changes and confirm or abort them
    commit       Issues the command to save the running config to the startup config on the device
    discard      Cancel the current C2Sync session
"""

def main():
    try:
        command = sys.argv[1]
    except IndexError:
        print(USAGE)
        return

    command_arguments = sys.argv[2:]

    match command:
        case 'init':
            init(command_arguments)
        case 'status':
            status(command_arguments)
        case 'sync':
            sync(command_arguments)
        case 'commit':
            commit(command_arguments)
        case 'discard':
            discard(command_arguments)
        case _:
            LOGGER.error(f'Unknown command {command}')
            print(USAGE)


def init(arguments: list):
    LOGGER.debug(f'Given arguments: {arguments}')

    if not arguments:
        print('Usage: c2sync init SERIAL_DEVICE [BAUDRATE]')
        sys.exit(1)

    config = user_config.load()

    serial_device = arguments[0]
    baudrate = int(arguments[1]) if len(arguments) > 1 else config.get('baudrate', 9600)

    project_kwargs = {'SERIAL_DEVICE': serial_device, 'BAUDRATE': baudrate}
    if 'timeout' in config:
        project_kwargs['TIMEOUT'] = config['timeout']
    if 'prompt_regex' in config:
        project_kwargs['PROMPT_REGEX'] = config['prompt_regex']

    init_project(Project(**project_kwargs))


def status(arguments: list):
    LOGGER.debug(f'Given arguments: {arguments}')

    project = _require_project()
    lines = _refresh_staging(project)

    state = StateEngine(project).state
    print(f'Device state: {state.label}')

    if lines:
        print('\nStaged commands (run `c2sync sync` to push):\n')
        for line in lines:
            print(f'  {line}')
    elif state.device_dirty:
        print('Running config has not been saved to startup-config yet (run `c2sync commit`).')
    else:
        print('Nothing staged.')


def sync(arguments: list):
    LOGGER.debug(f'Given arguments: {arguments}')
    force = '-y' in arguments

    project = _require_project()
    lines = _refresh_staging(project)

    if not lines:
        print('Nothing staged to sync.')
        return

    print('The following commands will be sent to the device:\n')
    for line in lines:
        print(f'  {line}')

    if not force and not _confirm('\nProceed?'):
        print('Aborted.')
        return

    state_engine = StateEngine(project)
    interface = _connect(project)

    try:
        interface.apply_config(lines)
    except ConfigApplyError as e:
        print(f'\nDevice rejected the configuration, nothing was applied:\n{e}')
        interface.disconnect()
        sys.exit(1)

    # The push is Netmiko-confirmed at this point: clear what's staged and
    # record that the device now has unsaved (running-config-only) changes.
    Differ(project).clear_staging()
    state_engine.mark_host_clean()
    state_engine.mark_device_dirty()

    # The device is now the source of truth again - refresh the file the
    # user edits and commit it, so git HEAD (the baseline we diff against
    # next time) advances the way a `git commit` advances the index.
    new_config = interface.get_running_config()
    with open(project.EDIT_FILE, 'w') as file:
        file.write(new_config)

    edit_file_name = os.path.relpath(project.EDIT_FILE, project.PROJECT_DIR)
    git_ops.commit(project.PROJECT_DIR, [edit_file_name], f'c2sync sync: pushed to {project.SERIAL_DEVICE}')

    interface.disconnect()
    print('\nSynced. Device has pending changes not yet saved to startup-config (run `c2sync commit`).')


def commit(arguments: list):
    LOGGER.debug(f'Given arguments: {arguments}')
    force = '-y' in arguments

    project = _require_project()
    state_engine = StateEngine(project)
    state = state_engine.state

    if state.host_dirty:
        print('You have unsynced local edits. Run `c2sync sync` before committing.')
        sys.exit(1)

    if not state.device_dirty:
        print('Nothing to commit, running config already matches startup config.')
        return

    if not force and not _confirm('Save running config to startup config?'):
        print('Aborted.')
        return

    interface = _connect(project)

    try:
        interface.save_config()
    except ConfigSaveError as e:
        print(f'\nFailed to save configuration on the device:\n{e}')
        interface.disconnect()
        sys.exit(1)

    state_engine.mark_device_clean()
    git_ops.commit_empty(project.PROJECT_DIR, f'c2sync commit: saved to startup-config on {project.SERIAL_DEVICE}')
    interface.disconnect()
    print('\nSaved. Device is now synced.')


def discard(arguments: list):
    LOGGER.debug(f'Given arguments: {arguments}')

    project = _require_project()

    # Revert the edit file itself, not just the staging file - otherwise
    # the discarded edits would just get re-staged the next time status/sync
    # recomputes the diff against the baseline.
    edit_file_name = os.path.relpath(project.EDIT_FILE, project.PROJECT_DIR)
    baseline = git_ops.show_at_head(project.PROJECT_DIR, edit_file_name) or ''
    with open(project.EDIT_FILE, 'w') as file:
        file.write(baseline)

    Differ(project).clear_staging()
    StateEngine(project).mark_host_clean()
    print('Discarded staged changes.')


def _require_project() -> Project:
    project = get_project()
    if project is None:
        print('No C2Sync project found in this directory. Run `c2sync init SERIAL_DEVICE` first.')
        sys.exit(1)
    return project


def _refresh_staging(project: Project) -> list[str]:
    """
    Recompute staging on demand from the baseline vs. the current edit
    file, update host_dirty accordingly, and return the staged lines.
    """
    staged = Differ(project).refresh_staging_from_files()

    state_engine = StateEngine(project)
    if staged:
        state_engine.mark_host_dirty()
    else:
        state_engine.mark_host_clean()

    with open(project.STAGING_FILE) as file:
        return [line.rstrip('\n') for line in file if line.strip()]


def _confirm(prompt: str) -> bool:
    return input(f'{prompt} [y/N] ').strip().lower() == 'y'


def _connect(project: Project) -> SerialInterface:
    # Username is not a secret, so it can also come from the user's global
    # config (~/.config/c2sync/config.toml) - password/secret never do, only
    # the env vars below or an interactive prompt.
    config = user_config.load()
    username = os.environ.get('C2SYNC_USERNAME') or config.get('username')
    password = os.environ.get('C2SYNC_PASSWORD')

    # CI/non-interactive path: only skip prompting if both are already
    # resolved, so a partially-set environment falls back to fully
    # interactive rather than half-prompting (and potentially hanging on
    # stdin in CI).
    if username is not None and password is not None:
        secret = os.environ.get('C2SYNC_SECRET') or None
    else:
        username = username or input('Username: ')
        password = password or getpass.getpass('Password: ')
        secret = getpass.getpass('Enable secret (leave blank if none): ') or None

    try:
        interface = SerialInterface(project, username=username, password=password, secret=secret)
        interface.initialize_session()
        return interface
    except (NetmikoAuthenticationException, NetmikoTimeoutException) as e:
        print(f'Could not connect to device: {e}')
        sys.exit(1)
