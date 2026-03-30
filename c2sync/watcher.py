# c2sync/watcher.py

from pathlib import Path
from typing import Optional

from c2sync import git_ops
from c2sync.diff_engine import build_staging


class Watcher:
    """
    On-demand staging builder.

    Uses Git to:
    - Compare HEAD vs working tree
    - Generate CLI staging config
    """
    REPO_PATH = git_ops.REPO_PATH

    @classmethod
    def _write_staging(cls, device: str, content: str):
        staging_path = cls.REPO_PATH / ".c2sync" / f".{device}.staging"
        staging_path.parent.mkdir(exist_ok=True)
        staging_path.write_text(content)

    @classmethod
    def _build_staging_text(cls, filepath: str) -> Optional[str]:
        """
        Core diff logic
        """
        old_file = git_ops.get_head_file(filepath)
        new_file = git_ops.get_working_file(filepath)

        if old_file is None or new_file is None:
            return None

        if old_file == new_file:
            return ""
        
        staging_lines = build_staging(old_file.split(), new_file.split())

        return staging_lines

    @classmethod
    def build_for_device(cls, device: str):
        """
        Build staging file for a single device
        """
        filepath = f"{device}.config"

        result = cls._build_staging_text(filepath)

        if result is None:
            print(f"[WATCHER] Skipping {device} (missing file)")
            return

        cls._write_staging(device, result)

        if result == "":
            print(f"[WATCHER] {device} unchanged")
        else:
            print(f"[WATCHER] Staging updated for {device}")

    @classmethod
    def build_changed(cls):
        """
        Build staging for modified configs
        """
        files = git_ops.get_changed_config_files()

        for filepath in files:
            device = Path(filepath).stem
            cls.build_for_device(device)