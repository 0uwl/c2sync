# C2Sync Handoff

Context dump for a new Claude Code session picking up this project. Written at the
tip of the `netmiko` branch, immediately before it's merged into `main` via PR.
Read this first, then `agent-summary.md` (stable design reference) and `README.md`
(user-facing docs — currently drifted from the real CLI, see Known Gaps).

## What C2Sync is

A CLI tool that pulls a Cisco IOS device's running-config to a local text file over
a console/serial connection, lets the user edit it in a normal text editor, and
pushes the diff back as CLI commands. Positioning decision (made this session,
see Roadmap #1): **console-only is the deliberate starting scope**, not an
accidental limitation — SSH support is a designed-for future extension, not
something to build now.

## Current architecture

```
c2sync/
├── __init__.py       Project dataclass (paths for every per-project file) + init_project/get_project
├── connector.py       SerialInterface - Netmiko ConnectHandler wrapper (serial transport)
├── differ.py          Differ - indentation-based diff -> CLI command blocks
├── exceptions.py       C2SyncError, ConfigApplyError, ConfigSaveError
├── main.py            CLI: init / status / sync / commit / discard
├── models.py          Addition, Command, CommandBlock dataclasses
├── state_engine.py    StateEngine - host_dirty / device_dirty tracking, persisted to state.json
└── tests/             pytest, all mocked (no real hardware needed to run them)
```

Per-project files (all under `./.c2sync/`, one project = one device today):
- `device.config` (`EDIT_FILE`) — what the user edits.
- `baseline.config` (`BASELINE_FILE`) — snapshot of the config as of the last confirmed
  sync. `EDIT_FILE` is diffed against this on demand. **Likely to be replaced by git
  itself** — see Roadmap #3 open question.
- `staging.txt` (`STAGING_FILE`) — recomputed CLI commands, overwritten each check.
- `state.json` (`STATE_FILE`) — `{host_dirty, device_dirty}`.
- `c2sync.config` (`CONFIG_FILE`) — serialized `Project` (serial device, baudrate, paths).

## What changed this session (in order)

1. **Netmiko migration** — replaced a hand-rolled `pyserial` polling loop
   (`connector.py`) with Netmiko's `ConnectHandler(serial_settings=...)`. Fixed real
   bugs in the process: enable-password prompts were never handled, `timeout is -1`
   used identity comparison on an int, paging-disable depended on call order.
2. **Verified apply + a state tracker** — `send_config_set(error_pattern=...)` now
   raises `ConfigApplyError` on a rejected command instead of silently pushing it;
   `save_config()` checks for IOS's `[OK]` before trusting the save happened.
   `StateEngine` transitions only ever fire on these *confirmed* outcomes, never on
   send. `main.py`'s `sync`/`commit`/`discard` were fully implemented on top (they
   were `NotImplementedError` stubs before this session).
3. **Removed the watcher, went on-demand** — deleted `watchdog`/`watcher.py`
   entirely. `Differ.refresh_staging_from_files()` diffs `BASELINE_FILE` vs
   `EDIT_FILE` fresh on every `status`/`sync` call, mirroring how `git status` works
   (compare against stored content when asked, not a live watch). Added the `status`
   command as the read-only entry point for this. `sync` now advances
   `BASELINE_FILE` after a confirmed push (the git-index analogy); `discard` now
   actually reverts `EDIT_FILE` to the baseline instead of only clearing staging.

All 33 tests pass (`pytest c2sync/tests`). Note: this sandbox doesn't have `netmiko`
installed in the system Python — tests were run against a scratch venv
(`python3 -m venv`, then `pip install -e .`). A fresh session should just
`pip install -e .` normally; there's nothing unusual about the dependency.

## Known gaps (full fresh-eyes review, condensed)

Ranked by how much each undercuts the project's premise:

1. **Git isn't actually integrated anywhere in the code.** README/agent-summary
   describe auto-commits on every pull/sync; grepping the codebase turns up zero
   `git` usage. This is the #1 priority — see Roadmap #3.
2. **No `pull` command exists.** `init` only creates empty files. There's currently
   no way to onboard an already-configured device — which is nearly every real
   device. Needs to exist before the tool is usable for anything but a blank
   greenfield device.
3. **Deletions are silently dropped by design.** `Differ._extract_additions` only
   looks at `difflib.ndiff`'s `"+ "` lines; removed (`"- "`) lines are discarded.
   The documented workaround (type `no <command>` instead of deleting the line)
   inverts the basic text-editor affordance a new user expects, with no warning
   when they get it wrong. High-value, tractable fix once the config-tree parser
   (Roadmap #2) is in — `ndiff`'s `-` lines already carry the removed text.
4. Diff engine is indentation-counting, not a real parser: no multi-line block
   support (banners, macros), no strict command ordering guarantees within a
   block, no validation when whitespace is inconsistent. Addressed by Roadmap #2.
5. No pre-flight safety net beyond command-rejection detection — nothing stops a
   syntactically valid but operationally dangerous command (e.g. dropping the
   interface the console session rides on), and no rollback if command N of M in
   a batch is rejected after N-1 already landed.
6. `main.py`'s `_connect()` is `input()`/`getpass.getpass()` only — no
   non-interactive/CI mode. Directly blocks the PR-merge-triggers-apply workflow
   that's the actual payoff of using git properly (Roadmap #3).
7. README documents `init, pull, sync, status, diff, commit` and a multi-device
   registry; actual CLI is `init, status, sync, commit, discard`, one device per
   project, no registry. Docs and code have drifted — worth reconciling once the
   roadmap items land rather than chasing a moving target now.
8. `state.json`/`staging.txt` have no file locking — fine for a single interactive
   CLI today, a real race once anything (e.g. a future VS Code extension) polls
   `status` in the background while a user also runs commands manually.
9. Netmiko's `session_log` (full transcript of what was sent/received) is unused —
   no audit trail independent of the before/after file diff.
10. Tests are solid for logic (differ/state/connector, all mocked) but nothing
    exercises a realistic IOS response fixture (multi-line banners, `--More--`
    paging, varied rejection message text).

## Roadmap (user-approved priority order)

### Priority 1: Real git integration — do this before anything else below

**Model: thin wrapper, not a full wrapper.** c2sync should not reimplement
`diff`/`log`/`branch`/PR review — that would just be git with extra steps under a
different name, and throws away the actual reason to use git (GitHub/GitLab review,
`git log`/`blame`, branching, `git revert` as the rollback story, every tool anyone
already has for git). Instead:

- `c2sync init` runs `git init` in the project dir (and presumably commits the
  initial empty state).
- A confirmed `sync`/`commit` makes a real git commit with a structured message
  (device name, timestamp, what was pushed / what was saved to startup-config).
- The user is free to use `git` directly against the same repo for anything
  history/review/branching-related — c2sync doesn't need its own verbs for that.
- This is also what unlocks non-interactive apply later (gap #6): a PR against
  `device.config` gets reviewed and merged normally on GitHub, then `c2sync sync`
  (run by a human or CI) picks up the merged file, diffs it against the
  device-confirmed baseline, and pushes. Git owns review/history; c2sync owns the
  device-specific translation and safety-checked push.

**Open design question for the implementing session:** does `BASELINE_FILE` still
need to exist once git is real, or does "last confirmed baseline" become "the file
as of c2sync's last commit" (read via `git show HEAD:device.config` or similar)?
The current `BASELINE_FILE` was explicitly built this session as a stand-in for
git's index/blob (see `agent-summary.md`'s "On-demand change detection" section) —
now that real git is coming, collapsing that stand-in into git itself is probably
right, but decide deliberately rather than keeping both.

Other open questions to resolve while implementing:
- Exactly which lifecycle points commit (every `sync`? every `commit`-to-startup?
  both, as separate commits?).
- Where `gitpython` vs shelling out to `git` lands, and why.
- Whether `pull` (gap #2 — doesn't exist yet) should be built alongside this,
  since a real onboarding flow (`pull` → git-tracked baseline) is a natural
  extension of "git is now real" and directly unblocks the tool's actual usability.

### Priority 2: A real config-tree parser (after git lands)

Use **ciscoconfparse** (or the maintained fork `ciscoconfparse2`) — NOT TextFSM.
TextFSM parses flat command *output* via regex templates (e.g. `show version` into
a table of rows); it has no concept of hierarchical config structure, so it can't
build the parent/child tree this needs. ciscoconfparse is purpose-built for exactly
this: parses IOS-style indentation into a real object tree (`find_objects`,
`re_search_children`), supports structured add/delete, round-trips back to valid
config text. Adopting it should directly resolve:
- Deletion handling (gap #3) — real removal semantics instead of requiring manual
  `no` prefixes.
- Multi-line block handling (banners, macros) — treated as opaque blocks instead of
  being walked line-by-line.
- Command ordering guarantees within a block.

This will likely mean rewriting most of `differ.py`'s `_build_context_block`/
`_extract_additions`/`_build_commands` pipeline around ciscoconfparse's tree
instead of hand-rolled indentation counting. `models.py`'s `Addition`/`Command`/
`CommandBlock` dataclasses may or may not still be the right shape — evaluate once
the tree structure is in hand rather than forcing the old shape to fit.

### Priority 3: SSH as a second transport (design for it now, build it later)

Not being built yet — mentioned here so the git and parser work doesn't
accidentally make it harder later. Since `connector.py` already goes through
Netmiko's `ConnectHandler`, SSH support is close to just swapping
`serial_settings={...}` for `host=...` on the same `device_type`. Keep that cheap:
- Don't let serial-specific assumptions leak into `differ.py`, `state_engine.py`,
  or `main.py` — they should already be transport-agnostic (they operate on
  `Project` and CLI text, not on `SerialInterface` internals), and should stay
  that way as git/parser work lands.
- If it's easy when touching `connector.py` for other reasons, consider whether
  `SerialInterface` should eventually be renamed/generalized (e.g. a transport
  parameter or a small factory) rather than assuming serial forever — but don't
  build SSH support itself until it's explicitly prioritized.

## Explicit non-goals for now

- Multi-vendor support (Juniper/Arista/etc.) — Cisco IOS only, by design, for now.
- Multi-device-per-project registry — despite what README/agent-summary describe,
  don't build this until it's explicitly prioritized; either build it or fix the
  docs, but don't half-do it as a side effect of other work.
- Rollback/dry-run-against-simulator safety net — real gap (see Known Gaps #5),
  but not in scope until after git + parser land.

## Verifying the current state

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
pytest c2sync/tests -q   # expect: 33 passed
```

## Branch status

On `netmiko`, 3 commits ahead of `main`, fast-forwards cleanly (no conflicts as of
this writing). User will open the PR after this file is committed.
