import json
import logging
import os

from dataclasses import asdict, dataclass

from c2sync import Project

LOGGER = logging.getLogger(__name__)


@dataclass
class DeviceState:
    # Staged edits in EDIT_FILE haven't been pushed to the device yet.
    host_dirty: bool = False
    # Running-config has been pushed but not yet saved to startup-config.
    device_dirty: bool = False

    @property
    def label(self) -> str:
        if self.host_dirty:
            return 'host pending changes'
        if self.device_dirty:
            return 'device pending changes'
        return 'synced'


class StateEngine:
    """
    Tracks whether the host config file, the device's running config, and
    the device's startup config are in sync.

    Transitions are only ever driven by verified outcomes from the
    connector (a Netmiko-confirmed apply or save), never assumed on send.
    """

    def __init__(self, project: Project) -> None:
        self.state_file = project.STATE_FILE
        self.state = self._load()


    def _load(self) -> DeviceState:
        if not os.path.exists(self.state_file):
            return DeviceState()

        with open(self.state_file) as file:
            return DeviceState(**json.load(file))


    def _save(self) -> None:
        with open(self.state_file, 'w') as file:
            json.dump(asdict(self.state), file)
        LOGGER.info(f'Device state -> {self.state.label}')


    def mark_host_dirty(self) -> None:
        self.state.host_dirty = True
        self._save()


    def mark_host_clean(self) -> None:
        self.state.host_dirty = False
        self._save()


    def mark_device_dirty(self) -> None:
        self.state.device_dirty = True
        self._save()


    def mark_device_clean(self) -> None:
        self.state.device_dirty = False
        self._save()
