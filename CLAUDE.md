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
c2sync init /dev/ttyUSB0 [BAUDRATE]     # serial transport
c2sync init --ssh HOST [PORT]           # SSH transport
c2sync pull [--force|-f]
c2sync status
c2sync sync [-y]
c2sync commit [-y]
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

`c2sync init SERIAL_DEVICE [BAUDRATE]` (serial) or `c2sync init --ssh HOST [PORT]`
(SSH) creates `./.c2sync/` holding the entire state for **one device** — there is
currently no multi-device registry — a wanted future direction that is simply not built
yet, not a rejected one (see Out of current scope below):

- `device.config` (`EDIT_FILE`) — what the user edits in their text editor. Tracked in
  a real git repo (`git init` inside `PROJECT_DIR` at `init` time) — the baseline is no
  longer a separate file, it's `device.config` as of git `HEAD` (see On-demand change
  detection below).
- `staging.txt` (`STAGING_FILE`) — the recomputed CLI commands to push, fully
  overwritten (not appended) on every check.
- `state.json` (`STATE_FILE`) — `{host_dirty, device_dirty}`, see State tracking below.
- `c2sync.config` (`CONFIG_FILE`) — the serialized `Project` dataclass (transport,
  serial device path/baudrate or SSH host/port, timeout, all the above paths).

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
| `c2sync/connector.py` | `DeviceInterface` — Netmiko `ConnectHandler` wrapper (serial or SSH) |
| `c2sync/differ.py` | `Differ` — real config-tree diff (`ciscoconfparse2`) → CLI commands |
| `c2sync/git_ops.py` | Thin `git` subprocess wrapper — `init`/`commit`/`commit_empty`/`show_at_head` |
| `c2sync/user_config.py` | Reads the optional global TOML preferences file — `load()`/`config_path()` |
| `c2sync/state_engine.py` | `StateEngine` — `host_dirty`/`device_dirty` tracking |
| `c2sync/exceptions.py` | `C2SyncError`, `ConfigApplyError`, `ConfigSaveError`, `HostKeyRejectedError` |
| `c2sync/main.py` | CLI entry point: `init` / `pull` / `status` / `sync` / `commit` / `discard` / `revert` |

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

Two things this wrapper adds on top of raw Netmiko, transport-independent:

- `apply_config()` passes an IOS `error_pattern` to `send_config_set()`, so a rejected
  command raises `ConfigApplyError` instead of being silently pushed with the rest of
  the batch (Netmiko does not raise on command errors by default without this).
- `save_config()` checks the device's response for IOS's `[OK]` marker before
  returning, raising `ConfigSaveError` if the save wasn't actually confirmed.

`main.py`'s `sync`/`commit` only advance state (clear staging, mark `device_dirty`,
commit the new baseline to git, mark `device_dirty` clean) after these confirmed
returns — never optimistically.

### CLI surface (`main.py`)

Actual commands: `init`, `pull`, `status`, `sync`, `commit`, `discard`, `revert`. `pull`
connects, fetches `show running-config brief`, writes it to `EDIT_FILE`, and commits it
— this is how an already-configured device gets onboarded (`init` alone only creates an
empty `device.config`), and it doubles as a way to resync the baseline if the device
changed out-of-band. It refuses to run while `host_dirty` unless passed `--force`/`-f`
(would silently clobber uncommitted local edits); when not `host_dirty` it needs no
confirmation at all, since there's nothing local to lose. `pull` has no `-y` — it has no
other prompt to skip, so (like `revert`, see below) the overwrite-approval flag is
`--force`/`-f` specifically, never a generic "don't ask me anything" flag. `status` is read-only
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

`revert [COMMIT] [-y] [--force|-f]` is the manual recovery path for a bad push (e.g. a
batch where command 3 of 8 got rejected after 1-2 already landed) — see Known
constraints. `COMMIT` defaults to `HEAD` (the last confirmed sync). Unlike every other
command here, it diffs against a config fetched **fresh from the device right now**,
not `EDIT_FILE` — after something's gone wrong, neither `EDIT_FILE` nor the git
baseline is guaranteed to reflect what's actually running, only the device itself is.
Refuses to run while `host_dirty` unless `--force`/`-f` is passed — reverting
overwrites `EDIT_FILE` with the post-revert device state, which would otherwise
silently discard unsynced local edits. `-y` and `--force` are deliberately independent
flags: `-y` only skips the push-preview confirmation, `--force` is the only thing that
permits overwriting `host_dirty` edits — a user reaching for `-y` just to skip the
prompt shouldn't be able to lose local work as a side effect they didn't ask for.
Sequence: the `host_dirty` check, then `git_ops.resolve_rev()` the target (raises
`git_ops.GitError` on a typo'd commit — this must never silently fall back to an empty
target, which would try to strip the entire device config), `git_ops.show_at()` that
commit's `device.config` (deliberately not the soft-fallback `show_at_head()` — a bad
rev here must be a hard error too), connect and fetch the live running-config,
`Differ.diff_lines(live, target)`, preview and confirm (or `-y`), `apply_config()`. On
success it re-fetches and writes `EDIT_FILE`, same as `sync`. Modeled on `git revert`,
not `git reset --hard`: it makes a **new** commit recording the recovered state rather
than rewinding `HEAD`, so the incident stays visible in `git log` (and is a no-op
commit when the recovered content already matches `HEAD`, exactly like
`git_ops.commit()`'s existing "nothing changed" short-circuit that `sync`/`pull`
already rely on). `host_dirty`/`device_dirty` transition the same way a successful
`sync` does.

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

`pull`, `sync`, `commit`, and `revert` all connect through `_connected()`, a
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
Passwords/enable-secrets are **never** read from the global config file or stored
anywhere by c2sync itself — env vars are meant to be injected by the CI system's own
secrets manager. Combined with `sync -y`/`commit -y` (skips the confirmation prompt
too), this is what unblocks the PR-merge-triggers-apply workflow: a CI job that runs
`c2sync sync -y` against the device once a config change is reviewed and merged, which
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
- `baudrate` — default for `c2sync init SERIAL_DEVICE [BAUDRATE]`'s optional argument;
  an explicit CLI argument still wins.
- `ssh_port` — same, but for `c2sync init --ssh HOST [PORT]`'s optional argument.
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
- No *automatic* rollback if command N of a multi-command batch is rejected after
  N-1 already landed on the device — `apply_config` aborts the batch but doesn't
  undo what already applied. `c2sync revert` (see CLI surface above) is the manual
  recovery path: it diffs the live device against a past commit and pushes the
  correction.
- No file locking on `state.json`/`staging.txt` — fine for one interactive CLI
  invocation at a time, not safe for concurrent access.
- The release archive is Linux-only and tied to the build host's architecture (see
  Build and distribution above). Distro packages (`.deb`/`.rpm`) and PyPI are possible
  later, deliberately not now.

## Roadmap and active design decisions

Priority order, user-approved. All three have landed:

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
3. **SSH as a second transport — done.** `connector.py`'s `SerialInterface` is renamed
   `DeviceInterface` and branches on `project.TRANSPORT` (`'serial'`/`'ssh'`) to build
   Netmiko's `serial_settings={...}` or `host=..., port=...` kwargs — everything else in
   `DeviceInterface` was already transport-independent. `Project` gained `TRANSPORT`,
   `HOST`, `SSH_PORT` (`SERIAL_DEVICE`/`BAUDRATE` now only apply when `TRANSPORT ==
   'serial'`) and a `.target` property so callers don't branch on transport themselves.
   `c2sync init --ssh HOST [PORT]` is the new CLI entry point alongside the existing
   `c2sync init SERIAL_DEVICE [BAUDRATE]`. `differ.py`/`state_engine.py`/the rest of
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
