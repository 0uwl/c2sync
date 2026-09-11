class C2SyncError(Exception):
    """Base class for c2sync-specific errors."""


class ConfigApplyError(C2SyncError):
    """Raised when the device rejected one or more staged configuration commands."""


class ConfigSaveError(C2SyncError):
    """Raised when the device did not confirm that running-config was saved to startup-config."""


class HostKeyRejectedError(C2SyncError):
    """Raised when an unknown SSH host key couldn't be verified/trusted (declined, or the check itself failed)."""


class ProjectExistsError(C2SyncError):
    """Raised when `init` targets a directory that already holds a c2sync project."""


class ConfigReadError(C2SyncError):
    """Raised when what came back from `show running-config` isn't a running config.

    Console logging, a session left in config mode, or a dropped read can all
    put non-config text on the wire. Anything that reaches a caller is liable
    to be committed as the git baseline, so an unrecognizable read has to fail
    loudly rather than be stored as though it described the device.
    """
