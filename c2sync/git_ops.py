from pathlib import Path
from git import Repo, InvalidGitRepositoryError, NoSuchPathError, GitCommandError

# ---------------------------
# Repo Management
# ---------------------------

def init_repo(path: str = ".") -> Repo:
    """
    Initialize a git repository if it does not exist.
    """
    repo_path = Path(path)

    try:
        repo = Repo(repo_path)
        return repo
    except (InvalidGitRepositoryError, NoSuchPathError):
        repo = Repo.init(repo_path)
        return repo


def get_repo(path: str = ".") -> Repo:
    """
    Get an existing repo or raise error if not initialized.
    """
    try:
        return Repo(path)
    except InvalidGitRepositoryError:
        raise Exception("Not a git repository. Run `c2sync init` first.")


def is_repo_initialized(path: str = ".") -> bool:
    try:
        Repo(path)
        return True
    except InvalidGitRepositoryError:
        return False


# ---------------------------
# Commit Operations
# ---------------------------

def commit_all(message: str, path: str = ".") -> None:
    """
    Stage all changes and commit.
    """
    repo = get_repo(path)

    repo.git.add(A=True)

    # Avoid empty commits
    if repo.is_dirty(untracked_files=True):
        repo.index.commit(message)
    else:
        # Optional: silently ignore or log
        pass


def commit_file(file_path: str, message: str) -> None:
    """
    Commit a specific file.
    """
    repo = get_repo()

    repo.git.add(file_path)

    if repo.is_dirty(untracked_files=True):
        repo.index.commit(message)


# ---------------------------
# Diff Operations
# ---------------------------

def get_diff(file_path: Path) -> list[str]:
    """
    Get git diff for a specific file (working tree vs index).
    """
    repo = get_repo()

    try:
        diff: str = repo.git.diff(str(file_path))
        return [
            line[1:]
            for line in diff.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        ]
    except GitCommandError as e:
        raise Exception(f"Failed to get diff: {e}")


def get_staged_diff(file_path: Path) -> str:
    """
    Get diff of staged changes.
    """
    repo = get_repo()

    try:
        return repo.git.diff("--cached", str(file_path))
    except GitCommandError as e:
        raise Exception(f"Failed to get staged diff: {e}")


def get_full_diff() -> str:
    """
    Get full repo diff.
    """
    repo = get_repo()
    return repo.git.diff()


# ---------------------------
# Status Helpers
# ---------------------------

def has_changes(path: str = ".") -> bool:
    """
    Check if repo has uncommitted changes.
    """
    repo = get_repo(path)
    return repo.is_dirty(untracked_files=True)


def file_has_changes(file_path: Path) -> bool:
    """
    Check if a specific file has changes.
    """
    diff = get_diff(file_path)
    return bool(diff)


# ---------------------------
# History / Info
# ---------------------------

def get_last_commit_message(path: str = ".") -> str:
    repo = get_repo(path)
    head_commit_message = str(repo.head.commit.message)
    return head_commit_message


def get_last_commit_hash(path: str = ".") -> str:
    repo = get_repo(path)
    return repo.head.commit.hexsha


def log(limit: int = 5):
    """
    Return last N commits.
    """
    repo = get_repo()

    commits = []
    for commit in repo.iter_commits(max_count=limit):
        commits.append({
            "hash": commit.hexsha[:7],
            "message": commit.message.strip(),
        })

    return commits


# ---------------------------
# Safety Utilities
# ---------------------------

def ensure_clean_working_tree():
    """
    Ensure no pending changes exist before critical operations.
    """
    if has_changes():
        raise Exception(
            "Working directory has uncommitted changes. Commit or stash before proceeding."
        )


def ensure_file_tracked(file_path: Path):
    """
    Ensure a file is tracked by git.
    """
    repo = get_repo()

    if str(file_path) not in repo.git.ls_files():
        raise Exception(f"{file_path} is not tracked by git.")