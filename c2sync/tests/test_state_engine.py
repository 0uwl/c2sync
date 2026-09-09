import os

from c2sync.state_engine import DeviceState, StateEngine

from constants import PROJECT


def test_new_state_defaults_to_synced():
    if os.path.exists(PROJECT.STATE_FILE):
        os.remove(PROJECT.STATE_FILE)

    engine = StateEngine(PROJECT)

    assert engine.state == DeviceState(host_dirty=False, device_dirty=False)
    assert engine.state.label == 'synced'


def test_mark_host_dirty_persists_across_reload():
    engine = StateEngine(PROJECT)
    engine.mark_host_dirty()

    assert engine.state.label == 'host pending changes'

    reloaded = StateEngine(PROJECT)
    assert reloaded.state.host_dirty is True

    engine.mark_host_clean()


def test_host_dirty_takes_priority_over_device_dirty():
    engine = StateEngine(PROJECT)
    engine.mark_device_dirty()
    engine.mark_host_dirty()

    assert engine.state.label == 'host pending changes'

    engine.mark_host_clean()
    assert engine.state.label == 'device pending changes'

    engine.mark_device_clean()
    assert engine.state.label == 'synced'
