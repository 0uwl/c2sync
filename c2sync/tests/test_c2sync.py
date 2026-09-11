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


# ------------------------------------------------------------------
# c2sync.toml: tracked in git, and what makes a plain `git clone` work
# ------------------------------------------------------------------

def test_c2sync_toml_is_git_tracked(tmp_path, monkeypatch):
    from c2sync import Project, git_ops, init_project

    monkeypatch.chdir(tmp_path)
    init_project(Project(NAME='myrouter', SERIAL_DEVICE='/dev/ttyUSB0'))

    tracked = git_ops.show_at_head('.', 'c2sync.toml')

    assert tracked is not None
    assert 'myrouter' in tracked


def test_none_valued_fields_are_omitted_from_written_toml(tmp_path, monkeypatch):
    from c2sync import Project, git_ops, init_project

    monkeypatch.chdir(tmp_path)
    init_project(Project(NAME='myrouter', TRANSPORT='ssh', HOST='10.0.0.1'))

    written = git_ops.show_at_head('.', 'c2sync.toml')

    # TOML has no null type - a field that's actually None for this
    # transport (SERIAL_DEVICE, unset on an SSH project) must be left out
    # entirely, not written as an empty/null value. BAUDRATE isn't one of
    # these - it always defaults to a real int regardless of TRANSPORT, so
    # it's still written even though it's meaningless for SSH.
    assert 'SERIAL_DEVICE' not in written
    assert 'HOST' in written


def test_get_project_bootstraps_scratch_state_after_a_simulated_clone(tmp_path, monkeypatch):
    """
    Simulates what a fresh `git clone` of a c2sync project looks like:
    c2sync.toml/device.config/.git are present (tracked), but .c2sync/ is
    not (gitignored, never travels with a clone). get_project() must still
    find the project and silently recreate working scratch state on its
    own - the whole point of tracking c2sync.toml in the first place.
    """
    import shutil

    from c2sync import Project, get_project, init_project
    from c2sync.state_engine import StateEngine

    monkeypatch.chdir(tmp_path)
    init_project(Project(NAME='myrouter', SERIAL_DEVICE='/dev/ttyUSB0'))
    shutil.rmtree('.c2sync')

    project = get_project()

    assert project is not None
    assert project.NAME == 'myrouter'
    assert project.SERIAL_DEVICE == '/dev/ttyUSB0'
    assert os.path.isfile(project.STAGING_FILE)
    assert os.path.isfile(project.STATE_FILE)
    # A freshly-cloned working tree always matches HEAD, so this is a
    # verified fact, not just a placeholder default.
    assert StateEngine(project).state.host_dirty is False


def test_init_refuses_when_c2sync_toml_exists_even_without_a_scratch_dir(tmp_path, monkeypatch):
    """
    The ProjectExistsError guard has to key off c2sync.toml, not the
    scratch dir - a freshly cloned project has the former but not the
    latter, and must still be refused rather than treated as blank and
    silently overwritten.
    """
    import shutil

    import pytest

    from c2sync import Project, init_project
    from c2sync.exceptions import ProjectExistsError

    monkeypatch.chdir(tmp_path)
    init_project(Project(NAME='myrouter', SERIAL_DEVICE='/dev/ttyUSB0'))
    shutil.rmtree('.c2sync')

    with pytest.raises(ProjectExistsError):
        init_project(Project(NAME='myrouter', SERIAL_DEVICE='/dev/ttyUSB0'))