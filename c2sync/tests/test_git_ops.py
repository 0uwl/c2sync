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


# ------------------------------------------------------------------
# commit_content
# ------------------------------------------------------------------

def test_commit_content_advances_head_without_touching_the_working_tree(repo, tmp_path):
    """
    The whole point of commit_content: move the baseline to what the device
    actually has while the file the user is editing keeps their unpushed
    edits.
    """
    (tmp_path / 'device.config').write_text('my unpushed edits\n')

    changed = git_ops.commit_content(repo, 'device.config', 'live from device\n', 'partial push')

    assert changed is True
    assert git_ops.show_at_head(repo, 'device.config') == 'live from device\n'
    assert (tmp_path / 'device.config').read_text() == 'my unpushed edits\n'


def test_commit_content_returns_false_when_content_matches_head(repo):
    assert git_ops.commit_content(repo, 'device.config', 'interface Gi1/0/1\n', 'no-op') is False


def test_commit_content_preserves_other_tracked_files(repo, tmp_path):
    """
    The temp index is seeded from HEAD, so files commit_content isn't
    writing must survive into the new commit - losing .gitignore here would
    start tracking staging.txt/state.json.
    """
    (tmp_path / '.gitignore').write_text('staging.txt\n')
    git_ops.commit(repo, ['.gitignore'], 'add gitignore')

    git_ops.commit_content(repo, 'device.config', 'live from device\n', 'partial push')

    assert git_ops.show_at(repo, 'HEAD', '.gitignore') == 'staging.txt\n'


def test_commit_content_leaves_no_staged_change_behind(repo, tmp_path):
    """
    If the real index kept the pre-commit blob, a plain `git status` in the
    project dir would report a staged change the user never made.
    """
    (tmp_path / 'device.config').write_text('my unpushed edits\n')

    git_ops.commit_content(repo, 'device.config', 'live from device\n', 'partial push')

    status = git_ops._run(repo, 'status', '--porcelain')
    assert status.strip() == 'M device.config'


def test_commit_content_keeps_the_previous_commit_as_parent(repo):
    """
    Modeled on the rest of c2sync's git use: history is appended to, never
    rewritten, so a bad push stays visible in `git log`.
    """
    before = git_ops.resolve_rev(repo, 'HEAD')

    git_ops.commit_content(repo, 'device.config', 'live from device\n', 'partial push')

    assert git_ops.resolve_rev(repo, 'HEAD~1') == before
