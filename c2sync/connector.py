import logging
import re

from netmiko import ConnectHandler
from netmiko.exceptions import ConfigInvalidException

from c2sync import Project
from c2sync.exceptions import ConfigApplyError, ConfigSaveError

LOGGER = logging.getLogger(__name__)

# Standard Cisco IOS CLI error prefixes. Passed to Netmiko as error_pattern so
# a rejected command aborts the push immediately instead of being silently
# sent along with the rest of the staged changes.
IOS_ERROR_PATTERN = r'%\s*(?:Invalid input|Incomplete command|Ambiguous command|Unrecognized command)'

# "write memory" only ever reports success via this marker on IOS; anything
# else (a prompt for confirmation, a permission error, no response) means the
# device did not actually persist the config.
IOS_SAVE_SUCCESS_PATTERN = r'\[OK\]'


class DeviceInterface:
    """
    Connection to a network device, backed by Netmiko - console/serial or
    SSH depending on project.TRANSPORT.

    Netmiko's ConnectHandler drives the actual transport (serial or SSH)
    and handles prompt detection, paging, and AAA login as part of session
    setup either way; the only thing that differs between transports is
    which kwargs ConnectHandler needs.
    """

    def __init__(self, project: Project, username: str = None, password: str = None, secret: str = None) -> None:
        if project.TRANSPORT == 'ssh':
            # Netmiko/Paramiko default to trusting any host key on every
            # connection (AutoAddPolicy, nothing persisted) - that's a real
            # MITM exposure for device credentials. Verify against the
            # user's own ~/.ssh/known_hosts instead, the same trust model a
            # plain `ssh` client uses.
            transport_kwargs = {
                'host': project.HOST,
                'port': project.SSH_PORT,
                'ssh_strict': True,
                'system_host_keys': True,
            }
        else:
            transport_kwargs = {'serial_settings': {'port': project.SERIAL_DEVICE, 'baudrate': project.BAUDRATE}}

        self.conn = ConnectHandler(
            device_type='cisco_ios',
            timeout=project.TIMEOUT,
            username=username,
            password=password,
            secret=secret,
            **transport_kwargs,
        )


    def initialize_session(self):
        if not self.conn.check_enable_mode():
            self.conn.enable()


    def get_running_config(self) -> str:
        return self.conn.send_command('show running-config brief')


    def apply_config(self, lines: list[str]) -> str:
        """
        Push staged CLI lines to the device.

        Raises ConfigApplyError if the device rejects any command, so the
        caller never has to guess whether a push actually succeeded.
        """
        LOGGER.debug(f'Sending config lines: {lines}')
        try:
            return self.conn.send_config_set(lines, error_pattern=IOS_ERROR_PATTERN)
        except ConfigInvalidException as e:
            raise ConfigApplyError(str(e)) from e


    def sync_config(self, lines: list[str]) -> str:
        self.apply_config(lines)
        return self.get_running_config()


    def save_config(self) -> str:
        """
        Save running-config to startup-config.

        Raises ConfigSaveError unless the device's own response confirms
        the save actually happened.
        """
        output = self.conn.save_config()
        if not re.search(IOS_SAVE_SUCCESS_PATTERN, output):
            raise ConfigSaveError(f'Device did not confirm the save: {output!r}')
        return output


    def disconnect(self):
        self.conn.disconnect()
