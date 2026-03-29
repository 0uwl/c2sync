import serial
import re

from rich.progress import track

from c2sync.logger import get_logger


PROMPT_REGEX = re.compile(r"[>#]\s?$")


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
        self.port = tty
        self.baudrate = baudrate
        self.conn = serial.Serial(self.port, self.baudrate, timeout=1)
        self.log = get_logger()

        # if login:
        #     self.login()

    def send(self, cmd):
        self.log.debug(f"Sending command '{cmd}'")
        # self.conn.write((cmd + "\n").encode())

    def read_until_prompt(self, prompt=PROMPT_REGEX):
        buffer = ""
        while True:
            data = self.conn.read(1024).decode(errors="ignore")
            buffer += data
            if prompt.search(buffer):
                break
        return buffer

    def login(self):
        output = self.read_until_prompt()

        if "Username:" in output:
            self.send(input("Username: "))
        if "Password:" in output:
            self.send(input("Password: "))

        self.read_until_prompt()

    def send_command(self, cmd):
        self.send(cmd)
        # return self.read_until_prompt()

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
        #.strip()

        # Empty output means no differences
        if not output:
            return True

        return False