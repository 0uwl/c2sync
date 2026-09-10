import base64
import hashlib
import logging
import os
import re
import socket

import paramiko
from netmiko import ConnectHandler
from netmiko.exceptions import ConfigInvalidException, NetmikoTimeoutException

from c2sync import Project
from c2sync.exceptions import ConfigApplyError, ConfigSaveError, HostKeyRejectedError

LOGGER = logging.getLogger(__name__)

# Standard Cisco IOS CLI error prefixes. Passed to Netmiko as error_pattern so
# a rejected command aborts the push immediately instead of being silently
# sent along with the rest of the staged changes.
IOS_ERROR_PATTERN = r'%\s*(?:Invalid input|Incomplete command|Ambiguous command|Unrecognized command)'

# "write memory" only ever reports success via this marker on IOS; anything
# else (a prompt for confirmation, a permission error, no response) means the
# device did not actually persist the config.
IOS_SAVE_SUCCESS_PATTERN = r'\[OK\]'

KNOWN_HOSTS_PATH = os.path.expanduser('~/.ssh/known_hosts')

# Netmiko wraps every paramiko.SSHException (including RejectPolicy's) into
# NetmikoTimeoutException. This is the exact substring RejectPolicy uses for
# a host it has genuinely never seen before (straight from paramiko's own
# RejectPolicy.missing_host_key()). A *changed* key raises BadHostKeyException
# instead, with different text - so this check can't misfire on an actual
# MITM/rotated-key scenario, only on genuinely new hosts.
UNKNOWN_HOST_KEY_MARKER = 'not found in known_hosts'


class DeviceInterface:
    """
    Connection to a network device, backed by Netmiko - console/serial or
    SSH depending on project.TRANSPORT.

    Netmiko's ConnectHandler drives the actual transport (serial or SSH)
    and handles prompt detection, paging, and AAA login as part of session
    setup either way; the only thing that differs between transports is
    which kwargs ConnectHandler needs.
    """

    def __init__(
        self,
        project: Project,
        username: str = None,
        password: str = None,
        secret: str = None,
        prompt_for_unknown_hosts: bool = False,
    ) -> None:
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

        connect_kwargs = dict(
            device_type='cisco_ios',
            timeout=project.TIMEOUT,
            username=username,
            password=password,
            secret=secret,
            **transport_kwargs,
        )

        if project.TRANSPORT == 'ssh' and prompt_for_unknown_hosts:
            try:
                self.conn = ConnectHandler(**connect_kwargs)
            except NetmikoTimeoutException as e:
                if UNKNOWN_HOST_KEY_MARKER not in str(e):
                    raise
                _trust_new_host_key(project.HOST, project.SSH_PORT)
                self.conn = ConnectHandler(**connect_kwargs)
        else:
            self.conn = ConnectHandler(**connect_kwargs)


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


def _trust_new_host_key(host: str, port: int) -> None:
    """
    OpenSSH-style first-connection prompt for a genuinely unknown SSH host:
    fetch its actual key at the transport level only (no authentication),
    show the fingerprint, and add it to ~/.ssh/known_hosts only if the user
    explicitly confirms. Never called for a *changed* key - paramiko raises
    a different exception for that case, left to fail hard rather than
    ever being auto-trusted.
    """
    try:
        with socket.create_connection((host, port), timeout=10) as sock:
            transport = paramiko.Transport(sock)
            try:
                transport.start_client(timeout=10)
                server_key = transport.get_remote_server_key()
            finally:
                transport.close()
    except (OSError, paramiko.SSHException) as e:
        raise HostKeyRejectedError(f'Could not reach {host}:{port} to verify its host key: {e}') from e

    fingerprint = base64.b64encode(hashlib.sha256(server_key.asbytes()).digest()).decode().rstrip('=')

    print(f"The authenticity of host '{host}' can't be established.")
    print(f'{server_key.get_name()} key fingerprint is SHA256:{fingerprint}.')
    answer = input('Are you sure you want to continue connecting (yes/no)? ').strip().lower()

    if answer != 'yes':
        raise HostKeyRejectedError(f'Host key for {host!r} was not trusted.')

    # Append rather than load()+add()+save(): this only ever runs once
    # paramiko has already told us the host has no known_hosts entry, so
    # there's nothing to merge. Appending also means never parsing the
    # user's existing file, which sidesteps two real problems with the
    # load/save round-trip: it drops every comment/blank line on save,
    # and load() raises uncaught on marker-prefixed lines (@revoked,
    # @cert-authority) that a real known_hosts file can legitimately have.
    server_hostkey_name = host if port == 22 else f'[{host}]:{port}'
    line = paramiko.hostkeys.HostKeyEntry(hostnames=[server_hostkey_name], key=server_key).to_line()

    os.makedirs(os.path.dirname(KNOWN_HOSTS_PATH), exist_ok=True)
    with open(KNOWN_HOSTS_PATH, 'a') as file:
        file.write(line)
    print(f"Warning: Permanently added '{host}' ({server_key.get_name()}) to the list of known hosts.")
