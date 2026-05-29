import getpass
import re
import serial

from pathlib import Path
from rich.progress import track

from c2sync.logger import get_logger


PROMPT_REGEX = re.compile(r"[>#]\s?$")
LOGIN_PROMPT_REGEX = re.compile(r"[>#:]\s?$")


def discover_serial_ports() -> list[dict]:
    """Use pyudev to enumerate connected ttyUSB/ttyACM devices."""
    try:
        import pyudev
    except ImportError:
        raise RuntimeError("pyudev is not installed. Run: pip install pyudev")

    context = pyudev.Context()
    ports = []

    for device in context.list_devices(subsystem="tty"):
        dev_node = device.properties.get("DEVNAME")
        if not dev_node:
            continue
        if not re.match(r"tty(USB|ACM)\d*", Path(dev_node).name):
            continue
        if not Path(dev_node).exists():
            continue

        parts = []
        for key in ("ID_VENDOR", "ID_MODEL", "ID_SERIAL_SHORT"):
            val = device.properties.get(key)
            if val:
                parts.append(val.replace("_", " "))

        vendor = device.properties.get("ID_VENDOR", "")
        ports.append({
            "device": dev_node,
            "description": " – ".join(parts) if parts else "Serial device",
            "is_cisco": "cisco" in vendor.lower(),
        })

    return sorted(ports, key=lambda p: p["device"])


class SerialConnection:
    """
    Creates a serial interface to communicate with a device
    """
    def __init__(self, tty, baudrate=9600, login: bool = False):
        """Creates a serial interface to communicate with a device

        Args:
            tty (str): The TTY device to open a connection on
            baudrate (int, optional): The baudrate of the serial connection. Defaults to 9600.
            login (bool, optional): If the serial interface should try to log in immediately. Defaults to False.
        """
        if not Path(tty).exists():
            raise FileNotFoundError(f"TTY device not found: {tty}")

        self.port = tty
        self.baudrate = baudrate
        self.conn = serial.Serial(self.port, self.baudrate, timeout=1)
        self.log = get_logger()

        if login:
            self.login()

    def send(self, cmd):
        self.log.debug(f"Sending command '{cmd}'")
        self.conn.write((cmd + "\n").encode())

    def read_until_prompt(self, prompt=PROMPT_REGEX):
        buffer = ""
        while True:
            data = self.conn.read(1024).decode(errors="ignore")
            buffer += data
            if prompt.search(buffer):
                break
        return buffer

    def login(self):
        output = self.read_until_prompt(LOGIN_PROMPT_REGEX)

        if "Username:" in output:
            self.send(input("Username: "))
            output = self.read_until_prompt(LOGIN_PROMPT_REGEX)

        if "Password:" in output:
            self.send(getpass.getpass("Password: "))
            self.read_until_prompt()

    def send_command(self, cmd):
        self.send(cmd)
        return self.read_until_prompt()

    def enter_config_mode(self):
        self.send_command("configure terminal")

    def exit_config_mode(self):
        self.send_command("end")

    def send_config(self, commands: list[str]):
        """
        Sends the list of commands to te device

        Args:
            commands (list[str]): The list of commands to send
        """
        self.enter_config_mode()
        for cmd in track(commands, description="Sending commands..."):
            self.send_command(cmd)
        self.exit_config_mode()

    def get_running_config(self):
        """
        Returns the current running config in the device

        Returns:
            str: The running config
        """
        self.send_command("terminal length 0")
        return self.send_command("show running-config brief")
    
    def is_config_synced(self) -> bool:
        """
        Check if the running-config has been saved to startup-config.

        Returns:
            True if configs match, False otherwise
        """

        self.send_command("end")
        self.send_command("terminal length 0")

        output = self.send_command(
            "show archive config incremental-diffs nvram:startup-config"
        )

        return not any(
            line.startswith(("+", "-"))
            for line in output.splitlines()
        )