# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What C2Sync is

A CLI tool that acts as a middleman between a Cisco IOS device (over console/serial or
SSH) and a local git repository: pull the running-config to a local text file, edit it
in a normal text editor, and push the diff back as CLI commands. Cisco IOS only, one
device per project, is a **deliberate** starting scope (see Roadmap below), not an
oversight — both other vendors and multi-device fleets are documented future directions
(see `README.md`'s "Potential Future Features" and Out of current scope below), just not
current work.

There is no lint/format tooling configured in this repo (no ruff/black/flake8 config)
— don't invent one.

## Git as the mental model

C2Sync treats the device as a remote repository to push and pull its configuration
from. Every design decision here should run through one test: **what would git do at
this point?** That isn't decoration — it's the governing principle behind most of what's
already built, even in places where it was never written down as a rule until now:

- **Verb choice.** `push`/`pull` are named for git's own transfer verbs specifically
  because the earlier `sync`/`commit` naming obscured that `push` is a one-way write to
  production network gear, not a two-way reconciliation (see CLI surface below). `save`
  is deliberately kept *outside* git's verb space for the opposite reason — it isn't
  git's `commit` (an irreversible `write memory`, not a cheap local operation), and
  reusing that name would invite the wrong assumption at exactly the point the analogy
  stops holding.
- **Push safety.** The out-of-band drift check (see below) is framed and built as
  `git push` without a fetch: read the remote's current state first, and refuse to push
  over a target that's moved, rather than trusting a stale local diff. The flag that
  authorizes adopting that drift is named `push --rebase`, not `--force` — what it
  actually does (replay the staged commands on top of the device's moved-on state,
  leaving `device.config` itself untouched) is a rebase, not a blind overwrite, and the
  name should tell a git-literate operator exactly that.
- **Fetch vs. pull.** `c2sync fetch` is the read-only device-drift check — connect, read,
  report, touch nothing local — mirroring git's own `fetch` (look only) against `pull`
  (fetch *and* merge, which is what `c2sync pull` already did before `fetch` existed).
  `push`'s own drift check is effectively an inline `fetch` it happens to also act on;
  `fetch` is that same read made available on demand, closing the one real gap in an
  otherwise offline `status` (see On-demand change detection below).
- **Recovery.** `revert` is modeled on `git revert` (a new commit undoing prior state),
  not `git reset --hard` (rewinding history), specifically so a bad push stays visible
  in `git log` instead of being erased.
- **Refusing silent loss.** `pull`/`revert` both refuse to overwrite unpushed local
  edits unless explicitly forced — the same instinct behind git refusing a
  non-fast-forward `pull` rather than silently discarding local commits. The flag they
  check is *derived*, never cached, so the refusal holds whether or not `status`
  happened to run first (see State tracking below).
- **A project should survive a clone.** Connection info (`TRANSPORT`, `SERIAL_DEVICE`/
  `HOST`, etc.) is tracked in git via `c2sync.toml`, not left in gitignored scratch state
  — the same instinct behind git tracking everything a checkout needs to keep working,
  never requiring a manual post-clone setup step for anything but genuinely local
  scratch. See Connection info is git-tracked below.

**Where this stops:** the device is not a git remote, and c2sync should not try to make
it one. Git's model assumes structured objects, history, and merges on both ends; a
device has none of that — it's one flat, live state that either accepts a batch of CLI
commands or rejects them. A literal git-remote-helper (so a user could `git push device
main` directly) would mean reimplementing a real transport protocol to bridge two
genuinely mismatched models, and would bury the parts that actually matter — verified
command application, drift detection, `ciscoconfparse2`-generated negations — inside
protocol plumbing instead of the plain Python functions they are today. The metaphor
operates at the level of naming and behavior — what would git do at this decision point
— not the wire protocol.

Run new command or behavior decisions through this test before reaching for a bespoke
design.

## Commands

Requires Python >=3.11 (for stdlib `tomllib`, used to read the global config file).

```bash
# Install (editable). pytest lives in [project.optional-dependencies] dev, NOT in
# [project.dependencies] — build.sh vendors the latter into the shipped archive, so
# putting a test dependency there would ship pytest and its whole tree to users.
pip install -e ".[dev]"

# Run the full test suite
pytest c2sync/tests -q

# Run a single test file / single test
pytest c2sync/tests/test_differ.py -q
pytest c2sync/tests/test_differ.py::test_refresh_staging_stages_additions_with_context -q

# Run the CLI locally (after install; requires a real or mocked device connection for
# anything beyond `init`/`status`/`discard`)
c2sync init NAME SERIAL_DEVICE [BAUDRATE] [--dir PATH] [--pull]   # serial transport
c2sync init NAME --ssh HOST [PORT] [--dir PATH] [--pull]          # SSH transport
c2sync pull [--force|-f]
c2sync fetch
c2sync status
c2sync push [-y] [--rebase] [--rollback-on-error]
c2sync save [-y]
c2sync discard
c2sync revert [COMMIT] [-y] [--force|-f]   # COMMIT defaults to HEAD

# Build a distributable archive into dist/ (see Build and distribution below)
./build.sh                     # test, then build
./build.sh --skip-tests
./build.sh --test-install      # also install it in a container per Python version
PYTHON=/usr/bin/python3 ./build.sh   # if the default python3 has no pip
```

Tests are mocked at the Netmiko/`ConnectHandler` boundary — no real hardware, serial
port, or network connection is needed to run the suite. The one deliberate exception is
`test_serial_project_reaches_netmikos_real_serial_driver`, which runs the real
`ConnectHandler` and stubs one level lower (`check_serial_port` and the port open) so
that Netmiko's own transport dispatch is actually exercised; see Device transport below
for the bug that motivated it. It still touches no hardware. `c2sync/tests/` has no `__init__.py`; pytest's
default rootdir insertion is what makes `from constants import PROJECT` work in test
files, not a package import.

## Architecture

### Project directory model

`c2sync init NAME SERIAL_DEVICE [BAUDRATE] [--dir PATH]` (serial) or `c2sync init NAME
--ssh HOST [PORT] [--dir PATH]` (SSH) creates a project directory for **one device** —
there is currently no multi-device registry — a wanted future direction that is simply
not built yet, not a rejected one (see Out of current scope below). `NAME` is mandatory:
it's the default directory (`./NAME`, overridable with `--dir PATH`) and the project's
human-readable identity for anything that isn't the device itself (see `Project.target`
vs. `Project.NAME` below).

**Only `.c2sync/` is hidden.** Earlier this put everything - including `device.config`,
the one file a human actually opens - inside a single hidden folder, which inverted how
git itself is organized: `.git/` is hidden VCS metadata, but the tracked files live
directly in the working directory. The project directory now mirrors that split:

```
myrouter/                 <- what you `cd` into; PROJECT_DIR is '.' from in here
├── device.config         <- EDIT_FILE, what you edit, tracked in git
├── c2sync.toml            <- CONFIG_FILE, the serialized Project, tracked in git
├── .gitignore             <- excludes .c2sync/ - the only entry it needs
├── .git/                  <- real git repo, git_ops.init() ran here
└── .c2sync/               <- the only hidden part: pure operational scratch
    ├── staging.txt        <- STAGING_FILE, recomputed CLI commands, fully overwritten each check
    └── state.json         <- STATE_FILE, {device_dirty}, see State tracking below
```

`device.config` is tracked in git; the baseline is `device.config` at git `HEAD`, not a
separate file (see On-demand change detection below). `c2sync.toml` is tracked
too - see "Connection info is git-tracked" below for why. The two `.c2sync/` files are
never committed - `.gitignore`'s one line (`.c2sync/`) excludes the whole subfolder,
rather than naming each file, so nothing new added there later needs a matching entry.

**Every command except `init` expects cwd to already be inside the project directory** -
the same way `git status` expects to be run from inside the repository, not handed a
path to one. `get_project()` looks for `./c2sync.toml` relative to cwd; there's no
walking up the tree the way git finds `.git` from a subdirectory, so the project's own
top level is the only place commands work from today.

**`Project.at(project_dir, **kwargs)`** (`c2sync/__init__.py`) is what actually builds a
`Project` for a location other than `.`. The dataclass's own field defaults for
`CONFIG_FILE`/`EDIT_FILE`/`STAGING_FILE`/`STATE_FILE` are plain `'.'`-relative strings
evaluated once at class definition time - correct only for the implicit "already inside
the project" case every command but `init` runs in. `init` (creating `./NAME` or
`--dir PATH`) and any test needing a project somewhere else go through `.at()` instead
of the plain constructor.

**`Project.to_dict()` deliberately omits `PROJECT_DIR` and the four paths derived from
it.** A path is only ever valid relative to wherever `init` was physically run from, but
every later command loads this config expecting cwd to already be inside the project -
so the fixed `.`-relative dataclass defaults are what should apply on load, not
whatever path happened to resolve at creation time. This is also what makes a renamed or
moved project directory keep working with no migration: nothing in `c2sync.toml`
encodes where the directory used to be, or is.

`init_project()` refuses to run (raises `ProjectExistsError`, caught in `main.py` and
turned into a clean exit) if `PROJECT_DIR/c2sync.toml` already exists, rather than
silently overwriting an existing project's `device.config`/`c2sync.toml`. This is
stricter than `git init`'s own idempotent-rerun behavior on purpose: `git init` in an
existing repo is harmless, but c2sync's `init` also writes `device.config`, so the same
idempotence would mean silent data loss on a second run. Checking `c2sync.toml`
specifically (not the scratch dir) is what makes this guard correct after a `git clone`
too, per the next section - a freshly cloned project has `c2sync.toml` but no scratch
dir yet, and must still be refused rather than treated as blank.

### Connection info is git-tracked (`c2sync.toml`)

`c2sync.toml` holds the serialized `Project` - `NAME`, `TRANSPORT`, `SERIAL_DEVICE`/
`HOST`, `BAUDRATE`/`SSH_PORT`, `TIMEOUT`, `PROMPT_REGEX` (everything `Project.to_dict()`
returns) - and is committed by `init_project()` in the same first commit as
`device.config` and `.gitignore`. This is what makes `git clone`ing a c2sync project
directory actually work: without it, the connection details lived only in the gitignored
scratch dir, so a fresh clone had `device.config` and history but no way to know what
device to talk to - a real gap, since the whole point of tracking the project in git is
that it travels. None of this is secret: a serial device path, a hostname, a baud rate,
and a timeout carry no credentials (those are never persisted anywhere - see Global user
config and the CLI surface's `_connect()` notes below), so committing and pushing them is
safe by the same reasoning that makes `device.config` itself safe to push.

TOML, not JSON, for the same reason as `user_config.py`'s global preferences file: it's
meant to be hand-editable too (a device gets a new IP, a baud rate changes). Reading uses
stdlib `tomllib` (read-only, Python 3.11+); writing uses a small hand-rolled
`_write_toml()` rather than pulling in `tomli-w` as a dependency, since the data is
always flat scalars (str/int, no nesting or arrays) - genuinely nothing a real TOML
writer would buy here. TOML has no null type, so `_write_toml()` skips `None`-valued
fields entirely rather than writing them as empty/null - `SERIAL_DEVICE` is simply absent
from an SSH project's `c2sync.toml`, and `HOST` from a serial project's, rather than
either being present-but-empty. Fields that always hold a real value regardless of
`TRANSPORT` (`BAUDRATE`, `SSH_PORT`) are written either way, even when meaningless for
the project's actual transport - only fields that are genuinely `None` get omitted.

**Auto-bootstrapping scratch state after a clone.** `.c2sync/`'s two files
(`staging.txt`, `state.json`) are pure local operational scratch and stay gitignored -
they never travel with a clone, on purpose (see the directory-layout intro above). So a
fresh `git clone` leaves a project with `c2sync.toml`/`device.config`/history but no
`.c2sync/` yet. `get_project()` calls `_ensure_scratch_state()` on every load, which
recreates `staging.txt` and `state.json` from scratch if `.c2sync/` doesn't exist -
mirroring git itself needing no post-clone setup step beyond the clone. Only
`device_dirty` is written there (initialized `False`), and it's a real unknown rather
than a verified fact - the device's actual state hasn't been read at clone time;
`c2sync fetch` (see CLI surface below) is how an operator trues that up. `host_dirty`
needs no entry at all: it's derived from `EDIT_FILE` vs. `HEAD` on every load (see State
tracking below), which for a fresh checkout correctly computes clean without anything
having to assert it.

### Module map

| Module | Responsibility |
|---|---|
| `c2sync/__init__.py` | `Project` dataclass, `init_project`/`get_project` |
| `c2sync/connector.py` | `DeviceInterface` — Netmiko `ConnectHandler` wrapper (serial or SSH) |
| `c2sync/differ.py` | `Differ` — real config-tree diff (`ciscoconfparse2`) → CLI commands |
| `c2sync/git_ops.py` | Thin `git` subprocess wrapper — `init`/`commit`/`commit_content`/`commit_empty`/`show_at`/`show_at_head`/`resolve_rev` |
| `c2sync/user_config.py` | Reads the optional global TOML preferences file — `load()`/`config_path()` |
| `c2sync/state_engine.py` | `StateEngine` — derived `host_dirty` / persisted `device_dirty` |
| `c2sync/exceptions.py` | `C2SyncError`, `ConfigApplyError`, `ConfigReadError`, `ConfigSaveError`, `HostKeyRejectedError`, `ProjectExistsError` |
| `c2sync/main.py` | CLI entry point: `init` / `pull` / `fetch` / `status` / `push` / `save` / `discard` / `revert` |

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
refresh_staging_from_files()` (called at the top of both `status` and `push` in
`main.py`) reads the baseline via `git_ops.show_at_head()` (`git show HEAD:device.
config` under the hood) and `EDIT_FILE` fresh every time, recomputes `STAGING_FILE`
from scratch, and returns whether anything is staged. There's no mtime/stat fast-path
the way git status has one — these config files are small enough that a full content
diff (plus one `git show` subprocess call) on every check is already cheap.

### State tracking (`state_engine.py`)

Two independent booleans, not a single enum — this matters because a discard should be
able to clear `host_dirty` without touching `device_dirty`, and vice versa:

- `host_dirty` — `EDIT_FILE` differs from `device.config` at git `HEAD` (unpushed local
  edits).
- `device_dirty` — a push has been sent and confirmed, but not yet saved to
  startup-config.

**They are stored differently on purpose, and this is load-bearing.** `host_dirty` is
**derived** on every `StateEngine` construction, by diffing `EDIT_FILE` against the
baseline; only `device_dirty` is persisted in `state.json`. The reason is that
`host_dirty` is a question the files can always answer, so storing it only creates a
cache — and it was a stale one. It used to be written by `_refresh_staging()`, which
only `status` and `push` call, while `pull`/`save`/`revert` read the stored value to
decide whether overwriting `EDIT_FILE` would destroy unpushed work. Editing
`device.config` and running `pull` straight away therefore found a stale `False` and
silently clobbered the edits — the exact silent loss the guard exists to prevent. A
derived flag cannot go stale, and no new command can forget to refresh it. `device_dirty`
stays persisted because nothing local can derive it: whether the device has
running-config changes not yet written to startup-config is only knowable from what we
last did to the device.

The derivation uses `Differ.diff_lines`, **not** string equality, for the same reason
drift detection does: the question is whether there are commands to send. A cosmetic
edit that stages nothing is not unpushed work, and treating it as such would make `pull`
refuse to run while `status` simultaneously reported nothing staged.

`StateEngine.state.label` computes a single display string (`host pending changes` >
`device pending changes` > `synced`) with `host_dirty` taking priority. `device_dirty`
transitions are only ever driven by **confirmed** outcomes from `connector.py` (a
Netmiko-verified push or save), never assumed on send — see next section.

### Device transport (`connector.py`)

`DeviceInterface` wraps Netmiko's `ConnectHandler` for both transports —
`device_type='cisco_ios_serial'` plus `serial_settings={port, baudrate}` when
`project.TRANSPORT == 'serial'`, `device_type='cisco_ios'` plus `host=..., port=...`
(SSH_PORT, default 22) when `'ssh'`. **`device_type` is part of that branch, not a
constant**: Netmiko selects the transport class from `device_type` alone and ignores
`serial_settings` when choosing, so plain `'cisco_ios'` is the *SSH* driver and a serial
project built with it dies in `ConnectHandler` with `ValueError: Either ip or host must
be set` — which was a real bug here, invisible to the suite because the transport tests
mock `ConnectHandler` and so accept any `device_type`.
`test_serial_project_reaches_netmikos_real_serial_driver` is the guard against a repeat:
it lets the real `ConnectHandler` run, stubbing only `check_serial_port` (which
validates against the *test host's* comports) and the port-opening calls, and asserts
the resolved class. That branch is the entire transport difference; everything past
connection setup (prompt detection, paging, AAA login, `apply_config`/`save_config`) is
identical either way since both are the same `cisco_ios` command set over a different
transport. `Project.target`
(`c2sync/__init__.py`) returns whichever of `SERIAL_DEVICE`/`HOST` is relevant, so
callers (`main.py`'s git commit messages) don't need to branch on `TRANSPORT`
themselves.

The SSH branch also passes `ssh_strict=True, system_host_keys=True` — Netmiko/Paramiko
default to `ssh_strict=False` (`AutoAddPolicy`: silently trust and never persist any
host key presented, on every connection), which is a real MITM exposure for device
credentials once you're connecting over a network instead of a local serial cable.
`system_host_keys=True` makes it verify against `~/.ssh/known_hosts` instead, the same
trust-on-first-use-with-persistence model a plain `ssh` client uses. Practical
consequence: a device whose host key isn't already trusted there will fail to connect
until the operator trusts it once outside c2sync (e.g. a plain `ssh user@host` or
`ssh-keyscan`) — correct, expected behavior, not a bug.

**Optional escape hatch:** if the global config's `prompt_for_unknown_ssh_hosts` is
`True` (default `False`), `DeviceInterface.__init__` catches the specific failure
Netmiko raises for a genuinely-unknown host — a `NetmikoTimeoutException` containing
`"not found in known_hosts"`, which is exactly paramiko's `RejectPolicy.
missing_host_key()`'s own message wrapped by Netmiko's generic `except paramiko.
ssh_exception.SSHException` handler — and calls `_trust_new_host_key()`. That function
opens its own `paramiko.Transport` directly (no Netmiko, no authentication - just the
transport-level handshake) to fetch the server's real key, prints an OpenSSH-style
`"key fingerprint is SHA256:..."` prompt, and only on an explicit `yes` adds it to
`~/.ssh/known_hosts` (via `paramiko.HostKeys`) before the original `ConnectHandler`
call is retried once. A **changed** key never reaches this path at all regardless of
the setting - paramiko raises `BadHostKeyException` (different exception, different
message) for that case, straight from its own host-key-checking logic in
`SSHClient.connect()`, so it always fails hard rather than ever being auto-trusted.
This is why the setting defaults off: TOFU-with-persistence only protects against an
attacker who isn't on-path during the *first* connection - for a device being
onboarded for the first time (a shared lab VLAN, a jump host, a compromised switch),
that's exactly the moment a human verifying the fingerprint out-of-band actually
matters.

**Reads are sanitized and validated, not trusted.** `get_running_config()` does not
hand back whatever Netmiko returned. With `logging console` on — the IOS default — the
device emits asynchronous syslog messages straight into the session, which land in the
middle of command output and are indistinguishable from config lines to a parser. On a
real console this put `Switch#` and `*Sep 11 12:33:35.565` into `device.config`. Because
the trailing fragment is cut wherever the read happened to stop, it differed on every
read, so **every** subsequent `fetch`/`push` reported drift that did not exist — and
`push` offered to send `no *Sep 11 12:3` to the device as a config command.

`_clean_running_config()` therefore drops syslog-shaped lines wherever they appear (not
just at the tail — an async message can splice itself between two config lines) and
keeps nothing after the final `end`. The `show` preamble is deliberately left alone:
`ciscoconfparse2` already absorbs it without producing diff lines. It then raises
`ConfigReadError` if there is no `end` at all, which is the same fact the truncation
depends on — without a terminator there is no way to tell a complete config from a read
that was cut short, and a partial config committed as the baseline is exactly as
damaging as a noisy one. A config of just `end` is legitimately a blank device. This
matters because every caller treats the return value as the device's true state and
commits it, so a bad read does not merely look untidy — it *becomes* the baseline.

Two things this wrapper adds on top of raw Netmiko, transport-independent:

- `apply_config()` passes an IOS `error_pattern` to `send_config_set()`, so a rejected
  command raises `ConfigApplyError` instead of being silently pushed with the rest of
  the batch (Netmiko does not raise on command errors by default without this).
- `save_config()` checks the device's response for IOS's `[OK]` marker before
  returning, raising `ConfigSaveError` if the save wasn't actually confirmed.

`main.py`'s `push`/`save` only advance state (clear staging, mark `device_dirty`,
commit the new baseline to git, mark `device_dirty` clean) after these confirmed
returns — never optimistically.

### Out-of-band drift check (`push`'s pre-flight)

Everything `_refresh_staging()` computes is offline: `EDIT_FILE` diffed against
`device.config` at git `HEAD`, never the device. `push` used to trust that all the way
through — it previewed those commands, took the confirmation, and only then opened a
connection, to `apply_config()` and nothing else. So if the device had changed
out-of-band (another operator on the console, another tool), the preview described a
device that no longer existed, and the user approved it anyway.

Deletions are the sharp edge, because negations are generated from the **baseline's**
content rather than the device's: delete `description Server` locally and c2sync emits
`no description Server`, which on IOS clears whatever description is actually there —
silently destroying a colleague's `description Core-Uplink` that replaced it, with
nothing in the preview hinting at it. This is `git push` without a fetch, and the fix is
git's: check first, refuse to push over a moved target.

`_reconcile_out_of_band_drift()` runs inside `_connected()` before any preview is shown.
It reads the live running-config and diffs it against the baseline **with
`Differ.diff_lines`, not string equality**, so formatting noise in `show
running-config` isn't mistaken for a change. No drift → returns the staged lines
untouched and the command behaves exactly as before.

On drift it prints what changed on the device, then:

- `--rebase` — adopt without asking. Named `--rebase`, not `--force`/`-f` like
  `pull`/`revert` use for their own overwrite-approval flag: what this actually does is
  replay the staged commands on top of the device's moved-on state (`EDIT_FILE` itself is
  never touched), which is a rebase, not a force-overwrite — see Git as the mental model
  above. No short form, unlike `pull -f`/`revert -f`: this is reached for rarely enough
  (only on genuine out-of-band drift) that a terse alias isn't worth the risk of it being
  typed reflexively.
- `-y` alone — **hard failure, exit 1**, never a prompt. `-y` means "don't ask me to
  confirm my own commands", not "silently overwrite someone else's work", and prompting
  here would hang a CI job on stdin. Same independent-flag split as `pull`/`revert`,
  where `-y` and `--force` are also deliberately separate flags — `push` just names its
  own version of that second flag differently, for the reason above.
- otherwise — prompt; declining exits 1 with nothing sent.

Adopting calls `git_ops.commit_content()` (which is why that function exists in the
shape it does — it advances `HEAD` to the live config **without touching `EDIT_FILE`**),
then re-runs `_refresh_staging()`. The user's edits survive, the drift is recorded as a
commit visible in `git log`, and the recomputed preview is the truth. Note the
recomputed commands *will* include undoing the out-of-band change, since `EDIT_FILE`
doesn't contain it — that is the intended outcome, not a flaw: it happens either way,
and this is the version where the operator sees it before approving. Keeping both sets
of changes is a real three-way merge, deliberately not built — `PROJECT_DIR` is a normal
git repo and git can do it.

**Consequences for the rest of `push`.** The preview and its confirmation now live
*inside* the `with _connected(...)` block, since neither can be computed before the
device has been read — so `push` prompts for credentials before showing anything.
`pre_push_head` is captured **after** drift handling, so a `--rollback-on-error` rollback
targets what the device actually had immediately before this push rather than a
pre-adoption baseline. A successful push now costs two fetches (drift check, then the
post-push re-read).

**Not covered:** when nothing is staged locally, `push` still returns early without
connecting, so drift goes undetected there. That is safe — no commands are generated
from the stale baseline — but it does mean `status` alone can report `synced` for a
device that has drifted. `status` is deliberately offline (the `git status` analog), so
detecting that would need its own opt-in device read — which is exactly what `c2sync
fetch` is, see CLI surface below. `_detect_drift()` is the small shared helper
(`main.py`) both `_reconcile_out_of_band_drift()` and `fetch()` call, so the two never
disagree on what counts as "changed".

### Partial-push reconciliation (`push`'s error path)

`apply_config` aborts the batch on the first rejected command, but the commands
*before* it are already running on the device. Previously `push` caught
`ConfigApplyError`, printed "nothing was applied" (which was simply false), and exited
without touching state — so the baseline still described the pre-push device and
`status` reported the landed commands as unpushed local edits. That is the bug this
path exists to fix.

**Always, unconditionally:** `_reconcile_after_failed_push()` re-reads the running
config over the still-open session and commits it as the new baseline via
`git_ops.commit_content()`, **deliberately leaving `EDIT_FILE` alone**. The user's
unpushed edits are still what they want, so diffing them against the corrected baseline
yields exactly the commands that did *not* land — which is what `status` then shows.
Reading the device is also strictly more reliable than parsing Netmiko's exception to
infer how many commands landed. `device_dirty` is set when anything landed (those
commands are in running-config, not startup-config). Reconciliation only ever reads from
the device and writes locally, which is what makes it safe to do automatically; if the
re-read itself fails, it says so and tells the user to `pull --force` rather than
leaving them with silently wrong state.

**The re-read is the dangerous part, and it is why `get_running_config()` validates.**
`apply_config` aborts the batch mid-flight, which leaves the session sitting in config
mode with the device's rejection text still unread. The re-read then happens over that
same session immediately afterwards. On real hardware this returned the error text
(`^` / `% Invalid input detected at '^' marker.`) where the running config should have
been, and it was committed verbatim as the new baseline — replacing 242 lines with 2.
`status` then listed the *entire* device config as outstanding, led by `no ^`, and the
next push would have tried to send it. Two things prevent a repeat: `apply_config` now
resets the session (`clear_buffer`, `exit_config_mode`) before raising, and
`get_running_config()` refuses to return anything that isn't a config (see Device
transport above). The existing "could not re-read" branch then does the right thing on
its own — it keeps the last good baseline, which is stale but *true*, and says so.

**Opt-in, via `push --rollback-on-error`:** `_rollback_after_failed_push()` diffs the
live device against the baseline the push *started* from (captured as `pre_push_head`
before the push, since reconciliation moves `HEAD`) and pushes the correction — the same
thing `revert` does by hand. It previews and confirms unless `-y`.

Undoing is **not** the default, and the reason is not implementation cost (`revert`
already had the machinery; wiring it in was a few lines):

- It sends *more* config to a device that just rejected some. The correction can itself
  be rejected, leaving a third state nobody predicted.
- A negation is not always a safe inverse — undoing an address or interface change can
  cut the session doing the undoing. This is the same unmitigated hazard listed under
  Out of current scope ("shutting the interface the session rides on"); making rollback
  automatic would make that hazard fire *unprompted* rather than only when a human
  chose it.
- The partial state is usually the one worth keeping: commands 1-5 landed, command 6 had
  a typo, and the normal fix is to correct the typo and push the remainder.

`status` prints the unsaved-startup-config note alongside staged commands when
`device_dirty` is also set. `state.label` only ever surfaces one flag (`host_dirty`
wins), and both being set is the normal post-partial-push state, so it would otherwise
be invisible.

### CLI surface (`main.py`)

**On the command names.** `push`/`save` were `sync`/`commit` until the rename, and the
pairing is deliberate. `pull`/`push` are symmetric transfer verbs, and `push` states the
direction and consequence that `sync` obscured — this is a one-way write to production
network gear, not a two-way reconciliation.

`save` matters more than `push` did. c2sync is otherwise modeled on git, but this one
command is **not** git's `commit`: it issues `write memory` on the device, persisting
config across a reload. Worse, the ordering is inverted from git's — git is `commit`
then `push`, c2sync is `push` then `save`. Naming it `commit` therefore invited reading
an irreversible device operation as git's cheap, local, reversible one, at the exact
point where the git analogy stops holding. `save` is also the vocabulary IOS operators
already use (`write mem`, `copy run start`). Keep these names out of git's verb space
even though the rest of the tool leans into it — and note `save` still records an empty
git commit via `git_ops.commit_empty()`, so "commit" survives as an implementation
detail, not as the user-facing verb.

Actual commands: `init`, `pull`, `fetch`, `status`, `push`, `save`, `discard`, `revert`.
`pull` connects, fetches `show running-config brief`, writes it to `EDIT_FILE`, and
commits it — this is how an already-configured device gets onboarded (`init` alone only
creates an empty `device.config`, unless `--pull` is also given — see below), and it
doubles as a way to resync the baseline if the device changed out-of-band. It refuses to
run while `host_dirty` unless passed `--force`/`-f` (would silently clobber uncommitted
local edits); when not `host_dirty` it needs no confirmation at all, since there's
nothing local to lose. `pull` has no `-y` — it has no other prompt to skip, so (like
`revert`, see below) the overwrite-approval flag is `--force`/`-f` specifically, never a
generic "don't ask me anything" flag. `fetch` is `pull`'s read-only sibling — same
connect-and-read, but reports what differs from the baseline without writing
`EDIT_FILE`, touching staging, or committing anything; it's the exact same read-and-diff
`push` already does as a pre-flight (`_detect_drift()`, shared by both), just available
on demand instead of only as a side effect of pushing. This is what lets an operator
check for out-of-band drift without either connecting blind during a push or trusting
`status`, which is deliberately offline and so cannot see it (see Out-of-band drift check
above). `status` is read-only (recomputes staging, prints state + preview, never
connects to the device — this is the `git status` analog). `push` checks the device for
out-of-band drift first (see Out-of-band drift check above), sends the staged commands,
then re-fetches `show running-config brief`, writes it to `EDIT_FILE`, and makes a real
git commit in `PROJECT_DIR` (`git_ops.commit()`) — advancing `HEAD` *is* advancing the
baseline now. When the push is rejected partway it does *not* just bail: see Partial-push
reconciliation above. `save` refuses to run while `host_dirty` (would save unintended
state to startup-config), saves running→startup only when `device_dirty`, and records
that milestone as an empty git commit (`git_ops.commit_empty()`) since there's no file
content to stage for it. `discard` reverts `EDIT_FILE` to `device.config` at git `HEAD`
(not just clearing staging) so discarded edits can't get silently re-staged on the next
check.

`init` also runs `git init -b main` in `PROJECT_DIR` and makes the first commit
(empty `device.config`, `c2sync.toml`, and `.gitignore`) — see Project directory model
above for the directory layout, `NAME`/`--dir` resolution, and why re-running `init` on
an existing project refuses rather than overwriting it. `init --pull` runs `pull`'s own
fetch-and-commit logic (`_pull_running_config()`, the shared helper the two call)
immediately afterward, over the same connection — onboarding an already-configured
device in one step instead of two, mirroring `git clone` doing an initial fetch for you
rather than leaving that as a separate manual step after `git init` + adding a remote.
`c2sync` intentionally does not wrap `git log`/`diff`/`branch`/PR review; the same repo
is a normal git repo the user can drive directly with `git` or push to GitHub/GitLab for
review.

`revert [COMMIT] [-y] [--force|-f]` is the manual recovery path for a bad push (e.g. a
batch where command 3 of 8 got rejected after 1-2 already landed) — see Known
constraints. `COMMIT` defaults to `HEAD` (the last confirmed push). Unlike every other
command here, it diffs against a config fetched **fresh from the device right now**,
not `EDIT_FILE` — after something's gone wrong, neither `EDIT_FILE` nor the git
baseline is guaranteed to reflect what's actually running, only the device itself is.
Refuses to run while `host_dirty` unless `--force`/`-f` is passed — reverting
overwrites `EDIT_FILE` with the post-revert device state, which would otherwise
silently discard unpushed local edits. `-y` and `--force` are deliberately independent
flags: `-y` only skips the push-preview confirmation, `--force` is the only thing that
permits overwriting `host_dirty` edits — a user reaching for `-y` just to skip the
prompt shouldn't be able to lose local work as a side effect they didn't ask for.
Sequence: the `host_dirty` check, then `git_ops.resolve_rev()` the target (raises
`git_ops.GitError` on a typo'd commit — this must never silently fall back to an empty
target, which would try to strip the entire device config), `git_ops.show_at()` that
commit's `device.config` (deliberately not the soft-fallback `show_at_head()` — a bad
rev here must be a hard error too), connect and fetch the live running-config,
`Differ.diff_lines(live, target)`, preview and confirm (or `-y`), `apply_config()`. On
success it re-fetches and writes `EDIT_FILE`, same as `push`. Modeled on `git revert`,
not `git reset --hard`: it makes a **new** commit recording the recovered state rather
than rewinding `HEAD`, so the incident stays visible in `git log` (and is a no-op
commit when the recovered content already matches `HEAD`, exactly like
`git_ops.commit()`'s existing "nothing changed" short-circuit that `push`/`pull`
already rely on). `host_dirty`/`device_dirty` transition the same way a successful
`push` does.

`main()` handles help before dispatching, via `_print_help()` and the `COMMAND_HELP`
dict (one entry per command, holding its own usage line, arguments, flags and the
reasoning behind them):

- `help`, `-h`, `--help` as the command, and bare `c2sync`, print the top-level `USAGE`
  to stdout and return 0.
- `c2sync help COMMAND` and `c2sync COMMAND --help` both print that command's entry
  from `COMMAND_HELP`; an unrecognized topic falls back to `USAGE` rather than erroring.
- `-h`/`--help` are matched *anywhere in a command's arguments*, not just the first
  position. This is load-bearing, not defensive: `init` reads its first argument as a
  serial device path, so `c2sync init --help` would otherwise start a project for a
  device literally named `--help`. `help` is deliberately recognized only as the command
  itself, so it stays usable as a `revert` commit-ish.
- An unrecognized command logs an error, prints `USAGE` to **stderr** and exits **1**,
  so a script can tell a typo from a help request.

`test_every_command_has_help_text` asserts `COMMAND_HELP`'s keys match the dispatched
commands exactly, so a new command added to the `match` without help text fails the
suite instead of silently falling back to `USAGE`. The top-level `USAGE` is deliberately one
line per command with no flags — arguments and flags live only in `COMMAND_HELP`, so
there is a single place to edit when they change.

`pull`, `push`, `save`, and `revert` all connect through `_connected()`, a
`@contextmanager` wrapping `_connect()` in `try`/`finally` so `interface.disconnect()`
always runs — including when a call inside the block raises (a rejected push, a
dropped session mid-fetch) or the function returns early — rather than every command
scattering its own `interface.disconnect()` before each exit point. `Project.
edit_file_relpath` (`c2sync/__init__.py`) is the one place `os.path.relpath(EDIT_FILE,
PROJECT_DIR)` is computed — every `git_ops` call site (which run with `git -C
PROJECT_DIR ...`, so need the path relative to it) uses that property instead of
recomputing it.

`_connect()` in `main.py` resolves `username` from `C2SYNC_USERNAME`, then the global
config's `username` key (see Global user config below); `password` only from
`C2SYNC_PASSWORD`. It only skips `input()`/`getpass.getpass()` once both username and
password are resolved — a partially-set environment (or a config file with no
`C2SYNC_PASSWORD` set) falls back to prompting for whatever's still missing, never
half-prompts or hangs on stdin in CI. `C2SYNC_SECRET` is checked the same way as
`C2SYNC_PASSWORD` but is optional either way (`None` if unset, prompted for otherwise).
When it does need to prompt and there is no terminal to prompt on, it exits 1 naming the
env vars to set, rather than letting `EOFError` (or the `termios.error` `getpass` raises
when it can't control echo) out as a traceback — the same applies to `_confirm()`, which
treats an unreadable stdin as a decline, since every one of its callers is about to
write to a live device and "nobody was there to answer" must never resolve to yes.
Passwords/enable-secrets are **never** read from the global config file or stored
anywhere by c2sync itself — env vars are meant to be injected by the CI system's own
secrets manager. Note `push -y` fails closed on out-of-band drift rather than
prompting, which is what keeps a CI job from pushing against a stale baseline;
`--rebase` is the opt-out. Combined with `push -y`/`save -y` (skips the confirmation prompt
too), this is what unblocks the PR-merge-triggers-apply workflow: a CI job that runs
`c2sync push -y` against the device once a config change is reviewed and merged, which
is the actual payoff of tracking device config in git rather than just having a
prettier editing loop. Note this is about a *user's* config repo (a `PROJECT_DIR`
created by `c2sync init`), not this repo's own CI/CD.

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
- `baudrate` — default for `c2sync init NAME SERIAL_DEVICE [BAUDRATE]`'s optional
  argument; an explicit CLI argument still wins.
- `ssh_port` — same, but for `c2sync init NAME --ssh HOST [PORT]`'s optional argument.
- `prompt_for_unknown_ssh_hosts` — **default `False`.** When `True`, an SSH host with no
  entry in `~/.ssh/known_hosts` gets an OpenSSH-style prompt (fingerprint shown, y/n)
  instead of failing closed; accepting adds it to `known_hosts` and retries the
  connection. See Device transport below for why this defaults off.
- `timeout` / `prompt_regex` — passed through to `Project.TIMEOUT`/`PROMPT_REGEX` at
  `init` time if present, otherwise the `Project` dataclass's own defaults apply.

TOML (not JSON) specifically because `tomllib` is stdlib as of Python 3.11 — this is
why `pyproject.toml`'s `requires-python` was bumped from `>=3.9` to `>=3.11`. `tomllib`
is read-only; that's fine here because this file is meant to be hand-edited by the
user, not written by c2sync. A malformed file prints a parse error and exits (`sys.
exit(1)`) rather than silently ignoring it, since — unlike a missing file — a present
but broken config is very likely a real mistake worth surfacing.

### Build and distribution (`build.sh` / `install.sh` / `uninstall.sh`)

Linux only, and deliberately just an archive plus an install script — no `.deb`/`.rpm`
/AUR packaging, no PyPI publish. `build.sh` writes
`dist/c2sync-<version>-linux-<arch>.tar.gz` (arch from `uname -m`; naming it now means
adding a second arch later is additive rather than a rename of published assets)
containing `wheels/` (a wheel for c2sync plus every runtime dependency), `install.sh`,
`uninstall.sh`, `VERSION`, `PYTHON_VERSIONS`, and `SHA256SUMS`. Install does no network
I/O at all, which is the point — these devices usually sit on isolated management
networks.

**The wheelhouse is built for several Python minors at once** (`PY_VERSIONS` in
`build.sh`, currently 3.11/3.12/3.13) and this is load-bearing, not belt-and-braces.
`cryptography`, `bcrypt` and `pynacl` ship `abi3` wheels that work across minors, but
`cffi` and `pyyaml` ship version-specific ones (`cp311-cp311`, `cp313-cp313`, ...). A
wheelhouse downloaded for a single Python version therefore fails to install on any
other, and `install.sh` builds its venv from whatever `python3` the target happens to
have. Vendoring all three costs ~2MB on a ~14MB archive.

Two pip details that constrain this and will bite anyone changing it:

- **No `--platform` flag.** pip matches platform tags exactly rather than by minimum,
  and the tree mixes `manylinux_2_17`/`_2_28`/`_2_34`, so pinning any single platform
  tag makes the resolve fail outright (`ResolutionImpossible`). Tags are inherited from
  the build host instead, which means **the archive is architecture-specific to the
  build host** (x86_64 in practice).
- **`install.sh` installs the c2sync wheel by path with `--find-links` pointing at the
  wheelhouse**, letting pip resolve the dependencies itself and pick tags matching the
  target venv. It must *not* pass the dependency wheels by filename — with a
  multi-version wheelhouse that forces pip to install wheels built for the wrong Python
  minor.

Two packaging traps that were live bugs here and are easy to reintroduce:

- **`pyproject.toml` pins `[tool.setuptools.packages.find]` with `namespaces = false`.**
  Without it, setuptools auto-discovery treats `c2sync/tests` (no `__init__.py`) as a
  namespace package and ships the whole test suite inside the wheel.
- **`pip wheel .` runs with `--no-cache-dir`.** pip caches locally-built wheels, so
  without it a source change with no version bump silently ships a stale c2sync wheel.
  The dependency downloads deliberately still use the cache.

`build.sh` resolves the **test runner separately from the build interpreter** (`PYTEST`
vs `PYTHON`): the interpreter that vendors wheels needs pip, the one that runs tests
needs pytest plus the runtime deps, and those are frequently not the same (a uv-created
`.venv` has no working pip). The `.venv` fallback uses `python -m pytest`, not the bare
`pytest` executable — only the `-m` form puts the repo root on `sys.path`, which is what
lets `from c2sync import ...` resolve without an install.

`install.sh` is **per-user and never calls sudo**: venv in
`${XDG_DATA_HOME:-~/.local/share}/c2sync/venv`, symlink at `~/.local/bin/c2sync`. When a
prerequisite (Python 3.11+, venv support, `git`) is missing it prints the right command
for the detected distro and exits rather than running a package manager itself. It
verifies `SHA256SUMS` first, installs straight from the archive's `wheels/` (no copy
under the install dir — that was 14MB of duplication for nothing), and warns if
`~/.local/bin` isn't on `PATH`. Alpha's version mixed a `$HOME` venv with a
`/usr/local/bin` launcher, which produced a system-wide command hardcoded to one user's
home; the per-user layout is the fix for that.

`uninstall.sh` reads the launcher's symlink target **before** deleting the install
directory (`readlink -f` fails on the resulting dangling link) and removes the launcher
only if it points into `INSTALL_DIR`, so a `c2sync` installed some other way survives.
Project directories are never touched.

`Dockerfile.test` (driven by `build.sh --test-install`, not used at runtime) installs
the built archive as a non-root user, once per supported Python version. It checks both
that `c2sync --help` runs and that `c2sync.main`/`connector`/`differ`/`git_ops` import
— a missing transitive wheel only surfaces at import time, not at `--help`.

### CI/CD (`.github/workflows/`)

Two workflows, both pinned to `ubuntu-24.04` rather than `ubuntu-latest`. That pin is
load-bearing: manylinux wheel selection depends on the build host's glibc, so the runner
image sets the glibc floor of every published archive — on `ubuntu-latest`, GitHub
rolling the image forward could raise that floor and break installs on older targets
with no change in this repo. **The two workflows must stay pinned to the same image**,
or releases get built on a different base than CI tested on.

`ci.yml` — on `pull_request` to `main` and `push` to `main`. Three jobs: `test` (the
suite across Python 3.11/3.12/3.13), `shellcheck` (`build.sh`/`install.sh`/
`uninstall.sh`; shellcheck is preinstalled on GitHub runners, so this adds no repo
config — it is not a general lint setup, and the "no lint/format tooling" note above
still holds for Python), and `package` (`./build.sh --skip-tests --test-install`, which
needs `test` and `shellcheck` to pass first). `--skip-tests` because `test` already ran
the suite on every supported interpreter. The matrix **must stay in step with
`PY_VERSIONS` in `build.sh`** — they are the same claim about which interpreters are
supported, expressed twice.

`release.yml` — on `release: published` (not `created`, which also fires for drafts, nor
`released`, which skips prereleases). It runs `./build.sh --test-install` **with** tests
(an artifact that reaches users is never built from an untested tree), writes an outer
`SHA256SUMS.txt` covering the tarball itself — the `SHA256SUMS` *inside* the archive
covers the bundled wheels and so cannot verify the download — and attaches both with
`gh release upload --clobber`. `contents: write` is scoped to that one job; the rest is
`contents: read`.

Two guards run before the build, and both are there for concrete failure modes:

- **The tagged commit must be contained in `main`.** GitHub lets a release be created
  from any branch or arbitrary tag, so without this a release cut from a feature branch
  publishes as though it were a main build.
- **The tag must match `pyproject.toml`'s version** (`v0.2.0` ↔ `0.2.0`). `build.sh`
  names the artifact from `pyproject.toml` and knows nothing about the git tag, so
  releasing `v0.2.0` while the file still said `0.1.0` would silently attach
  `c2sync-0.1.0-linux-x86_64.tar.gz` to the `v0.2.0` release. Cutting a release
  therefore means bumping `pyproject.toml` on `main` first.

`release.yml` installs `.[dev]` before building because `--test-install` runs the suite;
without it `build.sh`'s `resolve_pytest` finds nothing and aborts. `ci.yml`'s `package`
job does not need it, since it passes `--skip-tests`.

## Known constraints / simplifications

- Cisco IOS only; `device_type` is hardcoded in `connector.py` to the `cisco_ios`
  family (`cisco_ios_serial` on the serial branch — see Device transport above), and
  `Diff(..., syntax='ios')` is hardcoded in `differ.py`.
- Rolling back a partial push (command N of a batch rejected after N-1 landed) is
  **opt-in**, not automatic: `apply_config` aborts the batch, and `push` always
  reconciles local state to what actually landed but does not undo it. `push
  --rollback-on-error` and `c2sync revert` are the two recovery paths — see
  Partial-push reconciliation below for why undoing is not the default.
- No file locking on `state.json`/`staging.txt` — fine for one interactive CLI
  invocation at a time, not safe for concurrent access.
- The release archive is Linux-only and tied to the build host's architecture (see
  Build and distribution above). Distro packages (`.deb`/`.rpm`) and PyPI are possible
  later, deliberately not now.

## Roadmap and active design decisions

Priority order, user-approved. All three have landed:

1. **Real git integration — done.** `init_project` runs `git init -b main` in
   `PROJECT_DIR` and commits the initial empty `device.config`; `push` commits the
   newly-pulled running-config after a confirmed push; `save` (startup-config save)
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
3. **SSH as a second transport — done.** `connector.py`'s `SerialInterface` is renamed
   `DeviceInterface` and branches on `project.TRANSPORT` (`'serial'`/`'ssh'`) to build
   Netmiko's `serial_settings={...}` or `host=..., port=...` kwargs — everything else in
   `DeviceInterface` was already transport-independent. `Project` gained `TRANSPORT`,
   `HOST`, `SSH_PORT` (`SERIAL_DEVICE`/`BAUDRATE` now only apply when `TRANSPORT ==
   'serial'`) and a `.target` property so callers don't branch on transport themselves.
   `c2sync init NAME --ssh HOST [PORT]` is the new CLI entry point alongside the existing
   `c2sync init NAME SERIAL_DEVICE [BAUDRATE]`. `differ.py`/`state_engine.py`/the rest of
   `main.py` needed zero changes, confirming they really were transport-agnostic already
   (they operate on `Project` and CLI text, never on `DeviceInterface` internals).

### Out of current scope

Wanted, but not built and not currently in progress. **None of these are closed doors**
— they are scoping decisions about sequencing, not rejections. The rule is only that
each should be built deliberately, as its own piece of work, rather than half-emerging
as a side effect of an unrelated change. `README.md`'s "Potential Future Features" is
the user-facing write-up of what each would involve.

- **Multi-device support.** One project directory is one device today. A fleet registry
  was the project's original goal and remains a wanted direction; the current
  single-device model is the revamp's starting scope, not a verdict on the idea. The
  main open design question is one repo for the fleet versus one repo per device — see
  `README.md` for the trade-off.
- **Multi-vendor support.** Cisco IOS only today, and a documented future direction
  (see Known constraints above, and `README.md` for the per-vendor analysis: NX-OS is
  the realistic near-term target, JunOS a much bigger lift).
- **Dry-run against a simulator.** A real safety gap rather than a feature idea:
  nothing catches a command that is syntactically valid but operationally destructive,
  such as shutting the interface the session rides on. `c2sync revert` is today's
  recovery path, which is mitigation after the fact rather than prevention.

## Docs drift to be aware of

`README.md` was reconciled with the actual implementation (real CLI surface, single
device per project, git integration, credentials/global config) after Priority 1
landed. Still, treat `main.py` as ground truth over `README.md` if they ever
disagree again — reconciling docs after each roadmap item lands is the intended
cadence, not a one-time fix.
