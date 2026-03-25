import os
import json
import logging

from dataclasses import dataclass

LOGGER = logging.getLogger(__name__)

APP_CONFIG_NAME = 'c2sync.config'
DEVICE_CONFIG_NAME = 'device.config'
STAGING_FILE_NAME = 'staging.txt'
PROJECT_ROOT = './.c2sync'

@dataclass
class Project:
    SERIAL_DEVICE: str
    BAUDRATE: int = 9600
    TIMEOUT: int = 600
    PROJECT_DIR: str = PROJECT_ROOT
    CONFIG_FILE: str = os.path.join(PROJECT_DIR, APP_CONFIG_NAME)
    EDIT_FILE: str = os.path.join(PROJECT_DIR, DEVICE_CONFIG_NAME)
    PROMPT_REGEX: str = r'[>#]\s?$'
    STAGING_FILE: str = os.path.join(PROJECT_DIR, STAGING_FILE_NAME)

    def to_dict(self):
        return {
        'SERIAL_DEVICE': self.SERIAL_DEVICE,
        'BAUDRATE': self.BAUDRATE,
        'TIMEOUT': self.TIMEOUT,
        'PROJECT_DIR': self.PROJECT_DIR,
        'EDIT_FILE': self.EDIT_FILE,
        'PROMPT_REGEX': self.PROMPT_REGEX,
        'STAGING_FILE': self.STAGING_FILE
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


def get_serial_device():
    # TODO: Get arguments
    return '/dev/ttyUSB0'