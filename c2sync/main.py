import os

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.syntax import Syntax

from c2sync import git_ops, project_manager, serial_interface
from c2sync.logger import get_logger, setup_logging, set_log_context
from c2sync.models import Device

CONSOLE = Console()

@click.group()
def cli():
    """C2Sync - Console Configuration Synchronizer"""
    setup_logging()


@cli.command()
def init():
    """Initialize a C2Sync project"""
    result = project_manager.init_project()
    if not result:
        CONSOLE.print("\n[yellow]Project already initialized here[/yellow]\n")
        return
    git_ops.init_repo(".")

    log = set_log_context()

    log.info(f"New project initialized in {os.getcwd()}")
    CONSOLE.print("\n[green] New project initialized[/green]\n")


@cli.command()
@click.argument("device")
@click.argument("tty", required=False)
def pull(device_name: str, tty: str):
    """Pull running config from device"""

    log = set_log_context(device_name)
    device = project_manager.get_device(device_name, tty)

    log.info(f"Connecting to {device_name} over {tty}")
    CONSOLE.print(f"\n[cyan]Connecting to {device_name}...[/cyan]\n")

    fetch_config(device)

    log.debug("Committing to ")
    git_ops.commit_all(f"pulled from {device_name}")

    CONSOLE.print(f"\n[green]Pulled config from {device_name}[/green]\n")


@cli.command()
@click.argument("device", required=False)
def status(device_name: str = ''):
    """Show device status"""

    # Define output table
    table = Table(title="C2Sync Status")
    table.add_column("Device", style="cyan")
    table.add_column("State", style="magenta")
    state_color_mapping = {
        "SYNCED": "green",
        "HOST_PENDING": "yellow",
        "DEVICE_PENDING": "red",
    }

    # If a device name was passed...
    if device_name:
        # Fill query with only that device, if it exists
        device = project_manager.get_device(device_name)
        if device is None:
            raise LookupError(f"Device {device_name} not found")
        queries = [device]

    # Otherwise...
    else:
        # Fill query with all devices
        queries = project_manager.get_all_devices()
    
    log = set_log_context(device_name or '')

    log.info("Fetching status")

    # Add rows in the table for each query
    for device in queries:
        device_state = device.get_state()
        state_color_mapping.get(device_state)
        table.add_row(device.name, f"[{state_color_mapping}]{device_state}[/{state_color_mapping}]")

    CONSOLE.print(table)


@cli.command()
@click.argument("device")
def diff(device_name):
    """Show staged CLI commands"""

    device = project_manager.get_device(device_name)

    if device is None:
        raise LookupError(f"Device {device_name} not found")
    
    set_log_context(device_name)
    
    commands = project_manager.read_staging(device)

    if not commands:
        CONSOLE.print("[green]No staged changes[/green]")
        return

    syntax = Syntax(
        "\n".join(commands),
        "bash",
        theme="monokai",
        line_numbers=True
    )

    CONSOLE.print(Panel(syntax, title=f"{device_name} - Staged Commands"))


@cli.command()
@click.argument("device")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation")
@click.option("-m", "--message", help="Commit message")
def sync(device_name, yes, message):
    """Push staged changes to device"""

    device = project_manager.get_device(device_name)
    if not device:
        raise LookupError(f"Device {device_name} not found")

    set_log_context(device_name)

    commands = project_manager.read_staging(device)

    if not commands:
        CONSOLE.print("[yellow]No changes to sync[/yellow]")
        return

    # Show preview
    syntax = Syntax("\n".join(commands), "bash", theme="monokai")
    CONSOLE.print(Panel(syntax, title="Commands to Apply"))

    # Ask for confirmation if "yes"-option not given
    if not yes:
        if not click.confirm("Apply these changes?"):
            CONSOLE.print("[red]Aborted[/red]")
            return

    serial = serial_interface.SerialConnection(device.tty, login=True)

    with CONSOLE.status("[cyan]Applying configuration...[/cyan]"):
        serial.send_config(commands)

    # Re-pull updated config
    fetch_config(device)

    git_ops.commit_all(message or f"sync {device_name}")

    CONSOLE.print("[green] Sync complete[/green]")


@cli.command()
@click.argument("device")
@click.option("-y", "--yes", is_flag=True)
def commit(device_name, yes):
    """Save running-config to startup-config"""

    device = project_manager.get_device(device_name)
    if not device:
        raise LookupError(f"Device {device_name} not found")
    
    log = set_log_context(device_name)

    if not yes:
        if not click.confirm("Write memory to device?"):
            return

    serial = serial_interface.SerialConnection(device.tty, login=True)

    serial.send_command("write memory")

    log.info("Saved configuration on device")
    CONSOLE.print(f"[green] Configuration saved on {device_name}[/green]")


def fetch_config(device: Device):
    serial = serial_interface.SerialConnection(device.tty, login=True)
    log = get_logger()

    log.debug("Fetching running config")
    config = serial.get_running_config()

    log.debug(f"Saving config to file {device.config_path}")
    device.save_config(config)


def main():
    cli()