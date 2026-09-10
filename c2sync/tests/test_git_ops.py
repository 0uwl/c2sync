import pytest

from c2sync import git_ops


@pytest.fixture
def repo(tmp_path):
    project_dir = str(tmp_path)
    git_ops.init(project_dir)
    (tmp_path / 'device.config').write_text('interface Gi1/0/1\n')
    git_ops.commit(project_dir, ['device.config'], 'first commit')
    return project_dir


# ------------------------------------------------------------------
# resolve_rev
# ------------------------------------------------------------------

def test_resolve_rev_resolves_head_to_a_full_sha(repo):
    sha = git_ops.resolve_rev(repo, 'HEAD')

    assert len(sha) == 40
    assert all(c in '0123456789abcdef' for c in sha)


def test_resolve_rev_raises_on_unresolvable_rev(repo):
    with pytest.raises(git_ops.GitError):
        git_ops.resolve_rev(repo, 'not-a-real-commit')


# ------------------------------------------------------------------
# show_at
# ------------------------------------------------------------------

def test_show_at_returns_file_content_at_given_rev(repo):
    sha = git_ops.resolve_rev(repo, 'HEAD')

    assert git_ops.show_at(repo, sha, 'device.config') == 'interface Gi1/0/1\n'


def test_show_at_raises_rather_than_returning_empty_for_a_bad_rev(repo):
    """
    Critical for `c2sync revert`: a typo'd commit must never be silently
    treated as "target = empty config", which would try to strip the
    entire device configuration.
    """
    with pytest.raises(git_ops.GitError):
        git_ops.show_at(repo, 'not-a-real-commit', 'device.config')


def test_show_at_raises_when_file_not_tracked_at_rev(repo):
    sha = git_ops.resolve_rev(repo, 'HEAD')

    with pytest.raises(git_ops.GitError):
        git_ops.show_at(repo, sha, 'never-existed.config')


# ------------------------------------------------------------------
# show_at_head (soft-fallback wrapper)
# ------------------------------------------------------------------

def test_show_at_head_returns_content(repo):
    assert git_ops.show_at_head(repo, 'device.config') == 'interface Gi1/0/1\n'


def test_show_at_head_returns_none_when_file_not_tracked(repo):
    assert git_ops.show_at_head(repo, 'never-existed.config') is None


def test_show_at_head_returns_none_before_first_commit(tmp_path):
    project_dir = str(tmp_path)
    git_ops.init(project_dir)

    assert git_ops.show_at_head(project_dir, 'device.config') is None
