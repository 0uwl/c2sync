import logging
import subprocess

from c2sync.exceptions import C2SyncError

LOGGER = logging.getLogger(__name__)


class GitError(C2SyncError):
    """Raised when a git operation fails."""


def _run(project_dir: str, *args: str) -> str:
    result = subprocess.run(
        ['git', '-C', project_dir, *args],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise GitError(result.stderr.strip())
    return result.stdout


def init(project_dir: str) -> None:
    _run(project_dir, 'init', '-q', '-b', 'main')


def commit(project_dir: str, paths: list[str], message: str) -> bool:
    """
    Stage and commit the given paths (relative to project_dir). Returns
    False without erroring if nothing actually changed.
    """
    _run(project_dir, 'add', *paths)

    if not _run(project_dir, 'status', '--porcelain', *paths).strip():
        return False

    _run(project_dir, 'commit', '-q', '-m', message)
    return True


def commit_empty(project_dir: str, message: str) -> None:
    """
    Record a lifecycle event (e.g. startup-config save) that has no file
    content to stage, so it still shows up in `git log`.
    """
    _run(project_dir, 'commit', '-q', '--allow-empty', '-m', message)


def show_at_head(project_dir: str, path: str) -> str | None:
    """
    Return a tracked file's content as of HEAD, or None if there's no
    commit yet or the file isn't tracked at HEAD.
    """
    try:
        return _run(project_dir, 'show', f'HEAD:{path}')
    except GitError:
        return None
