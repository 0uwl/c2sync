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
    assert os.path.isdir(os.path.join(PROJECT.PROJECT_DIR, '.git'))
    assert os.path.isfile(PROJECT.STAGING_FILE)
    assert os.path.isfile(PROJECT.STATE_FILE)


def test_get_project(monkeypatch):
    # get_project() looks for a project relative to cwd, same as every
    # other command - unlike the old layout, PROJECT.PROJECT_DIR is no
    # longer '.' itself, so finding it means cd'ing into it first.
    monkeypatch.chdir(PROJECT.PROJECT_DIR)
    project = get_project()

    assert project