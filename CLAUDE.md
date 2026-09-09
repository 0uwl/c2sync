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

Requires Python >=3.11 (for stdlib `tomllib`, used to read the global config file).

```bash
# Install (editable, with dependencies from pyproject.toml: netmiko, pytest)
pip install -e .

# Run the full test suite
pytest c2sync/tests -q

# Run a single test file / single test
pytest c2sync/tests/test_differ.py -q
pytest c2sync/tests/test_differ.py::test_refresh_staging_stages_additions_with_context -q

# Run the CLI locally (after install; requires a real or mocked serial device for
# anything beyond `init`/`status`/`discard`)
c2sync init /dev/ttyUSB0 [BAUDRATE]
c2sync pull [-y]
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
for **one device** — there is currently no multi-device registry (see the explicit
non-goal in `HANDOFF.md`):

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
| `c2sync/differ.py` | `Differ` — real config-tree diff (`ciscoconfparse2`) → CLI commands |
| `c2sync/git_ops.py` | Thin `git` subprocess wrapper — `init`/`commit`/`commit_empty`/`show_at_head` |
| `c2sync/user_config.py` | Reads the optional global TOML preferences file — `load()`/`config_path()` |
| `c2sync/state_engine.py` | `StateEngine` — `host_dirty`/`device_dirty` tracking |
| `c2sync/exceptions.py` | `C2SyncError`, `ConfigApplyError`, `ConfigSaveError` |
| `c2sync/main.py` | CLI entry point: `init` / `pull` / `status` / `sync` / `commit` / `discard` |

### Diff → CLI command translation (`differ.py`)

A real config-tree parser, not indentation-counting — `Differ.refresh_staging()` is a
thin wrapper around `ciscoconfparse2.Diff(baseline_config, current_config,
syntax='ios').get_diff()`. `Diff` parses both configs into a real parent/child tree
(via `ciscoconfparse2`'s vendored `hier_config`) and returns the exact CLI lines needed
to turn the baseline into the current config, context headers included, ready to write
straight to `STAGING_FILE` — there's no `Addition`/`Command`/`CommandBlock` modeling of
our own anymore (that whole layer, and `models.py`, is gone).

This is what makes two things work for free, not just as future work:
- **Real deletions.** A line that's just removed from the file (not manually replaced
  with `no <command>`) now produces an actual negation command — `hier_config` diffs
  the parsed trees, not raw text, so a missing child under an unchanged parent is a
  removal it detects on its own.
- **Multi-line blocks.** Banners/macros are parsed as opaque blocks; an unrelated
  change elsewhere in the config doesn't cause them to be re-diffed line by line.

Example: deleting the `description Server` line and adding `switchport nonegotiate`
under `interface GigabitEthernet1/0/1` produces `interface GigabitEthernet1/0/1` /
`no description Server` / `switchport nonegotiate` — the context header appears once,
with both the negation and the addition grouped under it.

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

Actual commands: `init`, `pull`, `status`, `sync`, `commit`, `discard`. `pull` connects,
fetches `show running-config brief`, writes it to `EDIT_FILE`, and commits it — this is
how an already-configured device gets onboarded (`init` alone only creates an empty
`device.config`), and it doubles as a way to resync the baseline if the device changed
out-of-band. It refuses to run while `host_dirty` unless passed `-y` (would silently
clobber uncommitted local edits); when not `host_dirty` it needs no confirmation at all,
since there's nothing local to lose. `status` is read-only
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

`_connect()` in `main.py` resolves `username` from `C2SYNC_USERNAME`, then the global
config's `username` key (see Global user config below); `password` only from
`C2SYNC_PASSWORD`. It only skips `input()`/`getpass.getpass()` once both username and
password are resolved — a partially-set environment (or a config file with no
`C2SYNC_PASSWORD` set) falls back to prompting for whatever's still missing, never
half-prompts or hangs on stdin in CI. `C2SYNC_SECRET` is checked the same way as
`C2SYNC_PASSWORD` but is optional either way (`None` if unset, prompted for otherwise).
Passwords/enable-secrets are **never** read from the global config file or stored
anywhere by c2sync itself — env vars are meant to be injected by the CI system's own
secrets manager. Combined with `sync -y`/`commit -y` (skips the confirmation prompt
too), this is what unblocks the PR-merge-triggers-apply workflow from `HANDOFF.md`'s
roadmap.

A `docker login`-style persistent credential store (i.e. one that also holds the
password) was considered and explicitly declined: `docker login`'s own default storage
is just base64 in a config file (obfuscation, not encryption) unless a credential
helper is configured, and that's a real security downgrade for device-admin
credentials versus prompting every time. If interactive password persistence is
wanted later, the OS-native keyring (`keyring` package) is the option on the table —
not a home-grown file store.

### Global user config (`user_config.py`)

An entirely optional TOML file at `$XDG_CONFIG_HOME/c2sync/config.toml` (default
`~/.config/c2sync/config.toml`) for **non-secret** preferences only — `user_config.
load()` returns `{}` if it doesn't exist, and every key it can hold already has a
working default, so nothing breaks without it. `user_config.config_path()` resolves
the path fresh on each call (not a module-level constant) specifically so tests can
`monkeypatch.setenv('XDG_CONFIG_HOME', ...)` without reloading the module.

Recognized keys, all optional:
- `username` — read by `_connect()` (see CLI surface above); never the password/secret.
- `baudrate` — default for `c2sync init`'s optional `BAUDRATE` argument; an explicit
  CLI argument still wins.
- `timeout` / `prompt_regex` — passed through to `Project.TIMEOUT`/`PROMPT_REGEX` at
  `init` time if present, otherwise the `Project` dataclass's own defaults apply.

TOML (not JSON) specifically because `tomllib` is stdlib as of Python 3.11 — this is
why `pyproject.toml`'s `requires-python` was bumped from `>=3.9` to `>=3.11`. `tomllib`
is read-only; that's fine here because this file is meant to be hand-edited by the
user, not written by c2sync. A malformed file prints a parse error and exits (`sys.
exit(1)`) rather than silently ignoring it, since — unlike a missing file — a present
but broken config is very likely a real mistake worth surfacing.

## Known constraints / simplifications

- Cisco IOS only; `device_type='cisco_ios'` is hardcoded in `connector.py`, and
  `Diff(..., syntax='ios')` is hardcoded in `differ.py`.
- No rollback if command N of a multi-command batch is rejected after N-1 already
  landed on the device.
- No file locking on `state.json`/`staging.txt` — fine for one interactive CLI
  invocation at a time, not safe for concurrent access.

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
   consistent with the thin-wrapper decision. Non-interactive credentials (gap #6) are
   also done — `_connect()` takes `C2SYNC_USERNAME`/`C2SYNC_PASSWORD`/`C2SYNC_SECRET`
   from the environment when both username and password are set, only falling back to
   interactive prompts otherwise; nothing is persisted by c2sync (see Device transport
   below). `pull` (gap #2) is also done — see CLI surface above.
2. **A real config-tree parser — done.** `differ.py` is now a thin wrapper around
   `ciscoconfparse2.Diff` (see Diff → CLI command translation above) instead of
   hand-rolled indentation-walking. Not TextFSM — TextFSM parses flat command *output*
   (e.g. `show version`) via regex templates, it has no concept of hierarchical config
   structure. Real deletion handling and multi-line block support both came from this
   for free — no extra code needed beyond calling `get_diff()`. `models.py`
   (`Addition`/`Command`/`CommandBlock`) was deleted entirely rather than kept
   half-used: `ciscoconfparse2.Diff.get_diff()` already returns ready-to-write CLI
   lines, so there was nothing left for that layer to do.
3. **SSH as a second transport — designed for, not built yet.** Since `connector.py`
   already goes through Netmiko, SSH is close to swapping `serial_settings={...}` for
   `host=...` on the same `device_type`. Keep `differ.py`/`state_engine.py`/`main.py`
   transport-agnostic (they already operate on `Project` and CLI text, not on
   `SerialInterface` internals) so this stays cheap when it's actually prioritized.

## Docs drift to be aware of

`README.md` was reconciled with the actual implementation (real CLI surface, single
device per project, git integration, credentials/global config) after Priority 1
landed. Still, treat `main.py` as ground truth over `README.md` if they ever
disagree again — reconciling docs after each roadmap item lands is the intended
cadence, not a one-time fix.
