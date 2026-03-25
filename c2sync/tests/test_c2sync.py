import os

from c2sync import get_project

from constants import PROJECT

def test_init_project():
    """
    Test that the project that is initialized in conftest.py is correct
    """
    assert os.path.exists(PROJECT.PROJECT_DIR)
    assert os.path.isfile(PROJECT.CONFIG_FILE)
    assert os.path.isfile(PROJECT.EDIT_FILE)
    assert os.path.isfile(PROJECT.STAGING_FILE)


def test_get_project():
    project = get_project()

    assert project