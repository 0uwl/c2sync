import json
import os

import pytest

from c2sync.state_engine import DeviceState, StateEngine

from constants import PROJECT


@pytest.fixture
def clean_edit_file():
    """
    Restore EDIT_FILE to the empty baseline `init` committed.

    host_dirty is derived from this file, so a test that leaves edits behind
    would change what every later test computes - the project fixture is
    session-scoped and shared.
    """
    yield
    open(PROJECT.EDIT_FILE, 'w').close()


def _write_edit_file(text):
    with open(PROJECT.EDIT_FILE, 'w') as file:
        file.write(text)


def _write_state_file(payload):
    with open(PROJECT.STATE_FILE, 'w') as file:
        json.dump(payload, file)


# ------------------------------------------------------------------
# label
# ------------------------------------------------------------------

def test_label_reports_host_before_device():
    assert DeviceState(host_dirty=True, device_dirty=True).label == 'host pending changes'
    assert DeviceState(host_dirty=False, device_dirty=True).label == 'device pending changes'
    assert DeviceState(host_dirty=False, device_dirty=False).label == 'synced'


# ------------------------------------------------------------------
# device_dirty - the persisted half
# ------------------------------------------------------------------

def test_new_state_defaults_to_synced():
    if os.path.exists(PROJECT.STATE_FILE):
        os.remove(PROJECT.STATE_FILE)

    engine = StateEngine(PROJECT)

    assert engine.state == DeviceState(host_dirty=False, device_dirty=False)
    assert engine.state.label == 'synced'


def test_device_dirty_persists_across_reload():
    engine = StateEngine(PROJECT)
    engine.mark_device_dirty()

    assert StateEngine(PROJECT).state.device_dirty is True

    engine.mark_device_clean()
    assert StateEngine(PROJECT).state.device_dirty is False


def test_host_dirty_is_never_written_to_the_state_file():
    """
    Deliberately absent rather than written-and-ignored: a stored copy is
    the thing that went stale and silently authorised overwriting local
    edits, so there must be nothing there for a future reader to trust.
    """
    StateEngine(PROJECT).mark_device_dirty()

    with open(PROJECT.STATE_FILE) as file:
        assert 'host_dirty' not in json.load(file)

    StateEngine(PROJECT).mark_device_clean()


def test_state_file_written_by_an_older_version_still_loads():
    _write_state_file({'host_dirty': True, 'device_dirty': True})

    state = StateEngine(PROJECT).state

    # The stale host_dirty entry is ignored, not splatted into the dataclass.
    assert state.device_dirty is True
    assert state.host_dirty is False

    StateEngine(PROJECT).mark_device_clean()


# ------------------------------------------------------------------
# host_dirty - the derived half
# ------------------------------------------------------------------

def test_host_dirty_is_derived_from_the_edit_file(clean_edit_file):
    assert StateEngine(PROJECT).state.host_dirty is False

    _write_edit_file('interface Gi1/0/1\n description Local\nend\n')

    # No mark_* call anywhere: a new engine computes it from the files.
    assert StateEngine(PROJECT).state.host_dirty is True


def test_host_dirty_ignores_a_stale_clean_flag_in_the_state_file(clean_edit_file):
    """
    The regression this whole change exists for.

    `pull`/`save`/`revert` read host_dirty to decide whether overwriting
    EDIT_FILE would destroy unpushed work. When the flag was persisted, it
    was only refreshed by `status`/`push` - so editing the file and running
    `pull` straight away found a stale "clean" and silently clobbered the
    edits.
    """
    _write_edit_file('interface Gi1/0/1\n description Unpushed\nend\n')
    _write_state_file({'host_dirty': False, 'device_dirty': False})

    assert StateEngine(PROJECT).state.host_dirty is True


def test_cosmetic_edit_that_stages_no_commands_is_not_host_dirty(clean_edit_file):
    """
    Derived via the differ, not string equality, so this agrees with what
    `status` reports. A file that differs byte-wise but yields no commands
    is not unpushed work, and `pull` must not refuse to run over it while
    `status` says nothing is staged.
    """
    _write_edit_file('\n\n!\n')

    assert StateEngine(PROJECT).state.host_dirty is False


def test_refresh_host_dirty_recomputes_after_the_file_changes(clean_edit_file):
    engine = StateEngine(PROJECT)
    assert engine.state.host_dirty is False

    _write_edit_file('hostname Changed\nend\n')
    engine.refresh_host_dirty()

    assert engine.state.host_dirty is True
