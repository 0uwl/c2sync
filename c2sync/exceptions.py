class C2SyncError(Exception):
    """Base class for c2sync-specific errors."""


class ConfigApplyError(C2SyncError):
    """Raised when the device rejected one or more staged configuration commands."""


class ConfigSaveError(C2SyncError):
    """Raised when the device did not confirm that running-config was saved to startup-config."""


class HostKeyRejectedError(C2SyncError):
    """Raised when an unknown SSH host key couldn't be verified/trusted (declined, or the check itself failed)."""
