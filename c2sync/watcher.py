import time

from c2sync import diff_engine, git_ops, project_manager
from c2sync.logger import get_logger
from c2sync.models import Device

def watch(device):
    dev = project_manager.get_device(device)

    last_mtime = 0

    while True:
        mtime = dev.config_path.stat().st_mtime

        if mtime != last_mtime:
            handle_change(dev)
            last_mtime = mtime

        time.sleep(1)


def handle_change(device: Device):
    diff = git_ops.get_diff(device.config_path)

    file_lines = device.config_path.read_text().splitlines()

    commands = diff_engine.recontextualize_lines(file_lines, diff)

    device.staging_path.write_text("\n".join(commands))