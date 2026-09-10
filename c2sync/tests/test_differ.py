import pytest
from pathlib import Path

from c2sync.differ import Differ

from constants import PROJECT

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture
def differ():
    """
    Provide a Differ instance with a temporary staging file.
    """
    return Differ(PROJECT)


# ------------------------------------------------------------------
# Test: refresh_staging
# ------------------------------------------------------------------

def test_refresh_staging_stages_additions_with_context(differ):
    old = "interface Gi1/0/1\n"
    new = "interface Gi1/0/1\n shutdown\n"

    staged = differ.refresh_staging(old, new)

    assert staged is True
    content = Path(differ.staging_file).read_text()
    assert "interface Gi1/0/1" in content
    assert "shutdown" in content


def test_refresh_staging_returns_false_when_nothing_changed(differ):
    same = "interface Gi1/0/1\n description X\n"

    staged = differ.refresh_staging(same, same)

    assert staged is False
    assert Path(differ.staging_file).read_text() == ""


def test_refresh_staging_overwrites_rather_than_appends(differ):
    """
    Unlike the old watcher-driven save_to_staging, refresh_staging always
    recomputes from a fixed baseline - so re-running it (e.g. after the
    user keeps editing) replaces the previous result instead of piling on
    top of it.
    """
    old = "interface Gi1/0/1\n"

    differ.refresh_staging(old, "interface Gi1/0/1\n shutdown\n")
    differ.refresh_staging(old, "interface Gi1/0/1\n description test\n")

    content = Path(differ.staging_file).read_text()

    assert "shutdown" not in content
    assert "description test" in content


def test_refresh_staging_stages_a_real_no_command_for_deleted_lines(differ):
    """
    The headline reason for switching to ciscoconfparse2: a line that's
    just deleted (not manually replaced with `no ...`) now produces a real
    negation command, instead of being silently dropped.
    """
    old = "interface Gi1/0/1\n description Server\n switchport mode access\n"
    new = "interface Gi1/0/1\n switchport mode access\n"

    staged = differ.refresh_staging(old, new)

    assert staged is True
    content = Path(differ.staging_file).read_text()
    assert "no description Server" in content


def test_refresh_staging_ignores_unchanged_multiline_block(differ):
    """
    Multi-line blocks (banners, macros) are treated as opaque - an
    unrelated change elsewhere doesn't cause the block to be re-diffed
    line by line.
    """
    banner = "banner motd ^C\nWelcome\n^C\n"
    old = banner + "interface Gi1/0/1\n description A\n"
    new = banner + "interface Gi1/0/1\n description A2\n"

    staged = differ.refresh_staging(old, new)

    assert staged is True
    content = Path(differ.staging_file).read_text()
    assert "banner" not in content
    assert "description A2" in content


# ------------------------------------------------------------------
# Test: diff_lines
# ------------------------------------------------------------------

def test_diff_lines_returns_commands_without_touching_staging_file(differ):
    """
    `c2sync revert` uses diff_lines directly (live device config -> a past
    commit) without going through the staging file, since a revert isn't a
    pending local edit - it's an immediate corrective push.
    """
    before = Path(differ.staging_file).read_text()

    lines = Differ.diff_lines("interface Gi1/0/1\n", "interface Gi1/0/1\n shutdown\n")

    assert any("shutdown" in line for line in lines)
    assert Path(differ.staging_file).read_text() == before


# ------------------------------------------------------------------
# Test: refresh_staging_from_files
# ------------------------------------------------------------------

def test_refresh_staging_from_files_reads_baseline_and_edit_file(tmp_path):
    from c2sync import Project, git_ops

    project_dir = tmp_path / '.c2sync'
    project_dir.mkdir()

    edit_file = project_dir / 'device.config'
    staging_file = project_dir / 'staging.txt'

    edit_file.write_text("interface Gi1/0/1\n")
    staging_file.write_text("")

    git_ops.init(str(project_dir))
    git_ops.commit(str(project_dir), ['device.config'], 'baseline')

    edit_file.write_text("interface Gi1/0/1\n shutdown\n")

    project = Project(
        SERIAL_DEVICE='NOT USED',
        PROJECT_DIR=str(project_dir),
        EDIT_FILE=str(edit_file),
        STAGING_FILE=str(staging_file),
    )

    staged = Differ(project).refresh_staging_from_files()

    assert staged is True
    assert "shutdown" in staging_file.read_text()


# ------------------------------------------------------------------
# Test: clear_staging
# ------------------------------------------------------------------

def test_clear_staging(differ):
    # Write something first
    with open(differ.staging_file, "w") as f:
        f.write("test")

    differ.clear_staging()

    with open(differ.staging_file, "r") as f:
        content = f.read()

    assert content == ""
