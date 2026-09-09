import os

from watchdog.events import FileModifiedEvent

from c2sync import Project, init_project
from c2sync.state_engine import StateEngine
from c2sync.watcher import ConfigWatcher


def _make_project(tmp_path) -> Project:
    project_dir = str(tmp_path / '.c2sync')
    project = Project(
        SERIAL_DEVICE='NOT USED',
        PROJECT_DIR=project_dir,
        CONFIG_FILE=os.path.join(project_dir, 'c2sync.config'),
        EDIT_FILE=os.path.join(project_dir, 'device.config'),
        STAGING_FILE=os.path.join(project_dir, 'staging.txt'),
        STATE_FILE=os.path.join(project_dir, 'state.json'),
    )
    init_project(project)
    return project


def _write(path, lines):
    with open(path, 'w') as file:
        file.write('\n'.join(lines) + '\n')


def test_on_modified_stages_changes_and_marks_host_dirty(tmp_path):
    project = _make_project(tmp_path)
    _write(project.EDIT_FILE, ['interface Gi1/0/1'])

    watcher = ConfigWatcher(project)

    _write(project.EDIT_FILE, ['interface Gi1/0/1', ' shutdown'])
    watcher.on_modified(FileModifiedEvent(project.EDIT_FILE))

    with open(project.STAGING_FILE) as file:
        content = file.read()
    assert 'shutdown' in content

    assert StateEngine(project).state.host_dirty is True


def test_on_modified_updates_baseline_so_repeat_saves_dont_restage(tmp_path):
    project = _make_project(tmp_path)
    _write(project.EDIT_FILE, ['interface Gi1/0/1'])

    watcher = ConfigWatcher(project)

    _write(project.EDIT_FILE, ['interface Gi1/0/1', ' shutdown'])
    watcher.on_modified(FileModifiedEvent(project.EDIT_FILE))

    # Save again with an unrelated addition. Without tracking the latest
    # saved content as the new baseline, this would re-diff against the
    # very first version and duplicate the "shutdown" line in staging.
    _write(project.EDIT_FILE, ['interface Gi1/0/1', ' shutdown', ' no ip proxy-arp'])
    watcher.on_modified(FileModifiedEvent(project.EDIT_FILE))

    with open(project.STAGING_FILE) as file:
        content = file.read()

    assert content.count('shutdown') == 1


def test_on_modified_ignores_events_for_other_files(tmp_path):
    project = _make_project(tmp_path)
    _write(project.EDIT_FILE, ['interface Gi1/0/1'])

    watcher = ConfigWatcher(project)
    watcher.on_modified(FileModifiedEvent(str(tmp_path / 'unrelated.txt')))

    with open(project.STAGING_FILE) as file:
        assert file.read() == ''

    assert StateEngine(project).state.host_dirty is False
