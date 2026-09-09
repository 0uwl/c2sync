# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What C2Sync is

A CLI tool that acts as a middleman between a Cisco IOS device (over console/serial)
and a local git repository: pull the running-config to a local text file, edit it in a
normal text editor, and push the diff back as CLI commands. Console-only is a
**deliberate** starting scope (see Roadmap below), not an oversight — SSH is a
designed-for future extension, not current work.

There is no lint/format tooling configured in this repo (no ruff/black/flake8 config)
— don't invent one.

## Commands

```bash
# Install (editable, with dependencies from pyproject.toml: netmiko, pytest)
pip install -e .

# Run the full test suite
pytest c2sync/tests -q

# Run a single test file / single test
pytest c2sync/tests/test_differ.py -q
pytest c2sync/tests/test_differ.py::test_refresh_staging_writes_correct_output -q

# Run the CLI locally (after install; requires a real or mocked serial device for
# anything beyond `init`/`status`/`discard`)
c2sync init /dev/ttyUSB0 [BAUDRATE]
c2sync status
c2sync sync [-y]
c2sync commit [-y]
c2sync discard
```

All tests are mocked at the Netmiko/`ConnectHandler` boundary — no real hardware or
serial port is needed to run the suite. `c2sync/tests/` has no `__init__.py`; pytest's
default rootdir insertion is what makes `from constants import PROJECT` work in test
files, not a package import.

## Architecture

### Project directory model

`c2sync init SERIAL_DEVICE [BAUDRATE]` creates `./.c2sync/` holding the entire state
for **one device** (there is currently no multi-device registry, despite `README.md`
describing "one project, many devices" — that's aspirational, not implemented; see
Docs drift below):

- `device.config` (`EDIT_FILE`) — what the user edits in their text editor. Tracked in
  a real git repo (`git init` inside `PROJECT_DIR` at `init` time) — the baseline is no
  longer a separate file, it's `device.config` as of git `HEAD` (see On-demand change
  detection below).
- `staging.txt` (`STAGING_FILE`) — the recomputed CLI commands to push, fully
  overwritten (not appended) on every check.
- `state.json` (`STATE_FILE`) — `{host_dirty, device_dirty}`, see State tracking below.
- `c2sync.config` (`CONFIG_FILE`) — the serialized `Project` dataclass (serial device
  path, baudrate, timeout, all the above paths).

`staging.txt`, `state.json`, and `c2sync.config` are local operational scratch, not
device config to review — `init` writes a `.gitignore` in `PROJECT_DIR` excluding all
three, so only `device.config` is ever committed.

All paths live on the `Project` dataclass in `c2sync/__init__.py`, which also has
`init_project()`/`get_project()`. `get_project()` reads `./.c2sync/c2sync.config`
relative to cwd — commands must be run from the project directory.

### Module map

| Module | Responsibility |
|---|---|
| `c2sync/__init__.py` | `Project` dataclass, `init_project`/`get_project` |
| `c2sync/connector.py` | `SerialInterface` — Netmiko `ConnectHandler` wrapper (serial transport only) |
| `c2sync/differ.py` | `Differ` — indentation-based diff → CLI command blocks |
| `c2sync/git_ops.py` | Thin `git` subprocess wrapper — `init`/`commit`/`commit_empty`/`show_at_head` |
| `c2sync/models.py` | `Addition`, `Command`, `CommandBlock` dataclasses used by `Differ` |
| `c2sync/state_engine.py` | `StateEngine` — `host_dirty`/`device_dirty` tracking |
| `c2sync/exceptions.py` | `C2SyncError`, `ConfigApplyError`, `ConfigSaveError` |
| `c2sync/main.py` | CLI entry point: `init` / `status` / `sync` / `commit` / `discard` |

### Diff → CLI command translation (`differ.py`)

Indentation-based, not a real parser — this is a known limitation, see Constraints
below. Pipeline in `Differ._build_command_blocks`:

1. `difflib.ndiff(baseline_lines, current_lines)` — only `"+ "` (added) lines are used;
   `"- "` (removed) lines are **discarded entirely**. This is why the tool requires
   typing `no <command>` to remove something instead of deleting the line — deletions
   are not detected as removals at all.
2. For each added line, walk upward through `current_lines` collecting every line whose
   indentation is strictly less than the current running minimum, stopping at column 0
   — this reconstructs the Cisco CLI context stack (e.g. `interface Gi1/0/1`) purely
   from leading-space counts.
3. Commands sharing the same reconstructed context are grouped into one `CommandBlock`
   so e.g. two edits under the same `interface` are sent together rather than
   re-entering the context twice.

Example: editing `description X` → `no description X` plus adding `switchport
nonegotiate` under `interface GigabitEthernet1/0/1` produces one `CommandBlock` with
that interface as context and both lines as actions, sent as one grouped push.

### On-demand change detection (no background watcher)

There is deliberately no file-watcher process. Change detection mirrors how `git
status`/`git diff` work — literally now, not just by analogy: `Differ.
refresh_staging_from_files()` (called at the top of both `status` and `sync` in
`main.py`) reads the baseline via `git_ops.show_at_head()` (`git show HEAD:device.
config` under the hood) and `EDIT_FILE` fresh every time, recomputes `STAGING_FILE`
from scratch, and returns whether anything is staged. There's no mtime/stat fast-path
the way git status has one — these config files are small enough that a full content
diff (plus one `git show` subprocess call) on every check is already cheap.

### State tracking (`state_engine.py`)

Two independent booleans, not a single enum — this matters because a discard should be
able to clear `host_dirty` without touching `device_dirty`, and vice versa:

- `host_dirty` — `EDIT_FILE` differs from `device.config` at git `HEAD` (unsynced local
  edits).
- `device_dirty` — a sync has been pushed and confirmed, but not yet saved to
  startup-config.

`StateEngine.state.label` computes a single display string (`host pending changes` >
`device pending changes` > `synced`) with `host_dirty` taking priority. Transitions are
only ever driven by **confirmed** outcomes from `connector.py` (a Netmiko-verified push
or save), never assumed on send — see next section.

### Device transport (`connector.py`)

`SerialInterface` wraps Netmiko's `ConnectHandler(device_type='cisco_ios',
serial_settings={...})` — Netmiko drives the actual pyserial transport, handles prompt
detection, paging, and AAA login during session setup. Two things this wrapper adds on
top of raw Netmiko:

- `apply_config()` passes an IOS `error_pattern` to `send_config_set()`, so a rejected
  command raises `ConfigApplyError` instead of being silently pushed with the rest of
  the batch (Netmiko does not raise on command errors by default without this).
- `save_config()` checks the device's response for IOS's `[OK]` marker before
  returning, raising `ConfigSaveError` if the save wasn't actually confirmed.

`main.py`'s `sync`/`commit` only advance state (clear staging, mark `device_dirty`,
commit the new baseline to git, mark `device_dirty` clean) after these confirmed
returns — never optimistically.

### CLI surface (`main.py`)

Actual commands: `init`, `status`, `sync`, `commit`, `discard`. `status` is read-only
(recomputes staging, prints state + preview, never connects to the device — this is the
`git status` analog). `sync` pushes, then re-fetches `show running-config brief`,
writes it to `EDIT_FILE`, and makes a real git commit in `PROJECT_DIR` (`git_ops.
commit()`) — advancing `HEAD` *is* advancing the baseline now. `commit` refuses to run
while `host_dirty` (would save unintended state to startup-config), saves
running→startup only when `device_dirty`, and records that milestone as an empty git
commit (`git_ops.commit_empty()`) since there's no file content to stage for it.
`discard` reverts `EDIT_FILE` to `device.config` at git `HEAD` (not just clearing
staging) so discarded edits can't get silently re-staged on the next check.

`init` also runs `git init -b main` in `PROJECT_DIR` and makes the first commit
(empty `device.config` + the `.gitignore`) — see Project directory model above.
`c2sync` intentionally does not wrap `git log`/`diff`/`branch`/PR review; the same
repo is a normal git repo the user can drive directly with `git` or push to
GitHub/GitLab for review.

`_connect()` in `main.py` is interactive-only (`input()`/`getpass.getpass()`) — there is
currently no non-interactive/CI credential path.

## Known constraints / simplifications

- No support for multi-line config blocks (banners, macros) — the differ has no concept
  of an opaque block, it's line-by-line.
- No automatic handling of deletions (see Diff → CLI translation above) — must type
  `no <command>` instead of deleting a line.
- Assumes Cisco IOS-style indentation; `device_type='cisco_ios'` is hardcoded.
- No rollback if command N of a multi-command batch is rejected after N-1 already
  landed on the device.
- No file locking on `state.json`/`staging.txt` — fine for one interactive CLI
  invocation at a time, not safe for concurrent access.
- No `pull` command exists — `init` only creates empty files (now git-committed empty),
  so there is currently no way to onboard an already-configured device.

## Roadmap and active design decisions

See `HANDOFF.md` for the full write-up. Priority order, user-approved:

1. **Real git integration — done.** `init_project` runs `git init -b main` in
   `PROJECT_DIR` and commits the initial empty `device.config`; `sync` commits the
   newly-pulled running-config after a confirmed push; `commit` (startup-config save)
   records an empty commit since there's no file diff for that event. `BASELINE_FILE`
   is gone — the baseline is `device.config` at git `HEAD`, read via `git_ops.
   show_at_head()`. Still a **thin wrapper**: `c2sync` does not reimplement
   `diff`/`log`/`branch`/PR review — the user's normal git tooling (and GitHub/GitLab
   for review) operates on the same repo in `PROJECT_DIR` directly. `git_ops.py` shells
   out to the `git` binary via `subprocess` rather than a `gitpython` dependency,
   consistent with the thin-wrapper decision. Not yet built: `pull` (still gap #2
   below — onboarding an already-configured device needs a real first commit of its
   actual config, not an empty one) and non-interactive credentials (gap #6, still
   blocked on `_connect()`'s `input()`/`getpass.getpass()`).
2. **A real config-tree parser, after git** — replace the indentation-walking in
   `differ.py` with **ciscoconfparse** (or `ciscoconfparse2`), which parses IOS-style
   config into a real parent/child tree. Not TextFSM — TextFSM parses flat command
   *output* (e.g. `show version`) via regex templates, it has no concept of
   hierarchical config structure. This should be what finally makes real deletion
   handling and multi-line blocks tractable.
3. **SSH as a second transport — designed for, not built yet.** Since `connector.py`
   already goes through Netmiko, SSH is close to swapping `serial_settings={...}` for
   `host=...` on the same `device_type`. Keep `differ.py`/`state_engine.py`/`main.py`
   transport-agnostic (they already operate on `Project` and CLI text, not on
   `SerialInterface` internals) so this stays cheap when it's actually prioritized.

## Docs drift to be aware of

`README.md` documents a CLI surface (`init, pull, sync, status, diff, commit`) and a
multi-device registry (`register.json`) that do not match the current implementation
(`init, status, sync, commit, discard`; single device per project; no registry file).
Treat `main.py` as ground truth for the actual CLI, not `README.md`, until the docs are
reconciled — which is intentionally deferred until after the Roadmap items above land
rather than chased as a moving target now.
