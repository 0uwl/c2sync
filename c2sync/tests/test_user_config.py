import pytest

from c2sync import user_config


def test_config_path_respects_xdg_config_home(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))

    assert user_config.config_path() == tmp_path / 'c2sync' / 'config.toml'


def test_config_path_falls_back_to_home_dot_config(monkeypatch):
    monkeypatch.delenv('XDG_CONFIG_HOME', raising=False)
    monkeypatch.setattr(user_config.Path, 'home', lambda: user_config.Path('/home/testuser'))

    assert user_config.config_path() == user_config.Path('/home/testuser/.config/c2sync/config.toml')


def test_load_returns_empty_dict_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))

    assert user_config.load() == {}


def test_load_reads_toml_file(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    config_dir = tmp_path / 'c2sync'
    config_dir.mkdir()
    (config_dir / 'config.toml').write_text('username = "admin"\nbaudrate = 115200\n')

    assert user_config.load() == {'username': 'admin', 'baudrate': 115200}


def test_load_exits_on_invalid_toml(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    config_dir = tmp_path / 'c2sync'
    config_dir.mkdir()
    (config_dir / 'config.toml').write_text('not valid toml {{{')

    with pytest.raises(SystemExit):
        user_config.load()
