import pytest
import os
import shutil

from c2sync import init_project

from constants import PROJECT
from mocks.mock_device import MockCiscoDevice

@pytest.fixture(scope='session', autouse=True)
def project_init():
    init_project(PROJECT)

    yield

    if os.path.isdir(PROJECT.PROJECT_DIR):
        shutil.rmtree(PROJECT.PROJECT_DIR)




@pytest.fixture
def mock_device():
    """
    Provides a mock Cisco device with a base config.
    """
    initial_config = [
        "hostname Router1",
        "interface Gi1/0/1",
        " description OLD",
    ]

    return MockCiscoDevice(initial_config)