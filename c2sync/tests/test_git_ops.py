from pathlib import Path
import pytest

from c2sync import git_ops


# ---------------------------------------------------------------------------
# init_repo
# ---------------------------------------------------------------------------

def test_init_repo_creates_git_directory(project_dir):
    git_ops.init_repo()
    assert (project_dir / ".git").is_dir()


def test_init_repo_is_idempotent(project_dir):
    git_ops.init_repo()
    git_ops.init_repo()  # must not raise


def test_get_repo_raises_outside_git_repo(project_dir):
    with pytest.raises(Exception, match="git repository"):
        git_ops.get_repo()


# ---------------------------------------------------------------------------
# commit_all
# ---------------------------------------------------------------------------

def test_commit_all_creates_commit(initialized_project):
    (initialized_project / "test.txt").write_text("hello")
    git_ops.commit_all("initial commit")
    repo = git_ops.get_repo()
    assert repo.head.commit.message.strip() == "initial commit"


def test_commit_all_skips_when_nothing_to_commit(initialized_project):
    (initialized_project / "test.txt").write_text("hello")
    git_ops.commit_all("first")
    git_ops.commit_all("should not be created")
    repo = git_ops.get_repo()
    assert repo.head.commit.message.strip() == "first"


def test_commit_all_stages_new_files(initialized_project):
    (initialized_project / "config.txt").write_text("router config")
    git_ops.commit_all("add config")
    repo = git_ops.get_repo()
    tracked = [item[0] for item in repo.index.entries.keys()]
    assert "config.txt" in tracked


def test_commit_all_stages_modified_files(initialized_project):
    f = initialized_project / "config.txt"
    f.write_text("v1")
    git_ops.commit_all("v1")
    f.write_text("v2")
    git_ops.commit_all("v2")
    repo = git_ops.get_repo()
    assert repo.head.commit.message.strip() == "v2"


# ---------------------------------------------------------------------------
# get_head_file
# ---------------------------------------------------------------------------

def test_get_head_file_returns_committed_content(initialized_project):
    path = Path("notes.txt")
    (initialized_project / "notes.txt").write_text("hello from git")
    git_ops.commit_all("add notes")
    assert git_ops.get_head_file(path) == "hello from git"


def test_get_head_file_returns_none_for_untracked_file(initialized_project):
    (initialized_project / "seed.txt").write_text("x")
    git_ops.commit_all("seed")
    result = git_ops.get_head_file(Path("nonexistent.txt"))
    assert result is None


def test_get_head_file_reflects_last_commit_not_working_tree(initialized_project):
    path = Path("config.txt")
    (initialized_project / "config.txt").write_text("committed content")
    git_ops.commit_all("commit")
    (initialized_project / "config.txt").write_text("modified in working tree")
    assert git_ops.get_head_file(path) == "committed content"


# ---------------------------------------------------------------------------
# get_working_file
# ---------------------------------------------------------------------------

def test_get_working_file_returns_current_content(initialized_project):
    path = Path("config.txt")
    (initialized_project / "config.txt").write_text("working copy")
    assert git_ops.get_working_file(path) == "working copy"


def test_get_working_file_returns_none_for_missing_file(initialized_project):
    assert git_ops.get_working_file(Path("nonexistent.txt")) is None


def test_get_working_file_reflects_uncommitted_edits(initialized_project):
    path = Path("config.txt")
    (initialized_project / "config.txt").write_text("v1")
    git_ops.commit_all("v1")
    (initialized_project / "config.txt").write_text("v2 edit")
    assert git_ops.get_working_file(path) == "v2 edit"


# ---------------------------------------------------------------------------
# has_changes / ensure_clean_working_tree
# ---------------------------------------------------------------------------

def test_has_changes_false_on_clean_repo(initialized_project):
    (initialized_project / "f.txt").write_text("a")
    git_ops.commit_all("init")
    assert git_ops.has_changes() is False


def test_has_changes_true_with_uncommitted_file(initialized_project):
    (initialized_project / "f.txt").write_text("a")
    assert git_ops.has_changes() is True
