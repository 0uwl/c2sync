import pytest
import shutil
from pathlib import Path

from c2sync import init_project
from c2sync.diff_engine import Differ
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
# Test: save_to_staging
# ------------------------------------------------------------------

def test_save_to_staging_writes_correct_output(differ, tmp_path):
    old = [
        "interface Gi1/0/1",
    ]

    new = [
        "interface Gi1/0/1",
        " shutdown",
    ]

    differ.save_to_staging(old, new)

    staging_file = differ.staging_file

    content = Path(staging_file).read_text().strip()

    assert content == "\n".join([
        "interface Gi1/0/1",
        "shutdown"
    ])


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