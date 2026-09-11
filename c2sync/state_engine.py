import json
import logging
import os

from dataclasses import dataclass

from c2sync import Project, git_ops
from c2sync.differ import Differ

LOGGER = logging.getLogger(__name__)


@dataclass
class DeviceState:
    # Staged edits in EDIT_FILE haven't been pushed to the device yet.
    # Derived from the files on every load, never stored - see StateEngine.
    host_dirty: bool = False
    # Running-config has been pushed but not yet saved to startup-config.
    # The one genuinely persistent flag: nothing local can derive it.
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

    The two flags are not stored the same way, because they are not the
    same kind of fact:

    - `host_dirty` is *derived* on every load, by comparing EDIT_FILE
      against device.config at git HEAD. It is a question the files can
      always answer, so storing it only creates a cache that can go stale -
      and a stale "clean" is a silent data-loss bug, since it's what
      `pull`/`revert` check before overwriting EDIT_FILE. Deriving it is
      the same on-demand reasoning the differ already uses: these files are
      small, and one `git show` per command is cheap.
    - `device_dirty` is *persisted*, because nothing local can derive it.
      Whether the device has running-config changes not yet written to
      startup-config is only knowable from what we last did to the device.

    device_dirty transitions are only ever driven by verified outcomes from
    the connector (a Netmiko-confirmed apply or save), never assumed on
    send.
    """

    def __init__(self, project: Project) -> None:
        self.project = project
        self.state_file = project.STATE_FILE
        self.state = self._load()


    def _load(self) -> DeviceState:
        return DeviceState(
            host_dirty=self._compute_host_dirty(),
            device_dirty=self._load_device_dirty(),
        )


    def _compute_host_dirty(self) -> bool:
        """
        True when EDIT_FILE differs from the baseline (device.config at git
        HEAD) - i.e. there are local edits that have not been pushed.

        Compared with the differ rather than by string equality, for the
        same reason drift detection is: the question is whether there are
        commands to send, not whether the bytes match. A cosmetic edit that
        produces no commands is not unpushed work, and treating it as such
        would make `pull` refuse to run while `status` simultaneously
        reported nothing staged.

        A missing EDIT_FILE or an unreadable baseline both read as "no local
        edits": there is nothing that could be lost, which is the question
        every caller is actually asking. This is also what makes a fresh
        clone correct with no stored state at all - a clean checkout matches
        HEAD by construction.
        """
        try:
            with open(self.project.EDIT_FILE) as file:
                current = file.read()
        except OSError:
            return False

        baseline = git_ops.show_at_head(
            self.project.PROJECT_DIR, self.project.edit_file_relpath) or ''

        return bool(Differ.diff_lines(baseline, current))


    def _load_device_dirty(self) -> bool:
        if not os.path.exists(self.state_file):
            return False

        try:
            with open(self.state_file) as file:
                stored = json.load(file)
        except (OSError, ValueError):
            return False

        # Read by key rather than splatting the dict in: state.json written
        # by an older version also carries a host_dirty entry, which is now
        # derived and must be ignored rather than crash the load.
        return bool(stored.get('device_dirty', False))


    def _save(self) -> None:
        # Only device_dirty is written. host_dirty is intentionally absent
        # rather than written-and-ignored, so there is no stale copy for a
        # future reader to be tempted by.
        with open(self.state_file, 'w') as file:
            json.dump({'device_dirty': self.state.device_dirty}, file)
        LOGGER.info(f'Device state -> {self.state.label}')


    def refresh_host_dirty(self) -> None:
        """
        Recompute host_dirty from the files, for a caller that has changed
        EDIT_FILE since this engine was constructed.
        """
        self.state.host_dirty = self._compute_host_dirty()


    def mark_device_dirty(self) -> None:
        self.state.device_dirty = True
        self._save()


    def mark_device_clean(self) -> None:
        self.state.device_dirty = False
        self._save()
