import os
import json
import logging

from dataclasses import dataclass

from c2sync import git_ops
from c2sync.exceptions import ProjectExistsError

LOGGER = logging.getLogger(__name__)

APP_CONFIG_NAME = 'c2sync.config'
DEVICE_CONFIG_NAME = 'device.config'
STAGING_FILE_NAME = 'staging.txt'
STATE_FILE_NAME = 'state.json'
# The one thing still hidden: pure operational scratch nobody needs to open.
# device.config, .gitignore and .git/ all sit at the project directory's top
# level instead - see Project.at() and init_project() below.
SCRATCH_DIR_NAME = '.c2sync'
# Every command except `init` expects cwd to already BE the project
# directory (you `cd` into it, same as a git repo) - this is the marker
# get_project() looks for relative to cwd to confirm that.
SCRATCH_DIR = os.path.join('.', SCRATCH_DIR_NAME)

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
    CONFIG_FILE: str = os.path.join('.', SCRATCH_DIR_NAME, APP_CONFIG_NAME)
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
            CONFIG_FILE=os.path.join(project_dir, SCRATCH_DIR_NAME, APP_CONFIG_NAME),
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
        physically run from - but every command that later loads this
        config expects to run with cwd already inside the project
        directory (see get_project()), where the fixed '.'-relative
        defaults above are always correct regardless of how this directory
        was originally reached, what it was named at the time, or whether
        it's since been moved or renamed. Storing them would just be a
        stale, overridden-on-load copy of information the dataclass
        defaults already provide for free.
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

    Refuses to run if the target already holds a c2sync project (a SCRATCH_
    DIR_NAME already exists there) rather than silently overwriting its
    device.config/c2sync.config - unlike `git init`, which is safe to rerun
    in place, this also writes/would overwrite device.config, so the same
    idempotent-rerun behavior would mean silent data loss.
    """
    scratch_dir = os.path.dirname(project_config.CONFIG_FILE)

    if os.path.exists(scratch_dir):
        raise ProjectExistsError(f'{project_config.PROJECT_DIR!r} is already a c2sync project')

    LOGGER.info(f'Creating project {project_config.NAME!r} in {project_config.PROJECT_DIR}')
    os.makedirs(project_config.PROJECT_DIR, exist_ok=True)
    os.makedirs(scratch_dir)

    with open(project_config.CONFIG_FILE, 'w') as config_file:
        json.dump(project_config.to_dict(), config_file)

    open(project_config.EDIT_FILE, 'w').close()
    open(project_config.STAGING_FILE, 'w').close()

    with open(project_config.STATE_FILE, 'w') as state_file:
        json.dump({'host_dirty': False, 'device_dirty': False}, state_file)

    # Only device.config itself is worth tracking/reviewing in git - the
    # whole scratch directory is local operational state.
    gitignore_path = os.path.join(project_config.PROJECT_DIR, '.gitignore')
    with open(gitignore_path, 'w') as gitignore:
        gitignore.write(f'{SCRATCH_DIR_NAME}/\n')

    git_ops.init(project_config.PROJECT_DIR)
    git_ops.commit(project_config.PROJECT_DIR, [DEVICE_CONFIG_NAME, '.gitignore'], 'c2sync init: empty baseline')

    LOGGER.info(f'Created project')
    print('C2Sync project initialized')


def get_project() -> Project | None:
    """
        Get the C2Sync project object for the current working directory, or
        None if it isn't (or isn't inside) a c2sync project.
    """
    if not os.path.exists(SCRATCH_DIR):
        LOGGER.error(f'No project found in current working directory')
        return None

    config_dict = _load_configuration()

    c2sync = Project(**config_dict)

    return c2sync


def _load_configuration() -> dict:
    config_file_path = os.path.join(SCRATCH_DIR, APP_CONFIG_NAME)
    with open(config_file_path, 'r') as config_file:
        config_dict: dict = json.load(config_file)

    LOGGER.info(f'Loaded configuration: {config_dict}')

    return config_dict
