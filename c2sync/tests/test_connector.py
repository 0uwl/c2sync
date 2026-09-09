from unittest.mock import MagicMock, patch

import pytest

from netmiko.exceptions import ConfigInvalidException

from c2sync import Project
from c2sync.connector import DeviceInterface
from c2sync.exceptions import ConfigApplyError, ConfigSaveError

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


def test_ssh_project_passes_host_and_port_to_connecthandler():
    ssh_project = Project(TRANSPORT='ssh', HOST='10.0.0.1', SSH_PORT=2222)

    with patch('c2sync.connector.ConnectHandler') as mock_handler:
        DeviceInterface(ssh_project, username='admin', password='pw')

    kwargs = mock_handler.call_args.kwargs
    assert kwargs['host'] == '10.0.0.1'
    assert kwargs['port'] == 2222
    assert 'serial_settings' not in kwargs


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
