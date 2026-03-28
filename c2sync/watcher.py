import time
from pathlib import Path
from c2sync import diff_engine, git_ops, project_manager


def watch(device):
    dev = project_manager.get_device(device)

    last_mtime = 0

    while True:
        mtime = dev.config_path.stat().st_mtime

        if mtime != last_mtime:
            handle_change(dev)
            last_mtime = mtime

        time.sleep(1)


def handle_change(device):
    diff = git_ops.get_diff(device.config_path)

    changed = diff_engine.parse_diff(diff.splitlines())

    file_lines = device.config_path.read_text().splitlines()

    commands = diff_engine.reconstruct(file_lines, changed)

    device.staging_path.write_text("\n".join(commands))