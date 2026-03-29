from pathlib import Path
from dataclasses import dataclass

from c2sync.serial_interface import SerialConnection

@dataclass
class DeviceState:
    SYNCED = "SYNCED"
    HOST_PENDING = "HOST_PENDING"
    DEVIE_PENDING = "DEVIE_PENDING"
    HOST_PENDING = "HOST_PENDING"


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

    def get_state(self) -> str:
        """
        Get the current state of the device

        Returns:
            str: The state of the device
        """
        serial = SerialConnection(self.tty)

        if self.staging_path.exists() and self.staging_path.stat().st_size > 0:
            return DeviceState.HOST_PENDING
        elif serial.is_config_synced():
            return DeviceState.DEVIE_PENDING
        else:
            return DeviceState.SYNCED

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "tty": self.tty,
            "config_path": str(self.config_path),
            "staging_path": str(self.staging_path)
        }
    