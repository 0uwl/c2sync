import logging
import os
import subprocess
import tempfile

from c2sync.exceptions import C2SyncError

LOGGER = logging.getLogger(__name__)


class GitError(C2SyncError):
    """Raised when a git operation fails."""


def _run(project_dir: str, *args: str, stdin: str = None, env: dict = None) -> str:
    result = subprocess.run(
        ['git', '-C', project_dir, *args],
        capture_output=True, text=True, input=stdin,
        # env replaces the environment wholesale rather than extending it,
        # so callers passing one (commit_content's GIT_INDEX_FILE) would
        # otherwise lose PATH/HOME and git's own config discovery with it.
        env={**os.environ, **env} if env else None,
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


def commit_content(project_dir: str, path: str, content: str, message: str) -> bool:
    """
    Commit `content` as `path` without touching the working tree or the
    real index. Returns False without erroring if the resulting tree is
    identical to HEAD's, mirroring commit()'s "nothing changed" contract.

    This exists for advancing the baseline to a config the user is *not*
    editing - specifically `push`'s partial-push reconciliation, where
    HEAD must move to what the device actually has while EDIT_FILE keeps
    the user's unpushed edits. Doing that through `git add` would mean
    overwriting EDIT_FILE, committing, then writing the user's content
    back, leaving a window where a crash loses their work.

    Uses plumbing against a throwaway index seeded from HEAD, so every
    other tracked file is carried into the new commit unchanged and a
    concurrent `git add` in the project dir can't be disturbed by it.
    """
    blob = _run(project_dir, 'hash-object', '-w', '--stdin', stdin=content).strip()

    with tempfile.TemporaryDirectory() as tmpdir:
        index_env = {'GIT_INDEX_FILE': os.path.join(tmpdir, 'index')}
        _run(project_dir, 'read-tree', 'HEAD', env=index_env)
        _run(project_dir, 'update-index', '--add', '--cacheinfo', f'100644,{blob},{path}', env=index_env)
        tree = _run(project_dir, 'write-tree', env=index_env).strip()

    if tree == _run(project_dir, 'rev-parse', 'HEAD^{tree}').strip():
        return False

    head = _run(project_dir, 'rev-parse', 'HEAD').strip()
    new_commit = _run(project_dir, 'commit-tree', tree, '-p', head, '-m', message).strip()
    _run(project_dir, 'update-ref', '-m', message, 'HEAD', new_commit)

    # Point the *real* index at the new HEAD for this path, worktree
    # untouched. Without this the index still holds the pre-commit blob, so
    # a plain `git status` in the project dir reports a staged change the
    # user never made ("MM") - precisely the misleading state this whole
    # reconciliation exists to remove.
    _run(project_dir, 'reset', '-q', 'HEAD', '--', path)
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


def merge_file(
    base_content: str, ours_content: str, theirs_content: str,
    labels: tuple[str, str, str] = ('your edits', 'last baseline', 'device'),
) -> tuple[str, int]:
    """
    Three-way text merge via `git merge-file` - the same plumbing git uses to
    merge any text file, run here on three in-memory strings rather than
    files in a working tree. Used to reconcile local edits with an
    out-of-band device change (see push's drift handling in main.py).

    Returns (merged_content, conflict_count). 0 means a clean merge -
    merged_content is ready to use as-is. A positive count means
    merged_content contains that many git-style
    <<<<<<< / ||||||| / ======= / >>>>>>> blocks that need a human to resolve
    them - never something to guess at automatically, the same way git
    itself never picks a side on a real conflict.

    Deliberately does not go through _run(): git merge-file's exit code *is*
    the conflict count on a normal run, not a pass/fail signal, so _run()'s
    "nonzero exit -> raise GitError" would wrongly treat every conflict as a
    hard failure instead of the expected outcome it is.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        paths = {name: os.path.join(tmpdir, name) for name in ('ours', 'base', 'theirs')}
        for name, content in (('ours', ours_content), ('base', base_content), ('theirs', theirs_content)):
            with open(paths[name], 'w') as file:
                file.write(content)

        # -p: print the merge result instead of writing it back to the
        # 'ours' file, so this stays side-effect-free - those paths are
        # throwaway temp files, not anything in the actual project.
        # --diff3 includes the base content in a conflict block (the
        # '|||||||' section) alongside the two divergent sides, so a human
        # resolving it can see what actually changed on each side instead of
        # just the raw collision.
        result = subprocess.run(
            ['git', 'merge-file', '-p', '--diff3',
             '-L', labels[0], '-L', labels[1], '-L', labels[2],
             paths['ours'], paths['base'], paths['theirs']],
            capture_output=True, text=True,
        )

    if result.returncode < 0:
        raise GitError(result.stderr.strip())

    return result.stdout, result.returncode


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
