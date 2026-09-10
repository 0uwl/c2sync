from unittest.mock import MagicMock, patch

import paramiko
import pytest

from netmiko.exceptions import ConfigInvalidException, NetmikoTimeoutException

from c2sync import Project
from c2sync.connector import DeviceInterface
from c2sync.exceptions import ConfigApplyError, ConfigSaveError, HostKeyRejectedError

from constants import PROJECT


def _make_interface(mock_conn: MagicMock, project: Project = PROJECT) -> DeviceInterface:
    with patch('c2sync.connector.ConnectHandler', return_value=mock_conn):
        return DeviceInterface(project, username='admin', password='pw')


# ------------------------------------------------------------------
# transport selection
# ------------------------------------------------------------------

def test_serial_project_passes_serial_settings_to_connecthandler():
    serial_project = Project(TRANSPORT='serial', SERIAL_DEVICE='/dev/ttyUSB0', BAUDRATE=115200)

    with patch('c2sync.connector.ConnectHandler') as mock_handler:
        DeviceInterface(serial_project, username='admin', password='pw')

    kwargs = mock_handler.call_args.kwargs
    assert kwargs['serial_settings'] == {'port': '/dev/ttyUSB0', 'baudrate': 115200}
    assert 'host' not in kwargs
    # Netmiko selects the transport class from device_type alone - plain
    # 'cisco_ios' is the SSH driver and dies with "Either ip or host must be
    # set" on a serial project, serial_settings notwithstanding.
    assert kwargs['device_type'] == 'cisco_ios_serial'


def test_ssh_project_passes_host_and_port_to_connecthandler():
    ssh_project = Project(TRANSPORT='ssh', HOST='10.0.0.1', SSH_PORT=2222)

    with patch('c2sync.connector.ConnectHandler') as mock_handler:
        DeviceInterface(ssh_project, username='admin', password='pw')

    kwargs = mock_handler.call_args.kwargs
    assert kwargs['host'] == '10.0.0.1'
    assert kwargs['port'] == 2222
    assert 'serial_settings' not in kwargs
    assert kwargs['device_type'] == 'cisco_ios'


def test_ssh_project_verifies_host_keys_instead_of_trusting_any():
    """
    Netmiko/Paramiko default to silently trusting any SSH host key
    (AutoAddPolicy, nothing persisted) - a real MITM exposure. Lock in that
    we override this to the same known_hosts-verifying model a plain `ssh`
    client uses.
    """
    ssh_project = Project(TRANSPORT='ssh', HOST='10.0.0.1')

    with patch('c2sync.connector.ConnectHandler') as mock_handler:
        DeviceInterface(ssh_project, username='admin', password='pw')

    kwargs = mock_handler.call_args.kwargs
    assert kwargs['ssh_strict'] is True
    assert kwargs['system_host_keys'] is True


# ------------------------------------------------------------------
# unknown SSH host key handling
# ------------------------------------------------------------------

UNKNOWN_HOST_ERROR = NetmikoTimeoutException(
    "\nA paramiko SSHException occurred during connection creation:\n\n"
    "Server '10.0.0.1' not found in known_hosts\n\n"
)


def test_unknown_host_key_not_prompted_by_default():
    """
    prompt_for_unknown_hosts defaults to False - an unknown host key must
    fail closed (today's behavior), never silently trigger a prompt.
    """
    ssh_project = Project(TRANSPORT='ssh', HOST='10.0.0.1')

    with patch('c2sync.connector.ConnectHandler', side_effect=UNKNOWN_HOST_ERROR), \
         patch('c2sync.connector._trust_new_host_key') as mock_trust:
        with pytest.raises(NetmikoTimeoutException):
            DeviceInterface(ssh_project, username='admin', password='pw')

    mock_trust.assert_not_called()


def test_unknown_host_key_prompts_and_retries_when_enabled():
    ssh_project = Project(TRANSPORT='ssh', HOST='10.0.0.1')
    mock_conn = MagicMock()

    with patch('c2sync.connector.ConnectHandler', side_effect=[UNKNOWN_HOST_ERROR, mock_conn]) as mock_handler, \
         patch('c2sync.connector._trust_new_host_key') as mock_trust:
        interface = DeviceInterface(ssh_project, username='admin', password='pw', prompt_for_unknown_hosts=True)

    mock_trust.assert_called_once_with('10.0.0.1', 22)
    assert mock_handler.call_count == 2
    assert interface.conn is mock_conn


def test_unrelated_connection_failure_does_not_trigger_the_prompt():
    """
    Only RejectPolicy's specific "not found in known_hosts" message should
    ever trigger the trust flow - a different failure (bad password, TCP
    timeout, etc.) must not be misread as an unknown-host-key situation.
    """
    ssh_project = Project(TRANSPORT='ssh', HOST='10.0.0.1')
    other_error = NetmikoTimeoutException('TCP connection to device failed.')

    with patch('c2sync.connector.ConnectHandler', side_effect=other_error), \
         patch('c2sync.connector._trust_new_host_key') as mock_trust:
        with pytest.raises(NetmikoTimeoutException):
            DeviceInterface(ssh_project, username='admin', password='pw', prompt_for_unknown_hosts=True)

    mock_trust.assert_not_called()


def test_declining_the_host_key_prompt_raises_and_saves_nothing(tmp_path, monkeypatch):
    from c2sync.connector import _trust_new_host_key

    known_hosts = tmp_path / 'known_hosts'
    monkeypatch.setattr('c2sync.connector.KNOWN_HOSTS_PATH', str(known_hosts))

    fake_key = MagicMock()
    fake_key.get_name.return_value = 'ssh-ed25519'
    fake_key.asbytes.return_value = b'fake-key-bytes'

    mock_transport = MagicMock()
    mock_transport.get_remote_server_key.return_value = fake_key

    with patch('c2sync.connector.socket.create_connection'), \
         patch('c2sync.connector.paramiko.Transport', return_value=mock_transport), \
         patch('builtins.input', return_value='no'):
        with pytest.raises(HostKeyRejectedError):
            _trust_new_host_key('10.0.0.1', 22)

    assert not known_hosts.exists()


def test_accepting_the_host_key_prompt_saves_it_to_known_hosts(tmp_path, monkeypatch):
    from c2sync.connector import _trust_new_host_key

    known_hosts = tmp_path / 'known_hosts'
    monkeypatch.setattr('c2sync.connector.KNOWN_HOSTS_PATH', str(known_hosts))

    fake_key = MagicMock()
    fake_key.get_name.return_value = 'ssh-ed25519'
    fake_key.asbytes.return_value = b'fake-key-bytes'

    mock_transport = MagicMock()
    mock_transport.get_remote_server_key.return_value = fake_key

    with patch('c2sync.connector.socket.create_connection'), \
         patch('c2sync.connector.paramiko.Transport', return_value=mock_transport), \
         patch('builtins.input', return_value='yes'):
        _trust_new_host_key('10.0.0.1', 22)

    assert known_hosts.exists()
    assert '10.0.0.1' in known_hosts.read_text()


def test_accepting_the_prompt_preserves_existing_known_hosts_content(tmp_path, monkeypatch):
    """
    Regression test: an earlier version round-tripped the whole file
    through paramiko.HostKeys.load()/save(), which silently dropped every
    comment and blank line. Appending the new entry must leave existing
    content untouched.
    """
    from c2sync.connector import _trust_new_host_key

    known_hosts = tmp_path / 'known_hosts'
    known_hosts.write_text('# my personal hosts\nexisting.example ssh-ed25519 AAAAexisting\n\n# section 2\n')
    monkeypatch.setattr('c2sync.connector.KNOWN_HOSTS_PATH', str(known_hosts))

    fake_key = MagicMock()
    fake_key.get_name.return_value = 'ssh-ed25519'
    fake_key.asbytes.return_value = b'fake-key-bytes'
    fake_key.get_base64.return_value = 'AAAAnewkey'

    mock_transport = MagicMock()
    mock_transport.get_remote_server_key.return_value = fake_key

    with patch('c2sync.connector.socket.create_connection'), \
         patch('c2sync.connector.paramiko.Transport', return_value=mock_transport), \
         patch('builtins.input', return_value='yes'):
        _trust_new_host_key('10.0.0.1', 22)

    content = known_hosts.read_text()
    assert '# my personal hosts' in content
    assert 'existing.example ssh-ed25519 AAAAexisting' in content
    assert '# section 2' in content
    assert '10.0.0.1 ssh-ed25519 AAAAnewkey' in content


def test_accepting_the_prompt_does_not_read_existing_known_hosts(tmp_path, monkeypatch):
    """
    Regression test: an earlier version called paramiko.HostKeys.load() on
    the existing file, which raises uncaught on a real known_hosts file
    containing an @revoked/@cert-authority marker line. Appending never
    parses the existing file at all, so this can't happen.
    """
    from c2sync.connector import _trust_new_host_key

    known_hosts = tmp_path / 'known_hosts'
    known_hosts.write_text('@revoked * ssh-rsa this-is-not-valid-base64\n')
    monkeypatch.setattr('c2sync.connector.KNOWN_HOSTS_PATH', str(known_hosts))

    fake_key = MagicMock()
    fake_key.get_name.return_value = 'ssh-ed25519'
    fake_key.asbytes.return_value = b'fake-key-bytes'
    fake_key.get_base64.return_value = 'AAAAnewkey'

    mock_transport = MagicMock()
    mock_transport.get_remote_server_key.return_value = fake_key

    with patch('c2sync.connector.socket.create_connection'), \
         patch('c2sync.connector.paramiko.Transport', return_value=mock_transport), \
         patch('builtins.input', return_value='yes'):
        _trust_new_host_key('10.0.0.1', 22)

    assert '10.0.0.1 ssh-ed25519 AAAAnewkey' in known_hosts.read_text()


def test_handshake_failure_raises_host_key_rejected_error():
    """
    Regression test: paramiko.SSHException from a failed/stalled handshake
    (e.g. transport.start_client()) is not an OSError, so it needs its own
    except clause rather than slipping through as an unhandled traceback.
    """
    from c2sync.connector import _trust_new_host_key

    mock_transport = MagicMock()
    mock_transport.start_client.side_effect = paramiko.SSHException('negotiation failed')

    with patch('c2sync.connector.socket.create_connection'), \
         patch('c2sync.connector.paramiko.Transport', return_value=mock_transport):
        with pytest.raises(HostKeyRejectedError):
            _trust_new_host_key('10.0.0.1', 22)


def test_apply_config_returns_output_on_success():
    mock_conn = MagicMock()
    mock_conn.send_config_set.return_value = 'interface Gi1/0/1\n shutdown\nend'

    interface = _make_interface(mock_conn)
    output = interface.apply_config(['interface Gi1/0/1', ' shutdown'])

    assert output == 'interface Gi1/0/1\n shutdown\nend'
    mock_conn.send_config_set.assert_called_once()


def test_apply_config_raises_on_device_rejection():
    mock_conn = MagicMock()
    mock_conn.send_config_set.side_effect = ConfigInvalidException('bad command')

    interface = _make_interface(mock_conn)

    with pytest.raises(ConfigApplyError):
        interface.apply_config(['bogus command'])


def test_save_config_succeeds_on_ok_response():
    mock_conn = MagicMock()
    mock_conn.save_config.return_value = 'Building configuration...\n[OK]'

    interface = _make_interface(mock_conn)

    assert '[OK]' in interface.save_config()


def test_save_config_raises_when_unconfirmed():
    mock_conn = MagicMock()
    mock_conn.save_config.return_value = '% Command authorization failed'

    interface = _make_interface(mock_conn)

    with pytest.raises(ConfigSaveError):
        interface.save_config()


def test_initialize_session_enables_when_not_already_privileged():
    mock_conn = MagicMock()
    mock_conn.check_enable_mode.return_value = False

    interface = _make_interface(mock_conn)
    interface.initialize_session()

    mock_conn.enable.assert_called_once()


def test_initialize_session_skips_enable_when_already_privileged():
    mock_conn = MagicMock()
    mock_conn.check_enable_mode.return_value = True

    interface = _make_interface(mock_conn)
    interface.initialize_session()

    mock_conn.enable.assert_not_called()


def test_serial_project_reaches_netmikos_real_serial_driver():
    """
    The mocked transport tests above can't catch a device_type that Netmiko
    dispatches to the wrong driver - the mock accepts any kwargs. This one
    lets the real ConnectHandler run, stubbing only the part that would
    touch a physical port, so a serial project that resolves to the SSH
    class fails here (ValueError: "Either ip or host must be set") instead
    of only on real hardware.
    """
    serial_project = Project(TRANSPORT='serial', SERIAL_DEVICE='/dev/ttyUSB0', BAUDRATE=115200)

    # check_serial_port validates the path against the *test host's* real
    # comports, which has nothing to do with what we're asserting here.
    with patch('netmiko.base_connection.check_serial_port', side_effect=lambda p: p), \
         patch('netmiko.base_connection.BaseConnection._open'), \
         patch('netmiko.base_connection.BaseConnection.session_preparation'):
        interface = DeviceInterface(serial_project, username='admin', password='pw')

    assert type(interface.conn).__name__ == 'CiscoIosSerial'
