import os
import sys
import tomllib
from pathlib import Path


def config_path() -> Path:
    """
    XDG Base Directory spec: $XDG_CONFIG_HOME/c2sync/config.toml, falling
    back to ~/.config/c2sync/config.toml when XDG_CONFIG_HOME isn't set.
    """
    base = os.environ.get('XDG_CONFIG_HOME') or str(Path.home() / '.config')
    return Path(base) / 'c2sync' / 'config.toml'


def load() -> dict:
    """
    Read the user's optional global preferences file. Returns {} if it
    doesn't exist - this file is entirely optional, every setting it can
    hold already has a working default.

    Only non-secret preferences belong here (username, init defaults).
    Passwords/enable-secrets are never read from this file - see
    main.py's _connect().
    """
    path = config_path()
    try:
        with open(path, 'rb') as file:
            return tomllib.load(file)
    except FileNotFoundError:
        return {}
    except tomllib.TOMLDecodeError as e:
        print(f'Could not parse {path}: {e}')
        sys.exit(1)
