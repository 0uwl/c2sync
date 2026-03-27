from c2sync import git_ops, serial_interface, diff_engine
from c2sync import state_engine, watcher, models

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.syntax import Syntax

from c2sync import git_ops, serial_interface, models, state_engine

console = Console()

@click.group()
def cli():
    """C2Sync - Console Configuration Synchronizer"""
    pass


@cli.command()
def init():
    """Initialize a C2Sync project"""
    models.init_project()
    git_ops.init_repo(".")

    console.print("[green]✔ Project initialized[/green]")


@cli.command()
@click.argument("device")
@click.argument("tty", required=False)
def pull(device, tty):
    """Pull running config from device"""

    dev = models.get_device(device, tty)

    console.print(f"[cyan]Connecting to {device}...[/cyan]")

    conn = serial_interface.SerialConnection(dev.tty)
    conn.login()

    config = conn.get_running_config()

    dev.save_config(config)

    git_ops.commit_all(f"pulled from {device}")

    console.print(f"[green]✔ Pulled config from {device}[/green]")


@cli.command()
@click.argument("device", required=False)
def status(device):
    """Show device status"""

    states = state_engine.get_status(device)

    table = Table(title="C2Sync Status")

    table.add_column("Device", style="cyan")
    table.add_column("State", style="magenta")

    for dev, state in states.items():
        color = {
            "SYNCED": "green",
            "HOST_PENDING": "yellow",
            "DEVICE_PENDING": "red",
        }
        color.get(state)

        table.add_row(dev, f"[{color}]{state}[/{color}]")

    console.print(table)


@cli.command()
@click.argument("device")
def diff(device):
    """Show staged CLI commands"""

    dev = models.get_device(device)
    commands = models.read_staging(dev)

    if not commands:
        console.print("[green]No staged changes[/green]")
        return

    syntax = Syntax(
        "\n".join(commands),
        "bash",
        theme="monokai",
        line_numbers=True
    )

    console.print(Panel(syntax, title=f"{device} - Staged Commands"))


@cli.command()
@click.argument("device")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation")
@click.option("-m", "--message", help="Commit message")
def sync(device, yes, message):
    """Push staged changes to device"""

    dev = models.get_device(device)
    commands = models.read_staging(dev)

    if not commands:
        console.print("[yellow]No changes to sync[/yellow]")
        return

    # Show preview
    syntax = Syntax("\n".join(commands), "bash", theme="monokai")
    console.print(Panel(syntax, title="Commands to Apply"))

    if not yes:
        if not click.confirm("Apply these changes?"):
            console.print("[red]Aborted[/red]")
            return

    conn = serial_interface.SerialConnection(dev.tty)
    conn.login()

    with console.status("[cyan]Applying configuration...[/cyan]"):
        conn.send_config(commands)

    git_ops.commit_all(message or f"sync {device}")

    console.print("[green]✔ Sync complete[/green]")

    # Re-pull updated config
    pull.callback(device, None)


@cli.command()
@click.argument("device")
@click.option("-y", "--yes", is_flag=True)
def apply(device, yes):
    """Save running-config to startup-config"""

    if not yes:
        if not click.confirm("Write memory to device?"):
            return

    dev = models.get_device(device)

    conn = serial_interface.SerialConnection(dev.tty)
    conn.login()

    conn.send_command("write memory")

    console.print(f"[green]✔ Configuration saved on {device}[/green]")


@cli.command()
@click.argument("device")
def commit(device):
    """Commit current state to git"""
    git_ops.commit_all(f"commit {device}")
    console.print(f"[green]✔ Commit created for {device}[/green]")


@cli.result_callback()
def handle_result(*args, **kwargs):
    pass


@cli.errorhandler(Exception)
def handle_error(e):
    console.print(f"[red]Error:[/red] {e}")


if __name__ == "__main__":
    cli()