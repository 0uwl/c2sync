import pytest
from c2sync.diff_engine import build_staging


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def lines(text: str) -> list[str]:
    """Split a multi-line string into a list, ignoring the leading newline."""
    return text.strip().splitlines()


# ---------------------------------------------------------------------------
# Empty / unchanged
# ---------------------------------------------------------------------------

def test_empty_diff_returns_empty_string():
    config = lines("""
        hostname Router1
        interface Gi1/0/1
         description test
    """)
    assert build_staging(config, config) == ""


def test_only_deletions_returns_empty_string():
    """Deleted lines are intentionally ignored by the tool."""
    old = lines("""
        hostname Router1
        interface Gi1/0/1
         description OLD
    """)
    new = lines("""
        hostname Router1
        interface Gi1/0/1
    """)
    assert build_staging(old, new) == ""


# ---------------------------------------------------------------------------
# Top-level (zero-indent) additions
# ---------------------------------------------------------------------------

def test_top_level_addition_has_no_context():
    old = ["hostname Router1"]
    new = ["hostname Router1", "ip domain-name example.com"]

    result = build_staging(old, new)

    assert result == "ip domain-name example.com"


def test_multiple_top_level_additions():
    old = ["hostname Router1"]
    new = ["hostname Router1", "ip domain-name example.com", "ip name-server 8.8.8.8"]

    result = build_staging(old, new)

    assert "ip domain-name example.com" in result
    assert "ip name-server 8.8.8.8" in result


# ---------------------------------------------------------------------------
# Indented (context-aware) additions — the core use case
# ---------------------------------------------------------------------------

def test_indented_addition_includes_parent_context():
    old = lines("""
        interface Gi1/0/1
         description OLD
    """)
    new = lines("""
        interface Gi1/0/1
         description OLD
         shutdown
    """)

    result = build_staging(old, new)
    result_lines = result.splitlines()

    assert result_lines[0] == "interface Gi1/0/1"
    assert "shutdown" in result_lines


def test_readme_example():
    """Exact scenario from the project README."""
    old = lines("""
        interface GigabitEthernet1/0/1
         description Server
         switchport mode access
         switchport access vlan 100
    """)
    new = lines("""
        interface GigabitEthernet1/0/1
         no description Server
         switchport mode access
         switchport access vlan 100
         switchport nonegotiate
    """)

    result = build_staging(old, new)
    result_lines = result.splitlines()

    assert result_lines[0] == "interface GigabitEthernet1/0/1"
    assert "no description Server" in result_lines
    assert "switchport nonegotiate" in result_lines
    assert "switchport mode access" not in result_lines
    assert "switchport access vlan 100" not in result_lines


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------

def test_multiple_additions_same_context_grouped():
    old = ["interface Gi1/0/1"]
    new = ["interface Gi1/0/1", " description test", " shutdown"]

    result = build_staging(old, new)
    result_lines = result.splitlines()

    assert result_lines.count("interface Gi1/0/1") == 1
    assert "description test" in result_lines
    assert "shutdown" in result_lines


def test_additions_in_different_contexts_both_present():
    old = lines("""
        interface Gi1/0/1
        interface Gi1/0/2
    """)
    new = lines("""
        interface Gi1/0/1
         shutdown
        interface Gi1/0/2
         shutdown
    """)

    result = build_staging(old, new)

    assert "interface Gi1/0/1" in result
    assert "interface Gi1/0/2" in result
    assert result.count("shutdown") == 2


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_old_file():
    new = ["hostname Router1"]
    result = build_staging([], new)
    assert "hostname Router1" in result


def test_empty_new_file_returns_empty_string():
    old = ["hostname Router1"]
    assert build_staging(old, []) == ""


def test_no_regression_on_unchanged_lines():
    """Lines present in both old and new must not appear in staging."""
    old = lines("""
        interface Gi1/0/1
         description unchanged
    """)
    new = lines("""
        interface Gi1/0/1
         description unchanged
         shutdown
    """)

    result = build_staging(old, new)

    assert "description unchanged" not in result
