import os
import json
import logging
import tomllib

from dataclasses import dataclass

from c2sync import git_ops
from c2sync.exceptions import ProjectExistsError

LOGGER = logging.getLogger(__name__)

# Tracked in git, at the project directory's top level - see the module
# docstring-ish note on Project.to_dict() for why. TOML (not JSON) so it's
# meant to be hand-edited too, same rationale as user_config.py's global file.
APP_CONFIG_NAME = 'c2sync.toml'
DEVICE_CONFIG_NAME = 'device.config'
STAGING_FILE_NAME = 'staging.txt'
STATE_FILE_NAME = 'state.json'
# The only thing still hidden: pure operational scratch nobody needs to
# open, and - unlike APP_CONFIG_NAME - genuinely local to one checkout
# (never committed, never travels with a clone). device.config, .gitignore,
# .git/ and c2sync.toml all sit at the project directory's top level
# instead - see Project.at() and init_project() below.
SCRATCH_DIR_NAME = '.c2sync'
# Every command except `init` expects cwd to already BE the project
# directory (you `cd` into it, same as a git repo) - these are the paths
# get_project() looks for relative to cwd to confirm that.
SCRATCH_DIR = os.path.join('.', SCRATCH_DIR_NAME)
DEFAULT_CONFIG_FILE = os.path.join('.', APP_CONFIG_NAME)

@dataclass
class Project:
    # No default: every project has one, used for the default directory
    # name at `init` time and as the human-readable identifier elsewhere
    # (status output, and multi-device tooling down the line). Must be
    # first - dataclasses require every field without a default to precede
    # every field that has one.
    NAME: str
    # TRANSPORT picks which of the fields below matter: 'serial' uses
    # SERIAL_DEVICE/BAUDRATE, 'ssh' uses HOST/SSH_PORT. Both sets of fields
    # always exist on Project so serialization stays a flat dict either way.
    TRANSPORT: str = 'serial'
    SERIAL_DEVICE: str = None
    BAUDRATE: int = 9600
    HOST: str = None
    SSH_PORT: int = 22
    TIMEOUT: int = 600
    PROMPT_REGEX: str = r'[>#]\s?$'
    # These five default to paths relative to '.', correct once cwd is
    # already inside the project directory - which is exactly the state
    # every command other than `init` expects to run in. Project.at() below
    # is what a caller needs instead when project_dir isn't '.' (init
    # creating a fresh directory, a test using a tmp_path).
    PROJECT_DIR: str = '.'
    CONFIG_FILE: str = DEFAULT_CONFIG_FILE
    # The "baseline" (config as of the last confirmed push) is no longer a
    # separate file - it's whatever EDIT_FILE looks like at git HEAD in
    # PROJECT_DIR, the same way `git status` diffs against the index.
    EDIT_FILE: str = os.path.join('.', DEVICE_CONFIG_NAME)
    STAGING_FILE: str = os.path.join('.', SCRATCH_DIR_NAME, STAGING_FILE_NAME)
    STATE_FILE: str = os.path.join('.', SCRATCH_DIR_NAME, STATE_FILE_NAME)

    @classmethod
    def at(cls, project_dir: str, **kwargs) -> 'Project':
        """
        Build a Project physically rooted at project_dir, with every path
        field correctly derived from it.

        The dataclass's own field defaults above are only correct for the
        implicit project_dir='.' case (already sitting inside the project) -
        they're plain strings evaluated once at class definition time, not
        computed per-instance from self.PROJECT_DIR. Anything that creates a
        project somewhere other than '.' (init creating ./NAME, a test using
        a tmp_path) needs this instead of the plain constructor.
        """
        return cls(
            PROJECT_DIR=project_dir,
            CONFIG_FILE=os.path.join(project_dir, APP_CONFIG_NAME),
            EDIT_FILE=os.path.join(project_dir, DEVICE_CONFIG_NAME),
            STAGING_FILE=os.path.join(project_dir, SCRATCH_DIR_NAME, STAGING_FILE_NAME),
            STATE_FILE=os.path.join(project_dir, SCRATCH_DIR_NAME, STATE_FILE_NAME),
            **kwargs,
        )

    @property
    def target(self) -> str:
        """
        The single human-readable identifier for whichever device this
        project points at (a serial path or a hostname/IP), used in git
        commit messages and the like so callers don't need to branch on
        TRANSPORT themselves. Deliberately not NAME: this names the device
        being talked to, NAME names the project.
        """
        return self.HOST if self.TRANSPORT == 'ssh' else self.SERIAL_DEVICE

    @property
    def edit_file_relpath(self) -> str:
        """
        EDIT_FILE's path relative to PROJECT_DIR - what git_ops calls need
        (they run with `git -C PROJECT_DIR ...`), instead of every caller
        recomputing os.path.relpath(EDIT_FILE, PROJECT_DIR) itself.
        """
        return os.path.relpath(self.EDIT_FILE, self.PROJECT_DIR)

    def to_dict(self) -> dict:
        """
        Deliberately excludes PROJECT_DIR and the four file paths derived
        from it. A path is only ever valid relative to wherever `init` was
        physically run from, but every command that later loads this
        config expects to run with cwd already inside the project
        directory (see get_project()), where the fixed '.'-relative
        defaults above are always correct regardless of how this directory
        was originally reached, what it was named at the time, or whether
        it's since been moved, renamed, or cloned somewhere else entirely.
        """
        return {
        'NAME': self.NAME,
        'TRANSPORT': self.TRANSPORT,
        'SERIAL_DEVICE': self.SERIAL_DEVICE,
        'BAUDRATE': self.BAUDRATE,
        'HOST': self.HOST,
        'SSH_PORT': self.SSH_PORT,
        'TIMEOUT': self.TIMEOUT,
        'PROMPT_REGEX': self.PROMPT_REGEX,
    }


def init_project(project_config: Project) -> None:
    """
    Physically create the project at project_config.PROJECT_DIR (which may
    not be '.' - e.g. `c2sync init NAME ...` creates ./NAME by default; see
    Project.at()).

    Refuses to run if the target already holds a c2sync project (its
    c2sync.toml already exists) rather than silently overwriting
    device.config/c2sync.toml - unlike `git init`, which is safe to rerun
    in place, this also writes/would overwrite device.config, so the same
    idempotent-rerun behavior would mean silent data loss. Checking
    c2sync.toml specifically (not the scratch dir) matters once c2sync.toml
    is git-tracked: a freshly `git clone`d project has c2sync.toml but no
    scratch dir yet, and must still be refused, not treated as untouched.
    """
    if os.path.exists(project_config.CONFIG_FILE):
        raise ProjectExistsError(f'{project_config.PROJECT_DIR!r} is already a c2sync project')

    LOGGER.info(f'Creating project {project_config.NAME!r} in {project_config.PROJECT_DIR}')
    os.makedirs(project_config.PROJECT_DIR, exist_ok=True)

    scratch_dir = os.path.dirname(project_config.STATE_FILE)
    os.makedirs(scratch_dir)

    _write_toml(project_config.CONFIG_FILE, project_config.to_dict())

    open(project_config.EDIT_FILE, 'w').close()
    open(project_config.STAGING_FILE, 'w').close()

    with open(project_config.STATE_FILE, 'w') as state_file:
        json.dump({'device_dirty': False}, state_file)

    # Only the scratch dir is local operational state - device.config and
    # c2sync.toml are both worth tracking/reviewing in git.
    gitignore_path = os.path.join(project_config.PROJECT_DIR, '.gitignore')
    with open(gitignore_path, 'w') as gitignore:
        gitignore.write(f'{SCRATCH_DIR_NAME}/\n')

    git_ops.init(project_config.PROJECT_DIR)
    git_ops.commit(
        project_config.PROJECT_DIR,
        [DEVICE_CONFIG_NAME, '.gitignore', APP_CONFIG_NAME],
        'c2sync init: empty baseline',
    )

    LOGGER.info(f'Created project')
    print('C2Sync project initialized')


def get_project() -> Project | None:
    """
        Get the C2Sync project object for the current working directory, or
        None if it isn't (or isn't inside) a c2sync project.
    """
    if not os.path.exists(DEFAULT_CONFIG_FILE):
        LOGGER.error(f'No project found in current working directory')
        return None

    config_dict = _load_configuration()
    c2sync = Project(**config_dict)

    _ensure_scratch_state(c2sync)

    return c2sync


def _ensure_scratch_state(project: Project) -> None:
    """
    Recreate the untracked operational scratch state (staging.txt,
    state.json) if it's missing - the case right after a fresh `git clone`
    of a c2sync project, since SCRATCH_DIR_NAME is gitignored and never
    travels with the repo. This is what makes a plain clone sufficient to
    start working, mirroring git itself needing no post-clone setup step:
    the tracked c2sync.toml + device.config + history is enough on its own.

    Only device_dirty is stored. It is a real unknown at this point (the
    device's actual state is unread) rather than a verified fact, and
    nothing local can derive it - `c2sync fetch` is the way to true it up.
    host_dirty needs no entry: it is derived from EDIT_FILE vs. git HEAD on
    every load, which for a fresh checkout correctly computes clean without
    anything having to assert it here.

    Safe to call unconditionally - the overwhelmingly common case (scratch
    state already exists) is a single os.path.exists check and nothing
    else happens.
    """
    if os.path.exists(SCRATCH_DIR):
        return

    print('Setting up local state for this project (first time here)...')
    os.makedirs(SCRATCH_DIR)
    open(project.STAGING_FILE, 'w').close()
    with open(project.STATE_FILE, 'w') as state_file:
        json.dump({'device_dirty': False}, state_file)


def _write_toml(path: str, data: dict) -> None:
    """
    Minimal TOML writer for c2sync.toml's flat scalar fields (str/int only,
    no nesting or arrays) - not a general-purpose TOML library, just enough
    for Project.to_dict()'s shape. Reading it back uses stdlib tomllib
    (read-only), same as user_config.py's global config file.

    None values are omitted entirely rather than written as empty/null,
    since TOML has no null type - this is also what keeps an SSH project's
    c2sync.toml free of a meaningless SERIAL_DEVICE line, and vice versa.
    """
    lines = []
    for key, value in data.items():
        if value is None:
            continue
        if isinstance(value, str):
            escaped = value.replace('\\', '\\\\').replace('"', '\\"')
            lines.append(f'{key} = "{escaped}"')
        else:
            lines.append(f'{key} = {value}')

    with open(path, 'w') as file:
        file.write('\n'.join(lines) + '\n')


def _load_configuration() -> dict:
    with open(DEFAULT_CONFIG_FILE, 'rb') as config_file:
        config_dict: dict = tomllib.load(config_file)

    LOGGER.info(f'Loaded configuration: {config_dict}')

    return config_dict
