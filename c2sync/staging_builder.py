from pathlib import Path
from typing import Optional

from rich.console import Console

from c2sync import git_ops, project_manager
from c2sync.diff_engine import build_staging
from c2sync.models import Device

REPO_PATH = git_ops.REPO_PATH
CONSOLE = Console()


def _build_staging_content(config_file: Path) -> Optional[str]:
    """
    Build the cpmtemt for a staging file out of the given config file using Git operations

    Args:
        filepath (Path): Path of the config file to build staging from

    Returns:
        Optional[str]: Returns the staging content if a diff is found. If no diff is found, an empty string is returned.
                       If config file is not found, None is returned
    """
    old_file = git_ops.get_head_file(config_file)
    new_file = git_ops.get_working_file(config_file)

    if old_file is None or new_file is None:
        return None

    if old_file == new_file:
        return ""

    return build_staging(old_file.splitlines(), new_file.splitlines())


def write_device(device: Device):
    """
    Build and write staging file for a single device
    
    Args:
        device (Device): The device to build staged changes for
    """
    content = _build_staging_content(device.config_path)

    if content is None:
        CONSOLE.print(f"[yellow]Skipping {device.config_path} (missing file, have you pulled this device?)[/yellow]")
        return

    device.staging_path.write_text(content)

    if content == "":
        CONSOLE.print(f"[yellow]{device.config_path} unchanged[/yellow]")
    else:
        CONSOLE.print(f"[green]Staging updated for {device.config_path}[/green]")


def write_changed():
    """
    Build staging for all modified configs
    """
    files = git_ops.get_changed_config_files()

    for filepath in files:
        device_name = Path(filepath).stem
        try:
            device = project_manager.get_device(device_name)
        except ValueError:
            CONSOLE.print(f"[yellow]Skipping {filepath} (device '{device_name}' not in registry)[/yellow]")
            continue
        write_device(device)