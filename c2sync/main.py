import getpass
import logging
import sys

from netmiko.exceptions import NetmikoAuthenticationException, NetmikoTimeoutException

from c2sync import Project, get_project, init_project
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

    serial_device = arguments[0]
    baudrate = int(arguments[1]) if len(arguments) > 1 else 9600

    init_project(Project(SERIAL_DEVICE=serial_device, BAUDRATE=baudrate))


def sync(arguments: list):
    LOGGER.debug(f'Given arguments: {arguments}')
    force = '-y' in arguments

    project = _require_project()

    with open(project.STAGING_FILE) as file:
        lines = [line.rstrip('\n') for line in file if line.strip()]

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

    new_config = interface.get_running_config()
    with open(project.EDIT_FILE, 'w') as file:
        file.write(new_config)

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
    interface.disconnect()
    print('\nSaved. Device is now synced.')


def discard(arguments: list):
    LOGGER.debug(f'Given arguments: {arguments}')

    project = _require_project()

    Differ(project).clear_staging()
    StateEngine(project).mark_host_clean()
    print('Discarded staged changes.')


def _require_project() -> Project:
    project = get_project()
    if project is None:
        print('No C2Sync project found in this directory. Run `c2sync init SERIAL_DEVICE` first.')
        sys.exit(1)
    return project


def _confirm(prompt: str) -> bool:
    return input(f'{prompt} [y/N] ').strip().lower() == 'y'


def _connect(project: Project) -> SerialInterface:
    username = input('Username: ')
    password = getpass.getpass('Password: ')
    secret = getpass.getpass('Enable secret (leave blank if none): ') or None

    try:
        interface = SerialInterface(project, username=username, password=password, secret=secret)
        interface.initialize_session()
        return interface
    except (NetmikoAuthenticationException, NetmikoTimeoutException) as e:
        print(f'Could not connect to device: {e}')
        sys.exit(1)
