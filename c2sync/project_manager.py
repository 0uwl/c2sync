import json
from pathlib import Path

from c2sync.models import Device

C2SYNC_DIR = Path(".c2sync/")
REGISTRY = C2SYNC_DIR / "register.json"


def init_project():
    C2SYNC_DIR.mkdir(exist_ok=True)
    if not C2SYNC_DIR.exists():
        raise Exception("Failed to create project")
    if not REGISTRY.exists():
        REGISTRY.write_text(json.dumps({}))
        return True
    else:
        return False


def load_registry() -> dict[str, Device]:
    """Load and deserialize the registry, returning a dict of {"name": Device}

    Returns:
        dict[str, Device]: The loaded registry
    """
    serialized_data: dict[str, dict[str, str]] = json.loads(REGISTRY.read_text())
    loaded_data: dict[str, Device] = {}

    for device in serialized_data:
        name = serialized_data[device].get("name")
        tty = serialized_data[device].get("tty")
        if name is None or tty is None:
            raise ValueError("Missing data in registry")

        loaded_data[device] = Device(name, tty)

    return loaded_data


def save_registry(device_data: dict[str, Device]):
    """
    Convert the Device class to a dict and save it to the registry
    """
    serialized_data: dict[str, dict[str, str]] = {}

    for device in device_data:
        serialized_data[device] = device_data[device].to_dict()

    REGISTRY.write_text(json.dumps(serialized_data, indent=2))


def get_device(name, tty: str='') -> Device:
    """Get the device of the given name. If the device is not found, a new device will be added to the registry

    Args:
        name (str): Name of the queried device
        tty (str, optional): The TTY device used to communicate with the device. 
                             Only needed if this is the first time getting the device. Defaults to ''.

    Returns:
        Device: _description_
    """
    device_registry = load_registry()

    # If the device name isn't in the registry we create a new entry
    if name not in device_registry.keys:
        if not tty:
            raise ValueError("TTY_DEVICE required for first pull")
        _create_device(name, tty)

    return device_registry[name]


def get_all_devices() -> list[Device]:
    device_registry = load_registry()

    return [device for device in device_registry.values()]


def _create_device(name: str, tty: str) -> Device:
    """Create a new device to be added to the registry

    Args:
        name (str): Name of the device
        tty (str): TTY device used to comminicate with the device

    Raises:
        ValueError: Raises ValueError if TTY is not given

    Returns:
        Device: The newly created device
    """
    # The TTY device must be defined on the first pull
    
    device_registry = load_registry()

    # Set the tty to the new device
    new_device = Device(name, tty)
    device_registry[name] = new_device

    # Save the new registry
    save_registry(device_registry)

    return new_device


def read_staging(device: Device) -> list[str]:
    if not device.staging_path.exists():
        return []
    return device.staging_path.read_text().splitlines()