from pathlib import Path
from git import Repo, InvalidGitRepositoryError, NoSuchPathError

from typing import Optional


# ---------------------------
# Repo Management
# ---------------------------
REPO_PATH = Path(".")


def init_repo() -> Repo:
    """
    Initialize a git repository if it does not exist.
    """
    repo_path = Path(REPO_PATH)

    try:
        repo = Repo(repo_path)
        return repo
    except (InvalidGitRepositoryError, NoSuchPathError):
        repo = Repo.init(repo_path)
        return repo


def get_repo() -> Repo:
    """
    Get an existing repo or raise error if not initialized.
    """
    try:
        return Repo(REPO_PATH)
    except InvalidGitRepositoryError:
        raise Exception("Not a git repository. Run `c2sync init` first.")

# ---------------------------
# Commit Operations
# ---------------------------

def commit_all(message: str) -> None:
    """
    Stage all changes and commit.
    """
    repo = get_repo()

    repo.git.add(A=True)

    # Avoid empty commits
    if repo.is_dirty(untracked_files=True):
        repo.index.commit(message)
    else:
        # Optional: silently ignore or log
        pass

# ---------------------------
# Getters
# ---------------------------

def get_last_committed_version(file_path: Path) -> list[str]:
    repo = get_repo()
    blob = repo.head.commit.tree / str(file_path)
    return blob.data_stream.read().decode().splitlines()

# ---------------------------
# Status Helpers
# ---------------------------

def has_changes() -> bool:
    """
    Check if repo has uncommitted changes.
    """
    repo = get_repo()
    return repo.is_dirty(untracked_files=True)

# ---------------------------
# History / Info
# ---------------------------

def get_last_commit_message() -> str:
    repo = get_repo()
    head_commit_message = str(repo.head.commit.message)
    return head_commit_message


def get_last_commit_hash() -> str:
    repo = get_repo()
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


def ensure_file_tracked(, file_path: Path):
    """
    Ensure a file is tracked by git.
    """
    repo = get_repo()

    if str(file_path) not in repo.git.ls_files():
        raise Exception(f"{file_path} is not tracked by git.")
    

# ----------------------------
# File discovery
# ----------------------------

def get_tracked_config_files() -> list[str]:
    """
    Return tracked .config files (excluding .c2sync/)
    """
    files = []

    repo = get_repo()

    for (filepath, _stage) in repo.index.entries.keys():
        filepath = str(filepath)
        if filepath.endswith(".config") and not filepath.startswith(".c2sync/"):
            files.append(filepath)

    return files


def get_changed_config_files() -> list[str]:
    """
    Return only modified .config files (working tree vs index)
    """
    changed = []

    repo = get_repo()

    diffs = repo.index.diff(None)

    for diff in diffs:
        filepath = str(diff.a_path)
        if filepath.endswith(".config") and not filepath.startswith(".c2sync/"):
            changed.append(filepath)

    return changed


# ----------------------------
# File content access
# ----------------------------

def get_head_file(filepath: str) -> Optional[str]:
    """
    Get file content from HEAD commit
    """

    repo = get_repo()
    
    try:
        blob = repo.head.commit.tree / filepath
        return blob.data_stream.read().decode()
    except Exception:
        return None


def get_working_file(filepath: str) -> Optional[str]:
    """
    Get file content from working directory
    """
    full_path = REPO_PATH / filepath

    if not full_path.exists():
        return None

    return full_path.read_text()