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


def resolve_rev(project_dir: str, rev: str) -> str:
    """
    Resolve a rev-ish (HEAD, a short/long sha, a branch name, ...) to a
    full commit sha. Raises GitError with git's own message if it doesn't
    resolve to anything - e.g. a typo'd commit passed to `c2sync revert`.
    """
    return _run(project_dir, 'rev-parse', rev).strip()


def show_at(project_dir: str, rev: str, path: str) -> str:
    """
    Return a tracked file's content as of the given rev. Raises GitError if
    the rev doesn't resolve or the file isn't tracked there - this never
    silently substitutes an empty file for a bad rev, unlike show_at_head.
    Callers that want that soft fallback should use show_at_head instead;
    callers acting on a rev the user typed (e.g. `c2sync revert COMMIT`)
    should call this directly and surface the error.
    """
    return _run(project_dir, 'show', f'{rev}:{path}')


def show_at_head(project_dir: str, path: str) -> str | None:
    """
    Same as show_at(project_dir, 'HEAD', path), but returns None instead of
    raising when there's no commit yet (a brand new project) or the file
    isn't tracked at HEAD - the soft-fallback case every existing caller
    (Differ's baseline lookup, `discard`) wants.
    """
    try:
        return show_at(project_dir, 'HEAD', path)
    except GitError:
        return None
