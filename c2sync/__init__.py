import os
import json
import logging

from dataclasses import dataclass

from c2sync import git_ops

LOGGER = logging.getLogger(__name__)

APP_CONFIG_NAME = 'c2sync.config'
DEVICE_CONFIG_NAME = 'device.config'
STAGING_FILE_NAME = 'staging.txt'
STATE_FILE_NAME = 'state.json'
PROJECT_ROOT = './.c2sync'

@dataclass
class Project:
    # TRANSPORT picks which of the fields below matter: 'serial' uses
    # SERIAL_DEVICE/BAUDRATE, 'ssh' uses HOST/SSH_PORT. Both sets of fields
    # always exist on Project so serialization stays a flat dict either way.
    TRANSPORT: str = 'serial'
    SERIAL_DEVICE: str = None
    BAUDRATE: int = 9600
    HOST: str = None
    SSH_PORT: int = 22
    TIMEOUT: int = 600
    PROJECT_DIR: str = PROJECT_ROOT
    CONFIG_FILE: str = os.path.join(PROJECT_DIR, APP_CONFIG_NAME)
    # The "baseline" (config as of the last confirmed sync) is no longer a
    # separate file - it's whatever EDIT_FILE looks like at git HEAD in
    # PROJECT_DIR, the same way `git status` diffs against the index.
    EDIT_FILE: str = os.path.join(PROJECT_DIR, DEVICE_CONFIG_NAME)
    PROMPT_REGEX: str = r'[>#]\s?$'
    STAGING_FILE: str = os.path.join(PROJECT_DIR, STAGING_FILE_NAME)
    STATE_FILE: str = os.path.join(PROJECT_DIR, STATE_FILE_NAME)

    @property
    def target(self) -> str:
        """
        The single human-readable identifier for whichever device this
        project points at (a serial path or a hostname/IP), used in git
        commit messages and the like so callers don't need to branch on
        TRANSPORT themselves.
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

    def to_dict(self):
        return {
        'TRANSPORT': self.TRANSPORT,
        'SERIAL_DEVICE': self.SERIAL_DEVICE,
        'BAUDRATE': self.BAUDRATE,
        'HOST': self.HOST,
        'SSH_PORT': self.SSH_PORT,
        'TIMEOUT': self.TIMEOUT,
        'PROJECT_DIR': self.PROJECT_DIR,
        'EDIT_FILE': self.EDIT_FILE,
        'PROMPT_REGEX': self.PROMPT_REGEX,
        'STAGING_FILE': self.STAGING_FILE,
        'STATE_FILE': self.STATE_FILE
    }


def init_project(project_config: Project):  
    LOGGER.info(f'Creating project in {project_config.PROJECT_DIR}')
    os.makedirs(project_config.PROJECT_DIR)

    # Create a dict of the project config
    config_dict: dict = project_config.to_dict()

    with open(project_config.CONFIG_FILE, 'w') as config_file:
        json.dump(config_dict, config_file)

    open(project_config.EDIT_FILE, 'w').close()
    open(project_config.STAGING_FILE, 'w').close()

    with open(project_config.STATE_FILE, 'w') as state_file:
        json.dump({'host_dirty': False, 'device_dirty': False}, state_file)

    # Only the device config itself is worth tracking/reviewing in git -
    # c2sync.config/state.json/staging.txt are local operational scratch.
    gitignore_path = os.path.join(project_config.PROJECT_DIR, '.gitignore')
    with open(gitignore_path, 'w') as gitignore:
        gitignore.write('\n'.join([APP_CONFIG_NAME, STATE_FILE_NAME, STAGING_FILE_NAME]) + '\n')

    git_ops.init(project_config.PROJECT_DIR)
    git_ops.commit(project_config.PROJECT_DIR, [DEVICE_CONFIG_NAME, '.gitignore'], 'c2sync init: empty baseline')

    LOGGER.info(f'Created project')
    print('C2Sync project initialized')


def get_project() -> Project | None:
    """
        Get the C2Sync project object or return None if no project exists in the current working directory
    """
    if not os.path.exists(PROJECT_ROOT):
        LOGGER.error(f'No project found in current working directory')
        return None

    config_dict = _load_configuration()
            
    c2sync = Project(**config_dict)

    return c2sync


def _load_configuration() -> dict:
    config_file_path = os.path.join(PROJECT_ROOT, APP_CONFIG_NAME)
    with open(config_file_path, 'r') as config_file:
        config_dict: dict = json.load(config_file)
    
    LOGGER.info(f'Loaded configuration: {config_dict}')

    return config_dict