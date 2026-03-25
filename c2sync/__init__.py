import os
import json
import logging

from dataclasses import dataclass

LOGGER = logging.getLogger(__name__)

APP_CONFIG_NAME = 'c2sync.config'
DEVICE_CONFIG_NAME = 'device.config'
BAUDRATE = 9600
TIMEOUT = 600
PROJECT_DIR = './.c2sync'
PROMPT_REGEX = r'[>#]\s?$'


@dataclass
class Project:
    SERIAL_DEVICE: str
    BAUDRATE: int
    TIMEOUT: int
    PROJECT_DIR: str
    CONFIG_FILE: str
    PROMPT_REGEX: str


def get_project() -> Project:
    """
        Get or create a C2Sync project object containing project information
    """
    project_dir = os.getcwd() + '/.c2sync'
    if not os.path.exists(project_dir):
        LOGGER.info(f'Project directory not found in current working directory, creating {project_dir}')
        os.makedirs(project_dir)
        config_dict = _create_configuration(project_dir)
    else:
        config_dict = _load_configuration(project_dir)
            
    c2sync = Project(**config_dict)

    return c2sync


def _create_configuration(project_dir: str) -> dict:
    app_config_path = os.path.join(project_dir, APP_CONFIG_NAME)
    device_config_path = os.path.join(project_dir, DEVICE_CONFIG_NAME)

    config_dict: dict = {
        'SERIAL_DEVICE': _get_serial_device(),
        'BAUDRATE': BAUDRATE,
        'TIMEOUT': TIMEOUT,
        'PROJECT_DIR': project_dir,
        'CONFIG_FILE': device_config_path,
        'PROMPT_REGEX': PROMPT_REGEX
    }

    with open(app_config_path, 'w') as config_file:
        json.dump(config_dict, config_file)

    LOGGER.info(f'Created configuration: {config_dict}')

    return config_dict


def _load_configuration(project_dir: str) -> dict:
    config_file_path = os.path.join(project_dir, APP_CONFIG_NAME)
    with open(config_file_path, 'r') as config_file:
        config_dict: dict = json.load(config_file)
    
    LOGGER.info(f'Loaded configuration: {config_dict}')

    return config_dict


def _get_serial_device() -> str:
    # TODO: Get arguments
    return "/dev/ttyUSB0"