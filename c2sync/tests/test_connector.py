from unittest.mock import MagicMock, patch

import pytest

from netmiko.exceptions import ConfigInvalidException

from c2sync.connector import SerialInterface
from c2sync.exceptions import ConfigApplyError, ConfigSaveError

from constants import PROJECT


def _make_interface(mock_conn: MagicMock) -> SerialInterface:
    with patch('c2sync.connector.ConnectHandler', return_value=mock_conn):
        return SerialInterface(PROJECT, username='admin', password='pw')


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
