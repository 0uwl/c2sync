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

# One line per command, no flags: arguments and flags live in COMMAND_HELP
# below, so there is exactly one place to edit when they change.
USAGE = """
Usage:
c2sync COMMAND [ARGS]

Commands:
    init      Start a project for one device in the current directory
    pull      Fetch the device's running config and make it the new baseline
    status    Show unsynced local edits and unsaved device changes
    sync      Preview the staged commands and push them to the device
    commit    Save the device's running config to its startup config
    discard   Throw away local edits and return to the last confirmed sync
    revert    Push the device's running config back to a past commit
    help      Show this message (also -h, --help)

Run `c2sync COMMAND --help` for detail on a single command.
"""

# 'help' is only a command; -h/--help are also honoured as arguments to a
# command, so `c2sync init --help` prints that command's help instead of
# starting a project for a device literally named '--help'.
HELP_COMMANDS = ('help', '-h', '--help')
HELP_FLAGS = ('-h', '--help')

COMMAND_HELP = {
    'init': """
Usage: c2sync init SERIAL_DEVICE [BAUDRATE]
       c2sync init --ssh HOST [PORT]

Start a C2Sync project for one device in the current directory.

Creates ./.c2sync/, initializes a git repository there, and makes the first
commit (an empty device.config). Run `c2sync pull` next to onboard a device
that already has a configuration.

Arguments:
  SERIAL_DEVICE  Serial port to use, e.g. /dev/ttyUSB0
  BAUDRATE       Serial baud rate (default: 9600, or `baudrate` from the
                 global config file)
  HOST           Hostname or address to reach over SSH
  PORT           SSH port (default: 22, or `ssh_port` from the global config)

SSH host keys are verified against ~/.ssh/known_hosts
""",

    'pull': """
Usage: c2sync pull [--force|-f]

Fetch the device's running config and commit it as the new baseline.

This is how an already-configured device gets onboarded, since `init` alone
only creates an empty device.config. It also resyncs the baseline when the
device was changed outside of C2Sync.

Options:
  --force, -f  Overwrite unsynced local edits. Without it, pull refuses to
               run while you have local edits that would be lost.
""",

    'status': """
Usage: c2sync status

Show whether there are unsynced local edits or unsaved device changes.

Recomputes the staged commands from device.config against the last confirmed
sync (device.config at git HEAD), then prints the device state and a preview
of anything staged. Read-only, and never connects to the device.
""",

    'sync': """
Usage: c2sync sync [-y] [--rollback-on-error]

Preview the staged commands and push them to the device.

Shows the exact CLI commands that will be sent and asks for confirmation.
Once the device confirms the push, C2Sync re-fetches the running config,
writes it to device.config, and commits it, advancing the baseline.

Options:
  -y                    Skip the confirmation prompt.
  --rollback-on-error   If the device rejects a command after earlier ones
                        already applied, offer to undo them by pushing the
                        device back to the pre-sync baseline.

A command the device rejects aborts the batch, and the commands before it
stay on the device. C2Sync always re-reads the running config at that point
and moves the baseline to match, so `status` shows only what is still
outstanding and your edits are left alone -- it does not undo the push.

--rollback-on-error is opt-in because undoing means sending more config to
a device that just rejected some, and a negation is not always a safe
inverse. It previews and asks first unless -y is also given. `c2sync revert` 
is the same recovery driven by hand, and stays available either way.
""",

    'commit': """
Usage: c2sync commit [-y]

Save the device's running config to its startup config.

Recorded as an empty git commit, since there is no file change to stage for
this milestone.

Options:
  -y  Skip the confirmation prompt.

Refuses to run while you have unsynced local edits, which would save state
you did not intend, and does nothing when the running config is already
saved.
""",

    'discard': """
Usage: c2sync discard

Throw away local edits and return to the last confirmed sync.

Reverts device.config to its content at git HEAD and clears staging, so the
discarded edits cannot quietly be re-staged on the next status or sync. Does
not touch the device.
""",

    'revert': """
Usage: c2sync revert [COMMIT] [-y] [--force|-f]

Push the device's running config back to a past commit.

The recovery path for a bad push, e.g. a batch where one command was rejected
after others had already landed. Unlike every other command, the diff is taken
against a config fetched fresh from the device right now: after something has
gone wrong, neither device.config nor the git baseline is guaranteed to match
what is actually running.

Arguments:
  COMMIT  Commit to restore (default: HEAD, the last confirmed sync)

Options:
  -y           Skip the push confirmation prompt.
  --force, -f  Overwrite unsynced local edits.

-y and --force are independent on purpose: -y skips only the preview prompt,
and --force is the only thing that permits discarding local edits.

Modeled on `git revert`, it makes a new commit recording the recovered state
so the incident stays visible in the log.
""",
}


def _print_help(command=None) -> None:
    """
    Print help for a single command, falling back to the top-level usage for
    a missing or unrecognized one so `c2sync help bogus` still says something
    useful rather than erroring.
    """
    text = COMMAND_HELP.get(command)
    print(text.strip('\n') if text else USAGE)

def main():
    arguments = sys.argv[1:]

    if not arguments:
        print(USAGE)
        return

    command = arguments[0]
    command_arguments = arguments[1:]

    # `c2sync help COMMAND` / `c2sync --help COMMAND`, and bare `c2sync help`.
    if command in HELP_COMMANDS:
        _print_help(command_arguments[0] if command_arguments else None)
        return

    # `c2sync COMMAND --help`. Checked across all of the command's arguments so
    # the flag is caught wherever it lands, including in a position the command
    # would otherwise read as a value (`c2sync init --help` must not start a
    # project for a device named '--help').
    if any(a in HELP_FLAGS for a in command_arguments):
        _print_help(command)
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
        # label only ever shows one of the two flags (host_dirty wins), but
        # both being set is the normal state after a partial push - so say
        # so here rather than leaving the unsaved device changes invisible
        # behind the staged-command list.
        if state.device_dirty:
            print('\nThe device also has running-config changes not yet saved to '
                  'startup-config (run `c2sync commit`).')
    elif state.device_dirty:
        print('Running config has not been saved to startup-config yet (run `c2sync commit`).')
    else:
        print('Nothing staged.')


def sync(arguments: list):
    LOGGER.debug(f'Given arguments: {arguments}')
    force = '-y' in arguments
    rollback_on_error = '--rollback-on-error' in arguments

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

    # Captured before anything is pushed: if the push aborts partway and
    # --rollback-on-error is set, this is the state to put the device back
    # to. Reconciliation advances HEAD, so it can't be looked up as 'HEAD'
    # after the fact.
    try:
        pre_sync_head = git_ops.resolve_rev(project.PROJECT_DIR, 'HEAD')
    except git_ops.GitError:
        pre_sync_head = None

    with _connected(project) as interface:
        try:
            interface.apply_config(lines)
        except ConfigApplyError as e:
            landed = _reconcile_after_failed_push(project, interface, state_engine, e)
            if landed and rollback_on_error:
                _rollback_after_failed_push(project, interface, state_engine, pre_sync_head, force)
            # Recompute staging against the baseline reconciliation just
            # moved, so the state left behind is honest about what is still
            # outstanding.
            _refresh_staging(project)
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


def _reconcile_after_failed_push(project: Project, interface, state_engine: StateEngine, error) -> bool:
    """
    Record what the device *actually* has after a push aborted partway.

    apply_config aborts the batch on the first rejected command, but the
    commands before it are already running on the device. Without this the
    baseline still describes the pre-push device, so `status` reports
    everything - landed and unlanded alike - as unsynced local edits.

    Re-fetches the running config and commits it as the new baseline,
    deliberately leaving EDIT_FILE alone: the user's unpushed edits are
    still what they want, and diffing them against the corrected baseline
    is exactly the set of commands that did not make it. Reading the device
    is also strictly more reliable than parsing Netmiko's exception to
    guess how many commands landed.

    Returns True if anything had already landed.
    """
    print(f'\nDevice rejected a command, so the push stopped there:\n{error}')

    try:
        live_config = interface.get_running_config()
    except Exception as fetch_error:
        # Reconciliation is best-effort: losing the session here must not
        # mask the original rejection, but the local state is now known to
        # be untrustworthy and the user has to be told plainly.
        print(f'\nCould not re-read the running config to see what landed: {fetch_error}\n'
              f'Local state may not match the device - check the device directly, '
              f'then run `c2sync pull --force` to resync the baseline.')
        return False

    landed = git_ops.commit_content(
        project.PROJECT_DIR, project.edit_file_relpath, live_config,
        f'c2sync sync: partial push to {project.target} (device rejected a command)',
    )

    if landed:
        # Those commands are in running-config and not in startup-config,
        # which is exactly what device_dirty means.
        state_engine.mark_device_dirty()
        print('\nCommands before the rejected one are already on the device. The baseline now\n'
              'records what the device actually has, so `c2sync status` lists only what is\n'
              'still outstanding. Your edits in the config file were left untouched.')
    else:
        print('\nNothing landed on the device - the rejected command was the first one.')

    return landed


def _rollback_after_failed_push(
    project: Project, interface, state_engine: StateEngine, to_rev: str, skip_confirm: bool,
) -> bool:
    """
    Opt-in (`sync --rollback-on-error`) undo of a partial push: diff the
    live device against the baseline the sync started from and push the
    correction, the same thing `c2sync revert` does by hand.

    Off by default on purpose. Undoing a partial push means sending *more*
    config to a device that just rejected some, and a negation is not
    always a safe inverse - undoing an address or interface change can cut
    the session doing the undoing. So it still previews and asks unless -y.
    """
    if to_rev is None:
        print('\nCannot roll back: no commit to roll back to.')
        return False

    # Broad on purpose: this covers both git_ops.GitError and whatever
    # Netmiko raises on a session that died with the failed push. Either
    # way the fallback is the same - tell the user to drive `revert`.
    try:
        target_config = git_ops.show_at(project.PROJECT_DIR, to_rev, project.edit_file_relpath)
        live_config = interface.get_running_config()
    except Exception as e:
        print(f'\nCould not work out a rollback: {e}\n'
              f'Run `c2sync revert {to_rev[:8] if to_rev else ""}` once the device is reachable.')
        return False

    lines = Differ.diff_lines(live_config, target_config)
    if not lines:
        print('\nNothing to roll back - the device already matches the pre-sync baseline.')
        return False

    print(f'\nRolling back to {to_rev[:8]} - the following commands will be sent to the device:\n')
    for line in lines:
        print(f'  {line}')

    if not skip_confirm and not _confirm('\nProceed with rollback?'):
        print('Rollback aborted - the device is left in its partially-applied state.')
        return False

    try:
        interface.apply_config(lines)
    except ConfigApplyError as e:
        print(f'\nThe rollback was itself rejected - the device is in a partially-reverted\n'
              f'state and needs looking at directly:\n{e}')
        return False

    # Re-read rather than assuming the device now matches target_config
    # verbatim, the same way sync and revert do after their own pushes.
    rolled_back_config = interface.get_running_config()
    git_ops.commit_content(
        project.PROJECT_DIR, project.edit_file_relpath, rolled_back_config,
        f'c2sync sync: rolled back partial push to {project.target}',
    )
    # running-config was written twice (partial push, then the undo), so it
    # is not safe to claim it matches startup-config again.
    state_engine.mark_device_dirty()
    print('\nRolled back. Your edits in the config file were left untouched - fix the\n'
          'rejected command and run `c2sync sync` again.')
    return True


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
