from pathlib import Path
from dataclasses import dataclass
from enum import StrEnum

@dataclass(frozen=True)
class ConfigLine:
    """
    Represents a line in the configuration file.
    """
    index: int
    text: str
    indent: int


@dataclass(frozen=True)
class CommandBlock:
    """
    Represents a CLI command with its full hierarchical context.
    """
    context: tuple[str, ...]
    command: str


class DeviceState(StrEnum):
    SYNCED = "SYNCED"
    HOST_PENDING = "HOST_PENDING"
    DEVICE_PENDING = "DEVICE_PENDING"

class Device:
    """
    A class representing devices included in the project. Contains a name and the TTY device used to communicate with the device,
    as well as the path to the pulled config file and its staging file 
    """
    def __init__(self, name: str, tty: str):
        self.name = name
        self.tty = tty
        self.config_path = Path(f".c2sync/{name}.config")
        self.staging_path = Path(f".c2sync/.{name}.staging")

    def save_config(self, config):
        """
        Save config file to host

        Args:
            config (str): The string to write to the config file
        """
        self.config_path.write_text(config)

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "tty": self.tty,
        }
    