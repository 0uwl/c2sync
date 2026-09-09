import pytest
import shutil
from pathlib import Path

from c2sync import init_project
from c2sync.differ import Differ
from c2sync.models import Addition, Command, CommandBlock

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
# Test: _extract_additions
# ------------------------------------------------------------------

def test_extract_additions_basic(differ):
    old = [
        "hostname Router1",
    ]

    new = [
        "hostname Router1",
        "interface Gi1/0/1",
    ]

    additions = differ._extract_additions(old, new)

    assert additions == [
        Addition(index=1, line="interface Gi1/0/1")
    ]


def test_extract_additions_ignores_empty_lines(differ):
    old = ["hostname Router1"]
    new = ["hostname Router1", "   "]

    additions = differ._extract_additions(old, new)

    assert additions == []


# ------------------------------------------------------------------
# Test: _build_commands
# ------------------------------------------------------------------

def test_build_commands_with_context(differ):
    new_lines = [
        "interface Gi1/0/1",
        " description test",
    ]

    additions = [
        Addition(index=1, line=" description test")
    ]

    commands = differ._build_commands(additions, new_lines)

    assert commands == [
        Command(
            context=["interface Gi1/0/1"],
            action="description test"
        )
    ]


def test_build_commands_without_context(differ):
    new_lines = [
        "hostname Router1",
    ]

    additions = [
        Addition(index=0, line="hostname Router1")
    ]

    commands = differ._build_commands(additions, new_lines)

    assert commands == [
        Command(
            context=[],
            action="hostname Router1"
        )
    ]


# ------------------------------------------------------------------
# Test: _group_commands
# ------------------------------------------------------------------

def test_group_commands_groups_same_context(differ):
    commands = [
        Command(["interface Gi1/0/1"], "desc A"),
        Command(["interface Gi1/0/1"], "shutdown"),
    ]

    blocks = differ._group_commands_by_context(commands)

    assert blocks == [
        CommandBlock(
            context=["interface Gi1/0/1"],
            actions=["desc A", "shutdown"]
        )
    ]


def test_group_commands_separates_different_contexts(differ):
    commands = [
        Command(["interface Gi1/0/1"], "desc A"),
        Command(["router ospf 1"], "network 10.0.0.0 0.0.0.255 area 0"),
    ]

    blocks = differ._group_commands_by_context(commands)

    assert len(blocks) == 2

    assert CommandBlock(
        context=["interface Gi1/0/1"],
        actions=["desc A"]
    ) in blocks

    assert CommandBlock(
        context=["router ospf 1"],
        actions=["network 10.0.0.0 0.0.0.255 area 0"]
    ) in blocks


# ------------------------------------------------------------------
# Test: End-to-End Pipeline
# ------------------------------------------------------------------

def test_build_command_blocks_end_to_end(differ):
    old = [
        "interface Gi1/0/1",
        " description old",
    ]

    new = [
        "interface Gi1/0/1",
        " description old",
        " shutdown",
    ]

    blocks = differ._build_command_blocks(old, new)

    assert blocks == [
        CommandBlock(
            context=["interface Gi1/0/1"],
            actions=["shutdown"]
        )
    ]


# ------------------------------------------------------------------
# Test: refresh_staging
# ------------------------------------------------------------------

def test_refresh_staging_writes_correct_output(differ):
    old = [
        "interface Gi1/0/1",
    ]

    new = [
        "interface Gi1/0/1",
        " shutdown",
    ]

    staged = differ.refresh_staging(old, new)

    assert staged is True

    content = Path(differ.staging_file).read_text().strip()

    assert content == "\n".join([
        "interface Gi1/0/1",
        "shutdown"
    ])


def test_refresh_staging_returns_false_when_nothing_changed(differ):
    same = ["interface Gi1/0/1"]

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
    old = ["interface Gi1/0/1"]

    differ.refresh_staging(old, ["interface Gi1/0/1", " shutdown"])
    differ.refresh_staging(old, ["interface Gi1/0/1", " description test"])

    content = Path(differ.staging_file).read_text()

    assert "shutdown" not in content
    assert "description test" in content


# ------------------------------------------------------------------
# Test: refresh_staging_from_files
# ------------------------------------------------------------------

def test_refresh_staging_from_files_reads_baseline_and_edit_file(tmp_path):
    from c2sync import Project

    project_dir = tmp_path / '.c2sync'
    project_dir.mkdir()

    baseline_file = project_dir / 'baseline.config'
    edit_file = project_dir / 'device.config'
    staging_file = project_dir / 'staging.txt'

    baseline_file.write_text("interface Gi1/0/1\n")
    edit_file.write_text("interface Gi1/0/1\n shutdown\n")
    staging_file.write_text("")

    project = Project(
        SERIAL_DEVICE='NOT USED',
        PROJECT_DIR=str(project_dir),
        BASELINE_FILE=str(baseline_file),
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