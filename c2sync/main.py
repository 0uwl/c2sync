import getpass
import logging
import os
import sys

from contextlib import contextmanager

from netmiko.exceptions import NetmikoAuthenticationException, NetmikoTimeoutException

from c2sync import Project, get_project, git_ops, init_project, user_config
from c2sync.connector import DeviceInterface
from c2sync.differ import Differ
from c2sync.exceptions import ConfigApplyError, ConfigSaveError, HostKeyRejectedError, ProjectExistsError
from c2sync.state_engine import StateEngine

LOGGER = logging.getLogger(__name__)

# One line per command, no flags: arguments and flags live in COMMAND_HELP
# below, so there is exactly one place to edit when they change.
USAGE = """
Usage:
c2sync COMMAND [ARGS]

Commands:
    init      Start a project for one device
    pull      Fetch the device's running config and make it the new baseline
    fetch     Check the device for drift without changing any local state
    status    Show unpushed local edits and unsaved device changes
    push      Preview the staged commands and push them to the device
    save      Save the device's running config to its startup config
    discard   Throw away local edits and return to the last confirmed push
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
Usage: c2sync init NAME SERIAL_DEVICE [BAUDRATE] [--dir PATH] [--pull]
       c2sync init NAME --ssh HOST [PORT] [--dir PATH] [--pull]

Start a C2Sync project for one device.

Creates ./NAME/ (or PATH, if --dir is given) holding device.config - the
file you edit - and c2sync.toml - the device's connection info - plus a real
git repository (initialized there, with the first commit covering both,
alongside .gitignore). Both files are tracked in git, so a `git clone` of
this project is enough on its own to keep working with; the only thing that
stays local and untracked is a NAME/.c2sync/ subfolder holding staging.txt
and state.json - operational scratch nobody needs to open, silently
recreated if missing (e.g. right after a clone). `cd` into the project
directory before running any other command - they all expect cwd to already
be inside it, the same way `git status` expects to be run from inside the
repository.

Arguments:
  NAME           Project name - also the default directory (./NAME), unless
                  --dir overrides it. Must not contain '/'.
  SERIAL_DEVICE  Serial port to use, e.g. /dev/ttyUSB0
  BAUDRATE       Serial baud rate (default: 9600, or `baudrate` from the
                 global config file)
  HOST           Hostname or address to reach over SSH
  PORT           SSH port (default: 22, or `ssh_port` from the global config)

Options:
  --dir PATH  Create the project at PATH instead of ./NAME. NAME is still
              required - it's the project's identifier either way, not just
              a directory name.
  --pull      Immediately fetch the device's current running config as the
              baseline, same as running `c2sync pull` right after - for
              onboarding a device that's already configured, in one step
              instead of two (mirrors `git clone` doing a fetch for you).
              Without it, `init` alone just creates an empty device.config;
              run `c2sync pull` separately when you're ready to connect.

Refuses to run if the target directory already holds a c2sync project,
rather than overwriting its device.config/c2sync.toml.

SSH host keys are verified against ~/.ssh/known_hosts
""",

    'pull': """
Usage: c2sync pull [--force|-f]

Fetch the device's running config and commit it as the new baseline.

This is how an already-configured device gets onboarded, since `init` alone
only creates an empty device.config (or use `c2sync init --pull` to do both
in one step). It also resyncs the baseline when the device was changed
outside of C2Sync. Run `c2sync fetch` first if you just want to see what
changed without committing to it.

Options:
  --force, -f  Overwrite unpushed local edits. Without it, pull refuses to
               run while you have local edits that would be lost.
""",

    'fetch': """
Usage: c2sync fetch

Check the device for drift without changing any local state.

Connects, reads the live running config, and reports how it differs from
the baseline (device.config at git HEAD) - device.config, staging, and
state.json are all left untouched either way. This is the read-only half of
what `push` already does as a pre-flight before adopting drift and sending
commands; `fetch` is the same check available on demand, mirroring git's own
fetch (look only) vs. pull (fetch and merge) split.

Closes a gap `status` cannot: status is deliberately offline (it only
compares device.config against your local edits), so it can report the
device as synced even when it has actually drifted out-of-band. Run
`c2sync pull --force` to take the device as-is, or `c2sync push --rebase` to
adopt the drift as the new baseline and push your local edits on top of it.
""",

    'status': """
Usage: c2sync status

Show whether there are unpushed local edits or unsaved device changes.

Recomputes the staged commands from device.config against the last confirmed
push (device.config at git HEAD), then prints the device state and a preview
of anything staged. Read-only, and never connects to the device.
""",

    'push': """
Usage: c2sync push [-y] [--rebase] [--rollback-on-error]

Preview the staged commands and push them to the device.

Reads the device's running config first and checks it still matches the
baseline the staged commands were computed against, then shows the exact
CLI commands that will be sent and asks for confirmation. Once the device
confirms the push, C2Sync re-fetches the running config, writes it to
device.config, and commits it, advancing the baseline.

Options:
  -y                    Skip the confirmation prompt.
  --rebase              Adopt out-of-band device changes as the new
                        baseline without asking, then replay your staged
                        commands on top of it.
  --rollback-on-error   If the device rejects a command after earlier ones
                        already applied, offer to undo them by pushing the
                        device back to the pre-push baseline.

If the device was changed outside C2Sync since the last push, the staged
commands describe a device that no longer exists -- a line you deleted
locally still becomes `no <that line>` and can destroy someone else's
replacement for it, with nothing in the preview hinting at it. So push
checks first and stops, showing what changed on the device (run `c2sync
fetch` any time to see this without pushing). Accepting adopts the device's
current config as the new baseline (your edits in device.config are left
alone) and recomputes, so the preview you approve is the truth -- this is
what --rebase names: your edits are replayed on top of the device's moved-on
state, not overwritten by it. The recomputed commands will include undoing
those out-of-band changes, since your file does not contain them -- that is
the point: it happens either way, and this is the version where you see it
first.

-y does not stand in for that decision, and never prompts for it: with -y
and no --rebase, drift is a hard failure, so a CI job stops instead of
pushing against a stale baseline.

A command the device rejects aborts the batch, and the commands before it
stay on the device. C2Sync always re-reads the running config at that point
and moves the baseline to match, so `status` shows only what is still
outstanding and your edits are left alone -- it does not undo the push.

--rollback-on-error is opt-in because undoing means sending more config to
a device that just rejected some, and a negation is not always a safe
inverse. It previews and asks first unless -y is also given. `c2sync revert` 
is the same recovery driven by hand, and stays available either way.
""",

    'save': """
Usage: c2sync save [-y]

Save the device's running config to its startup config (`write memory`), so
it survives a reload.

Recorded as an empty git commit, since there is no file change to stage for
this milestone.

Options:
  -y  Skip the confirmation prompt.

Refuses to run while you have unpushed local edits, which would save state
you did not intend, and does nothing when the running config is already
saved.
""",

    'discard': """
Usage: c2sync discard

Throw away local edits and return to the last confirmed push.

Reverts device.config to its content at git HEAD and clears staging, so the
discarded edits cannot quietly be re-staged on the next status or push. Does
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
  COMMIT  Commit to restore (default: HEAD, the last confirmed push)

Options:
  -y           Skip the push confirmation prompt.
  --force, -f  Overwrite unpushed local edits.

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
        case 'fetch':
            fetch(command_arguments)
        case 'status':
            status(command_arguments)
        case 'push':
            push(command_arguments)
        case 'save':
            save(command_arguments)
        case 'discard':
            discard(command_arguments)
        case 'revert':
            revert(command_arguments)
        case _:
            LOGGER.error(f'Unknown command {command}')
            print(USAGE, file=sys.stderr)
            sys.exit(1)


_INIT_USAGE = (
    'Usage: c2sync init NAME SERIAL_DEVICE [BAUDRATE] [--dir PATH] [--pull]\n'
    '       c2sync init NAME --ssh HOST [PORT] [--dir PATH] [--pull]'
)


def init(arguments: list):
    LOGGER.debug(f'Given arguments: {arguments}')

    # --dir PATH can land anywhere after NAME, so pull it out first rather
    # than accounting for it shifting the positional arguments that follow.
    positional = list(arguments)
    project_dir_override = None
    if '--dir' in positional:
        idx = positional.index('--dir')
        try:
            project_dir_override = positional[idx + 1]
        except IndexError:
            print(f'{_INIT_USAGE}\n\n--dir requires a path')
            sys.exit(1)
        del positional[idx:idx + 2]

    # Mirrors `git clone`: onboard an already-configured device in one step
    # instead of `init` then a separate `pull`. Pulled out before the
    # remaining positional parsing below for the same reason --dir is.
    do_pull = '--pull' in positional
    if do_pull:
        positional.remove('--pull')

    if not positional:
        print(_INIT_USAGE)
        sys.exit(1)

    name = positional[0]
    # A NAME containing a path separator would otherwise silently nest
    # directories (or, with '..', escape the intended location) when it
    # doubles as the default directory name below.
    if os.sep in name or name in ('.', '..'):
        print(f"Invalid project name {name!r}: must not contain {os.sep!r} or be '.'/'..'")
        sys.exit(1)

    rest = positional[1:]
    if not rest:
        print(_INIT_USAGE)
        sys.exit(1)

    config = user_config.load()

    if rest[0] == '--ssh':
        if len(rest) < 2:
            print(_INIT_USAGE)
            sys.exit(1)
        port = int(rest[2]) if len(rest) > 2 else config.get('ssh_port', 22)
        project_kwargs = {'TRANSPORT': 'ssh', 'HOST': rest[1], 'SSH_PORT': port}
    else:
        baudrate = int(rest[1]) if len(rest) > 1 else config.get('baudrate', 9600)
        project_kwargs = {'TRANSPORT': 'serial', 'SERIAL_DEVICE': rest[0], 'BAUDRATE': baudrate}

    if 'timeout' in config:
        project_kwargs['TIMEOUT'] = config['timeout']
    if 'prompt_regex' in config:
        project_kwargs['PROMPT_REGEX'] = config['prompt_regex']

    project_dir = project_dir_override or os.path.join('.', name)
    project = Project.at(project_dir, NAME=name, **project_kwargs)

    try:
        init_project(project)
    except ProjectExistsError as e:
        print(str(e))
        sys.exit(1)

    if do_pull:
        # Reuses pull()'s own fetch-and-commit logic on the just-created
        # project - the device is already configured, so onboarding it is
        # `init` followed immediately by a `pull`, done here as one step
        # instead of two (mirrors `git clone` vs. `git init` + a manual
        # first fetch). A connection failure here must not look like `init`
        # itself failed - the project now exists either way.
        with _connected(project) as interface:
            _pull_running_config(project, interface)
        print('Pulled running config from device.')

    if project_dir != '.':
        print(f'Run `cd {project_dir}` to enter the project.')


def pull(arguments: list):
    """
    Fetch the device's actual running config and commit it as the new
    baseline - this is how an already-configured device gets onboarded
    (init alone only creates an empty device.config), and how the local
    baseline can be resynced if the device changed out-of-band.
    """
    LOGGER.debug(f'Given arguments: {arguments}')
    # pull has no other prompt to skip, so unlike push/save/revert there's
    # no separate -y - --force/-f is the only flag, matching revert's split
    # (a plain "skip prompts" flag must never be the same thing as "yes,
    # overwrite my local edits").
    force = '--force' in arguments or '-f' in arguments

    project = _require_project()

    state = StateEngine(project).state
    if state.host_dirty and not force:
        print('You have unpushed local edits that would be overwritten. Run '
              '`c2sync discard` first, or `c2sync pull --force` (or `-f`) to overwrite them anyway.')
        sys.exit(1)

    with _connected(project) as interface:
        _pull_running_config(project, interface)

    print('Pulled running config from device.')


def _pull_running_config(project: Project, interface) -> None:
    """
    The actual fetch-and-commit that both `pull` and `init --pull` need:
    read the running config, write it to EDIT_FILE, commit it as the new
    baseline, and clear any (stale, pre-pull) staged commands. Must run
    inside a caller's own `_connected()` block - `init --pull` reuses the
    same connection it authenticated for onboarding rather than opening a
    second one.
    """
    new_config = interface.get_running_config()

    with open(project.EDIT_FILE, 'w') as file:
        file.write(new_config)

    git_ops.commit(project.PROJECT_DIR, [project.edit_file_relpath], f'c2sync pull: fetched from {project.target}')

    Differ(project).clear_staging()
    StateEngine(project).mark_host_clean()


def fetch(arguments: list):
    """
    Read-only device-drift check: connect, read the live running config, and
    report how it differs from the baseline (device.config at git HEAD) -
    without touching device.config, staging, or state.json.

    Mirrors git's own fetch/pull split: `pull` is fetch-and-merge (it
    overwrites EDIT_FILE and advances the baseline), `fetch` is look-only.
    This closes a real gap `status` cannot: status is deliberately offline
    (see On-demand change detection), so it can report "synced" for a
    device that has actually drifted out-of-band. `push` already does this
    same read-and-diff as a pre-flight before adopting drift and sending
    commands; `fetch` is the same check available on demand, with nothing
    adopted and nothing sent.
    """
    LOGGER.debug(f'Given arguments: {arguments}')

    project = _require_project()

    with _connected(project) as interface:
        _, drift = _detect_drift(project, interface)

    if not drift:
        print('No drift: the device matches the local baseline (device.config at HEAD).')
        return

    print("The device has changed outside C2Sync since the last push/pull - here's what\n"
          'differs from the local baseline:\n')
    for line in drift:
        print(f'  {line}')
    print('\nNothing was changed locally or on the device. Run `c2sync pull --force` to take '
          'the device as-is (overwriting local edits), or `c2sync push --rebase` to adopt this '
          'as the new baseline and push your local edits on top of it.')


def status(arguments: list):
    LOGGER.debug(f'Given arguments: {arguments}')

    project = _require_project()
    lines = _refresh_staging(project)

    state = StateEngine(project).state
    print(f'Device state: {state.label}')

    if lines:
        print('\nStaged commands (run `c2sync push` to send them):\n')
        for line in lines:
            print(f'  {line}')
        # label only ever shows one of the two flags (host_dirty wins), but
        # both being set is the normal state after a partial push - so say
        # so here rather than leaving the unsaved device changes invisible
        # behind the staged-command list.
        if state.device_dirty:
            print('\nThe device also has running-config changes not yet saved to '
                  'startup-config (run `c2sync save`).')
    elif state.device_dirty:
        print('Running config has not been saved to startup-config yet (run `c2sync save`).')
    else:
        print('Nothing staged.')


def push(arguments: list):
    LOGGER.debug(f'Given arguments: {arguments}')
    assume_yes = '-y' in arguments
    # Deliberately not the same flag as -y: -y means "don't ask me to
    # confirm my own commands", never "silently overwrite changes someone
    # else made to the device". Named --rebase, not --force: what it does
    # is adopt the device's moved-on state as the new baseline and replay
    # the staged commands on top of it - a rebase, not a blind overwrite -
    # so that's the name that tells a git-literate operator what actually
    # happens. No short form, unlike pull/revert's --force/-f: this flag is
    # reached for rarely enough (only on genuine out-of-band drift) that a
    # short alias isn't worth the risk of it being typed reflexively.
    rebase = '--rebase' in arguments
    rollback_on_error = '--rollback-on-error' in arguments

    project = _require_project()
    lines = _refresh_staging(project)

    if not lines:
        print('Nothing staged to push.')
        return

    state_engine = StateEngine(project)

    with _connected(project) as interface:
        # The staged commands so far were computed offline, against the
        # baseline. Check that against the device before showing a preview
        # anyone is asked to approve - otherwise the preview describes a
        # device that may not exist any more. See the helper for why.
        lines = _reconcile_out_of_band_drift(project, interface, lines, assume_yes, rebase)

        if not lines:
            print('\nYour edits are already on the device - nothing left to push.')
            return

        print('The following commands will be sent to the device:\n')
        for line in lines:
            print(f'  {line}')

        if not assume_yes and not _confirm('\nProceed?'):
            print('Aborted.')
            return

        # Captured after any drift was adopted, so it names what the device
        # actually had immediately before this push - which is what a
        # rollback has to return it to. Reconciliation advances HEAD, so it
        # can't be looked up as 'HEAD' after the fact.
        try:
            pre_push_head = git_ops.resolve_rev(project.PROJECT_DIR, 'HEAD')
        except git_ops.GitError:
            pre_push_head = None

        try:
            interface.apply_config(lines)
        except ConfigApplyError as e:
            landed = _reconcile_after_failed_push(project, interface, state_engine, e)
            if landed and rollback_on_error:
                _rollback_after_failed_push(project, interface, state_engine, pre_push_head, assume_yes)
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

    git_ops.commit(project.PROJECT_DIR, [project.edit_file_relpath], f'c2sync push: pushed to {project.target}')

    print('\nPushed. Device has pending changes not yet saved to startup-config (run `c2sync save`).')


def save(arguments: list):
    LOGGER.debug(f'Given arguments: {arguments}')
    force = '-y' in arguments

    project = _require_project()
    state_engine = StateEngine(project)
    state = state_engine.state

    if state.host_dirty:
        print('You have unpushed local edits. Run `c2sync push` before saving.')
        sys.exit(1)

    if not state.device_dirty:
        print('Nothing to save, running config already matches startup config.')
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
    git_ops.commit_empty(project.PROJECT_DIR, f'c2sync save: saved to startup-config on {project.target}')
    print('\nSaved. Device is now in sync.')


def discard(arguments: list):
    LOGGER.debug(f'Given arguments: {arguments}')

    project = _require_project()

    # Revert the edit file itself, not just the staging file - otherwise
    # the discarded edits would just get re-staged the next time status/push
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
    commit (HEAD - the last confirmed push - by default), for recovering
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
        print('You have unpushed local edits that would be overwritten by revert. Run '
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
        # verbatim - same reasoning `push` already follows after its own
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
    # follow the same transitions as a successful push: local edits are (no
    # longer) a concept here, and running-config now differs from
    # startup-config again until `c2sync save`.
    Differ(project).clear_staging()
    state_engine = StateEngine(project)
    state_engine.mark_host_clean()
    state_engine.mark_device_dirty()

    print('\nReverted. Device has pending changes not yet saved to startup-config (run `c2sync save`).')


def _detect_drift(project: Project, interface) -> tuple[str, list[str]]:
    """
    Read the live running config and diff it against the baseline
    (device.config at git HEAD), with a tree diff rather than string
    equality so formatting noise in `show running-config` is not mistaken
    for a change.

    Returns (live_config, drift_lines) - shared by `fetch` (report-only) and
    `_reconcile_out_of_band_drift` (push's pre-flight, which also acts on
    it). Kept as one function so the two never drift apart on what counts
    as "changed".
    """
    baseline = git_ops.show_at_head(project.PROJECT_DIR, project.edit_file_relpath) or ''
    live = interface.get_running_config()
    return live, Differ.diff_lines(baseline, live)


def _reconcile_out_of_band_drift(
    project: Project, interface, lines: list[str], assume_yes: bool, rebase: bool,
) -> list[str]:
    """
    Verify the device still matches the baseline the staged commands were
    computed against, and return the commands to actually push.

    Everything up to this point is computed offline - `_refresh_staging()`
    diffs EDIT_FILE against `device.config` at git HEAD and never reads the
    device. If someone changed the device out-of-band (another operator on
    the console, another tool), that baseline no longer describes it and
    the preview is a description of a device that no longer exists.
    Deletions are the sharp edge, because negations are generated from the
    *baseline's* content: a `description Server` you deleted locally
    becomes `no description Server` and silently destroys the colleague's
    `description Core-Uplink` that replaced it, with nothing in the preview
    hinting at it. This is `git push` without a fetch, and the fix is the
    one git uses - check first, and refuse to push over a moved target.

    On drift, adopting the live config as the new baseline (via
    `git_ops.commit_content`, which leaves EDIT_FILE alone) and recomputing
    keeps the user's edits *and* makes the resulting preview honest. Note
    the recomputed commands will include undoing the out-of-band changes,
    since EDIT_FILE does not contain them - that is the point: it happens
    either way, and this is the version where the user sees it first.

    This is genuinely a rebase, not a force-overwrite - EDIT_FILE (the
    user's work) is untouched, only the baseline it's replayed against
    moves - which is why the flag that authorizes it is named --rebase.
    """
    live, drift = _detect_drift(project, interface)
    if not drift:
        return lines

    print('\nThe device has changed outside C2Sync since the last push, so the staged\n'
          'commands were computed against a baseline that no longer describes it.\n')
    print('Changed on the device, not by you:\n')
    for line in drift:
        print(f'  {line}')

    if rebase:
        print("\n--rebase: adopting the device's current config as the new baseline.")
    elif assume_yes:
        # -y must never stand in for this decision, and prompting here
        # would hang a CI job on stdin - so fail, loudly and non-zero.
        print('\nRefusing to push against a stale baseline. Re-run with --rebase to adopt\n'
              "the device's current config as the baseline and recompute the commands,\n"
              'or `c2sync pull --force` to take the device as-is and drop your edits.')
        sys.exit(1)
    elif not _confirm("\nAdopt the device's current config as the new baseline and recompute?"):
        print('Aborted. Nothing was sent to the device.')
        sys.exit(1)

    git_ops.commit_content(
        project.PROJECT_DIR, project.edit_file_relpath, live,
        f'c2sync push: adopted out-of-band changes on {project.target} as the new baseline',
    )

    print('\nRecomputed against the device\'s current config.')
    return _refresh_staging(project)


def _reconcile_after_failed_push(project: Project, interface, state_engine: StateEngine, error) -> bool:
    """
    Record what the device *actually* has after a push aborted partway.

    apply_config aborts the batch on the first rejected command, but the
    commands before it are already running on the device. Without this the
    baseline still describes the pre-push device, so `status` reports
    everything - landed and unlanded alike - as unpushed local edits.

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
        f'c2sync push: partially applied to {project.target} (device rejected a command)',
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
    Opt-in (`push --rollback-on-error`) undo of a partial push: diff the
    live device against the baseline the push started from and push the
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
        print('\nNothing to roll back - the device already matches the pre-push baseline.')
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
    # verbatim, the same way push and revert do after their own pushes.
    rolled_back_config = interface.get_running_config()
    git_ops.commit_content(
        project.PROJECT_DIR, project.edit_file_relpath, rolled_back_config,
        f'c2sync push: rolled back partial push to {project.target}',
    )
    # running-config was written twice (partial push, then the undo), so it
    # is not safe to claim it matches startup-config again.
    state_engine.mark_device_dirty()
    print('\nRolled back. Your edits in the config file were left untouched - fix the\n'
          'rejected command and run `c2sync push` again.')
    return True


def _require_project() -> Project:
    project = get_project()
    if project is None:
        print('No C2Sync project found in this directory. Run `c2sync init NAME SERIAL_DEVICE` to '
              'start one, or `cd` into an existing project directory first.')
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
