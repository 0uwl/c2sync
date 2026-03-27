import json
from pathlib import Path

C2SYNC_DIR = Path(".c2sync")
REGISTRY = C2SYNC_DIR / "register.json"

def init_project():
    C2SYNC_DIR.mkdir(exist_ok=True)
    if not REGISTRY.exists():
        REGISTRY.write_text(json.dumps({}))


def load_registry():
    return json.loads(REGISTRY.read_text())


def save_registry(data):
    REGISTRY.write_text(json.dumps(data, indent=2))


def get_device(name, tty=None):
    data = load_registry()

    if name not in data:
        if not tty:
            raise Exception("TTY required for first pull")
        data[name] = {"tty": tty}
        save_registry(data)

    return Device(name, data[name]["tty"])


class Device:
    def __init__(self, name, tty):
        self.name = name
        self.tty = tty
        self.config_path = Path(f".c2sync/{name}.config")
        self.staging_path = Path(f".c2sync/.{name}.staging")


    def save_config(self, config):
        self.config_path.write_text(config)


def read_staging(device):
    if not device.staging_path.exists():
        return []
    return device.staging_path.read_text().splitlines()