import logging

from netmiko import ConnectHandler

from c2sync import Project
from c2sync.models import CommandBlock

LOGGER = logging.getLogger(__name__)


class SerialInterface:
    """
    Console/serial connection to a network device, backed by Netmiko.

    Netmiko's ConnectHandler drives the serial port directly (via its
    `serial_settings` transport) and handles prompt detection, paging,
    and AAA login as part of session setup.
    """

    def __init__(self, project: Project, username: str = None, password: str = None, secret: str = None) -> None:
        self.conn = ConnectHandler(
            device_type='cisco_ios',
            serial_settings={
                'port': project.SERIAL_DEVICE,
                'baudrate': project.BAUDRATE,
            },
            timeout=project.TIMEOUT,
            username=username,
            password=password,
            secret=secret,
        )


    def initialize_session(self):
        if not self.conn.check_enable_mode():
            self.conn.enable()


    def get_running_config(self) -> str:
        return self.conn.send_command('show running-config brief')


    def apply_config_blocks(self, blocks: list[CommandBlock]) -> str:
        lines = [line for block in blocks for line in block.to_lines()]
        LOGGER.debug(f'Sending config lines: {lines}')
        return self.conn.send_config_set(lines)


    def sync_config(self, blocks: list[CommandBlock]) -> str:
        self.apply_config_blocks(blocks)
        return self.get_running_config()


    def save_config(self) -> str:
        return self.conn.save_config()


    def disconnect(self):
        self.conn.disconnect()
