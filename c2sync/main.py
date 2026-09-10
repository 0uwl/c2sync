import getpass
import logging
import os
import sys

from contextlib import contextmanager

from netmiko.exceptions import NetmikoAuthenticationException, NetmikoTimeoutException

from c2sync import Project, get_project, git_ops, init_project, user_config
from c2sync.connector import DeviceInterface
from c2sync.differ import Differ
from c2sync.exceptions import ConfigApplyError, ConfigSaveError, HostKeyRejectedError
from c2sync.state_engine import StateEngine

LOGGER = logging.getLogger(__name__)

USAGE = """
Usage:
c2sync COMMAND

Commands:
    init         Start a C2Sync session in the current working directory
    pull         Fetch the device's running config and make it the new baseline
                 [--force|-f]  required to overwrite unsynced local edits
    status       Show whether the local config file has unsynced edits
    sync         Preview changes and confirm or abort them
    commit       Issues the command to save the running config to the startup config on the device
    discard      Cancel the current C2Sync session
    revert       Push the device's running config back to a past commit (HEAD by default)
                 [COMMIT] [-y] [--force|-f]  -y skips the push confirmation; --force/-f
                 is required to overwrite unsynced local edits
    help         Show this message (also -h, --help)
"""

# 'help' is only a command; -h/--help are also honoured as arguments to a
# command, so `c2sync init --help` prints usage instead of starting a project
# for a device literally named '--help'.
HELP_COMMANDS = ('help', '-h', '--help')
HELP_FLAGS = ('-h', '--help')

def main():
    arguments = sys.argv[1:]

    if not arguments:
        print(USAGE)
        return

    command = arguments[0]
    command_arguments = arguments[1:]

    if command in HELP_COMMANDS or any(a in HELP_FLAGS for a in command_arguments):
        print(USAGE)
        return

    match command:
        case 'init':
            init(command_arguments)
        case 'pull':
            pull(command_arguments)
        case 'status':
            status(command_arguments)
        case 'sync':
            sync(command_arguments)
        case 'commit':
            commit(command_arguments)
        case 'discard':
            discard(command_arguments)
        case 'revert':
            revert(command_arguments)
        case _:
            LOGGER.error(f'Unknown command {command}')
            print(USAGE, file=sys.stderr)
            sys.exit(1)


def init(arguments: list):
    LOGGER.debug(f'Given arguments: {arguments}')

    if not arguments:
        print('Usage: c2sync init SERIAL_DEVICE [BAUDRATE]')
        print('       c2sync init --ssh HOST [PORT]')
        sys.exit(1)

    config = user_config.load()

    if arguments[0] == '--ssh':
        if len(arguments) < 2:
            print('Usage: c2sync init --ssh HOST [PORT]')
            sys.exit(1)
        port = int(arguments[2]) if len(arguments) > 2 else config.get('ssh_port', 22)
        project_kwargs = {'TRANSPORT': 'ssh', 'HOST': arguments[1], 'SSH_PORT': port}
    else:
        baudrate = int(arguments[1]) if len(arguments) > 1 else config.get('baudrate', 9600)
        project_kwargs = {'TRANSPORT': 'serial', 'SERIAL_DEVICE': arguments[0], 'BAUDRATE': baudrate}

    if 'timeout' in config:
        project_kwargs['TIMEOUT'] = config['timeout']
    if 'prompt_regex' in config:
        project_kwargs['PROMPT_REGEX'] = config['prompt_regex']

    init_project(Project(**project_kwargs))


def pull(arguments: list):
    """
    Fetch the device's actual running config and commit it as the new
    baseline - this is how an already-configured device gets onboarded
    (init alone only creates an empty device.config), and how the local
    baseline can be resynced if the device changed out-of-band.
    """
    LOGGER.debug(f'Given arguments: {arguments}')
    # pull has no other prompt to skip, so unlike sync/commit/revert there's
    # no separate -y - --force/-f is the only flag, matching revert's split
    # (a plain "skip prompts" flag must never be the same thing as "yes,
    # overwrite my local edits").
    force = '--force' in arguments or '-f' in arguments

    project = _require_project()

    state = StateEngine(project).state
    if state.host_dirty and not force:
        print('You have unsynced local edits that would be overwritten. Run '
              '`c2sync discard` first, or `c2sync pull --force` (or `-f`) to overwrite them anyway.')
        sys.exit(1)

    with _connected(project) as interface:
        new_config = interface.get_running_config()

    with open(project.EDIT_FILE, 'w') as file:
        file.write(new_config)

    git_ops.commit(project.PROJECT_DIR, [project.edit_file_relpath], f'c2sync pull: fetched from {project.target}')

    Differ(project).clear_staging()
    StateEngine(project).mark_host_clean()
    print('Pulled running config from device.')


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

    with _connected(project) as interface:
        try:
            interface.apply_config(lines)
        except ConfigApplyError as e:
            print(f'\nDevice rejected the configuration, nothing was applied:\n{e}')
            sys.exit(1)

        # The push is Netmiko-confirmed at this point: clear what's staged
        # and record that the device now has unsaved (running-config-only)
        # changes.
        Differ(project).clear_staging()
        state_engine.mark_host_clean()
        state_engine.mark_device_dirty()

        # The device is now the source of truth again - refresh the file
        # the user edits and commit it, so git HEAD (the baseline we diff
        # against next time) advances the way a `git commit` advances the
        # index.
        new_config = interface.get_running_config()

    with open(project.EDIT_FILE, 'w') as file:
        file.write(new_config)

    git_ops.commit(project.PROJECT_DIR, [project.edit_file_relpath], f'c2sync sync: pushed to {project.target}')

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

    with _connected(project) as interface:
        try:
            interface.save_config()
        except ConfigSaveError as e:
            print(f'\nFailed to save configuration on the device:\n{e}')
            sys.exit(1)

    state_engine.mark_device_clean()
    git_ops.commit_empty(project.PROJECT_DIR, f'c2sync commit: saved to startup-config on {project.target}')
    print('\nSaved. Device is now synced.')


def discard(arguments: list):
    LOGGER.debug(f'Given arguments: {arguments}')

    project = _require_project()

    # Revert the edit file itself, not just the staging file - otherwise
    # the discarded edits would just get re-staged the next time status/sync
    # recomputes the diff against the baseline.
    baseline = git_ops.show_at_head(project.PROJECT_DIR, project.edit_file_relpath) or ''
    with open(project.EDIT_FILE, 'w') as file:
        file.write(baseline)

    Differ(project).clear_staging()
    StateEngine(project).mark_host_clean()
    print('Discarded staged changes.')


def revert(arguments: list):
    """
    Push the device's running config back to what it looked like at a past
    commit (HEAD - the last confirmed sync - by default), for recovering
    from a bad push (e.g. one command in a batch got rejected after others
    already landed) without hand-crafting the fix.

    Diffed against a config fetched fresh from the device right now, not
    EDIT_FILE - after something's gone wrong, neither EDIT_FILE nor the git
    baseline is guaranteed to reflect what's actually running.

    Like `git revert`, not `git reset --hard`: this makes a *new* commit
    recording the recovered state rather than rewinding HEAD, so the
    incident stays visible in `git log` instead of being erased.
    """
    LOGGER.debug(f'Given arguments: {arguments}')
    skip_confirm = '-y' in arguments
    # Deliberately separate from -y: -y should only ever mean "skip the
    # push-preview prompt", never "also silently overwrite local edits I
    # didn't know I had" - a user reaching for -y just to move past the
    # confirmation shouldn't be able to lose work by accident.
    force_overwrite = '--force' in arguments or '-f' in arguments
    positional = [arg for arg in arguments if arg not in ('-y', '--force', '-f')]
    target_rev = positional[0] if positional else 'HEAD'

    project = _require_project()

    state = StateEngine(project).state
    if state.host_dirty and not force_overwrite:
        print('You have unsynced local edits that would be overwritten by revert. Run '
              '`c2sync discard` first, or `c2sync revert --force` (or `-f`) to overwrite them anyway.')
        sys.exit(1)

    try:
        resolved_sha = git_ops.resolve_rev(project.PROJECT_DIR, target_rev)
    except git_ops.GitError as e:
        print(f"Could not resolve '{target_rev}': {e}")
        sys.exit(1)

    try:
        target_config = git_ops.show_at(project.PROJECT_DIR, resolved_sha, project.edit_file_relpath)
    except git_ops.GitError as e:
        print(f"'{project.edit_file_relpath}' is not tracked at {resolved_sha[:8]}: {e}")
        sys.exit(1)

    with _connected(project) as interface:
        live_config = interface.get_running_config()

        lines = Differ.diff_lines(live_config, target_config)

        if not lines:
            print(f'Running config already matches {resolved_sha[:8]}. Nothing to revert.')
            return

        print(f'Reverting running config to {resolved_sha[:8]} - the following commands will be sent to the device:\n')
        for line in lines:
            print(f'  {line}')

        if not skip_confirm and not _confirm('\nProceed?'):
            print('Aborted.')
            return

        try:
            interface.apply_config(lines)
        except ConfigApplyError as e:
            print(f'\nDevice rejected the revert - it may now be in a partially-applied '
                  f'state, check `c2sync status` and the device directly:\n{e}')
            sys.exit(1)

        # The revert push is Netmiko-confirmed at this point. Re-fetch
        # rather than assuming the device now matches target_config
        # verbatim - same reasoning `sync` already follows after its own
        # push.
        new_config = interface.get_running_config()

    with open(project.EDIT_FILE, 'w') as file:
        file.write(new_config)

    git_ops.commit(
        project.PROJECT_DIR, [project.edit_file_relpath],
        f'c2sync revert: reverted {project.target} to {resolved_sha[:8]}'
    )

    # Whatever was staged before is now stale - EDIT_FILE just got
    # overwritten with the post-revert device state. host_dirty/device_dirty
    # follow the same transitions as a successful sync: local edits are (no
    # longer) a concept here, and running-config now differs from
    # startup-config again until `c2sync commit`.
    Differ(project).clear_staging()
    state_engine = StateEngine(project)
    state_engine.mark_host_clean()
    state_engine.mark_device_dirty()

    print('\nReverted. Device has pending changes not yet saved to startup-config (run `c2sync commit`).')


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


@contextmanager
def _connected(project: Project):
    """
    Connect and guarantee disconnect() runs even if the caller's block
    raises (a rejected push, a dropped session mid-fetch, ...) or returns
    early - `finally` inside a generator-based context manager still runs
    on any of those, SystemExit included, so callers no longer need to
    scatter interface.disconnect() before every exit point themselves.
    """
    interface = _connect(project)
    try:
        yield interface
    finally:
        interface.disconnect()


def _connect(project: Project) -> DeviceInterface:
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

    # Off by default: prompting to trust a never-seen host key is a
    # convenience, but it's a materially different (weaker) trust model
    # than "verify against what's already known" - see connector.py.
    prompt_for_unknown_hosts = bool(config.get('prompt_for_unknown_ssh_hosts', False))

    try:
        interface = DeviceInterface(
            project,
            username=username,
            password=password,
            secret=secret,
            prompt_for_unknown_hosts=prompt_for_unknown_hosts,
        )
        interface.initialize_session()
        return interface
    except (NetmikoAuthenticationException, NetmikoTimeoutException, HostKeyRejectedError) as e:
        print(f'Could not connect to device: {e}')
        sys.exit(1)
