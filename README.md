## C2Sync – CLI Config Synchronizer

## Overview

C2Sync is a Python-based CLI tool that acts as a **middleman between a Cisco IOS device (over serial console or SSH) and a local git repository**.

The tool lets you:

* Track a device's running configuration in a local git repository
* Edit the configuration locally using the text editor of your choice (e.g. VS Code)
* Diff your edits against a real Cisco IOS configuration tree, including deletions, not just added lines
* Push changes back to the device over serial or SSH, verified against the device's own response before anything is considered synced
* Save the running configuration to the startup configuration

Cisco IOS only, one device per project — a deliberate starting scope, not an oversight. Multi-vendor support is a possible future direction, not current scope (see Potential Future Features below).

## Core Design Principles

* Focus on simplicity, reliability, and CLI correctness
* Simplify the experience of managing device config over serial or SSH
* Every project is a real git repository — c2sync commits at defined lifecycle points (confirmed sync/commit), but doesn't reimplement `diff`/`log`/`branch`/PR review. Use your normal git tooling directly against the project directory, and push it to GitHub/GitLab for review like any other repo
* Users should already be comfortable with Cisco IOS CLI syntax

## Key Features

Each `c2sync init` creates a project (`./.c2sync/`) for **one device**. The project directory is a real git repository:

* `device.config` — the file you edit, tracked in git with one commit per confirmed sync (plus an empty commit marking each save to startup-config)
* Local edits are diffed against the last confirmed sync — `device.config` as of git `HEAD` — on demand, not via a background watcher, the same model as `git status`
* Staged changes are rebuilt into Cisco IOS CLI commands that respect configuration context (see Local Editing below)
* Pushes are verified against the device's own response; a rejected command aborts the whole push instead of partially applying

## Installation

Not published to PyPI or any distro repository yet. There are two ways in.

### From a release archive (Linux)

`build.sh` produces a single `.tar.gz` holding a wheel for c2sync, wheels for every
runtime dependency, and an installer. Nothing is fetched from the network at install
time, which matters for the isolated management networks these devices usually sit on.

```bash
tar -xzf c2sync-0.1.0.tar.gz
./c2sync-0.1.0/install.sh
```

This installs entirely under your home directory and **never needs root**:

| Path | Contents |
|---|---|
| `~/.local/share/c2sync/venv` | Private virtualenv holding c2sync and its dependencies |
| `~/.local/bin/c2sync` | Symlink to the launcher in that venv |

The installer checks its prerequisites (Python 3.11+, `python3-venv`, `git`) and, if
one is missing, prints the install command for your distribution and stops — it never
invokes `sudo` or a package manager on your behalf. It also verifies the bundled
wheels against `SHA256SUMS` before installing. If `~/.local/bin` isn't on your `PATH`
it will say so and tell you how to add it.

To remove it, run `./c2sync-0.1.0/uninstall.sh` (`-y` to skip the prompt). It deletes
only the two paths above — your project directories and their git history are left
alone.

### From a checkout

```bash
pip install -e ".[dev]"    # omit [dev] if you don't need the test suite
```

Requires Python 3.11+ (for reading the optional global config file, see Configuration
below) and a real or mocked device connection for anything beyond `init`/`status`/
`discard` — `pull`/`sync`/`commit` all need to reach the device, over serial or SSH.

## Building a release archive

```bash
./build.sh                    # test, build, and write dist/c2sync-<version>.tar.gz
./build.sh --skip-tests       # skip the test suite
./build.sh --test-install     # additionally install the result in clean containers
```

The archive is roughly 14MB and bundles wheels for Python 3.11, 3.12 and 3.13. Most of
the dependency tree is pure-python or `abi3`, but `cffi` and `pyyaml` publish
version-specific binary wheels, so a wheelhouse built for one Python minor will not
install on another. Vendoring all three costs about 2MB and lets the target's own pip
select matching tags.

Two constraints worth knowing:

* **Architecture follows the build host.** pip matches platform tags exactly rather
  than by minimum, and the tree mixes `manylinux_2_17`/`_2_28`/`_2_34` wheels, so
  pinning `--platform` breaks resolution. Build on x86_64 for x86_64 targets.
* **The build host needs an interpreter with pip.** Set `PYTHON` if the default
  `python3` doesn't have one (`PYTHON=/usr/bin/python3 ./build.sh`). The test runner is
  resolved separately via `PYTEST`, since the interpreter with pip and the one with
  pytest are often not the same.

`--test-install` builds a container per supported Python version, installs the archive
as a non-root user, and checks that the command runs and the package imports — a
missing transitive wheel only surfaces at import time, not at launch.

## Usage
### CLI Commands
```
c2sync COMMAND

Commands:
  init      SERIAL_DEVICE [BAUDRATE]  Start a project for one device over serial
  init      --ssh HOST [PORT]         Start a project for one device over SSH
  pull      [--force|-f]               Fetch the device's running config and make it the new baseline
  status                              Show whether there are unsynced local edits or an unsaved device change
  sync      [-y]                      Preview and push staged changes to the device
  commit    [-y]                      Save the device's running config to its startup config
  discard                             Revert local edits back to the last confirmed sync
  revert    [COMMIT] [-y] [--force|-f]  Push the device back to a past commit (default: HEAD)
  help                                Show this message (also -h, --help)
```

`-h`/`--help` work anywhere on the line, so `c2sync init --help` shows usage rather than
being read as a device path. Bare `c2sync` prints the same thing. An unrecognized
command prints usage to stderr and exits 1.

## General workflow

### 1. Init

```
c2sync init SERIAL_DEVICE [BAUDRATE]
c2sync init --ssh HOST [PORT]
```
Behavior:
* Creates a new project in the current working directory (`./.c2sync/`) for one device, reached over serial or SSH
* Initializes a git repository there and makes the first commit (an empty `device.config`)
* Serial: `BAUDRATE` defaults to 9600, or to the global config's `baudrate` if set (see Configuration below)
* SSH: `PORT` defaults to 22, or to the global config's `ssh_port` if set. Host keys are verified against your `~/.ssh/known_hosts`, same as a plain `ssh` client — trust the device's key there first (e.g. `ssh user@host` once) if you haven't already, or C2Sync will refuse to connect

### 2. Pull

```
c2sync pull [--force|-f]
```
Behavior:
* Connects to the device, fetches the running config, and commits it as the new baseline — this is how you onboard a device that's already configured (`init` alone only creates an empty `device.config`)
* Also useful later to resync the baseline if the device changed outside of C2Sync
* Refuses to run if you have unsynced local edits, unless `--force`/`-f` is passed to overwrite them — there's no `-y` here, since pull has no other prompt to skip; a flag that only means "overwrite my local edits" shouldn't be spelled the same as "don't ask me anything"

### 3. Local Editing

The user edits `./.c2sync/device.config` with the text editor of their choice, using normal Cisco IOS CLI syntax. Just delete a line to remove it — C2Sync parses the config into a real tree (via `ciscoconfparse2`) and generates the correct `no <command>` for you; you don't need to type the negation yourself, though it still works fine if you do.

When `status`/`sync` recompute staging, C2Sync diffs the parsed tree against the last confirmed baseline and writes the exact commands that would be sent to `./.c2sync/staging.txt`.

Example:
```
interface GigabitEthernet1/0/1
 description Server
 switchport mode access
 switchport access vlan 100
```
To remove the description and disable link negotiation, just edit the interface like this:
```
interface GigabitEthernet1/0/1
 switchport mode access
 switchport access vlan 100
 switchport nonegotiate
```
C2Sync stages exactly the commands needed to make that change, with the interface context included once:
```
interface GigabitEthernet1/0/1
 no description Server
 switchport nonegotiate
```
This preserves context and means the entire configuration doesn't need to be sent back to the device every time changes are pushed. After `sync`, the device's config would look like this:
```
interface GigabitEthernet1/0/1
 switchport mode access
 switchport access vlan 100
 switchport nonegotiate
```

### 4. Status

```
c2sync status
```
Behavior:
* Shows whether the local file has unsynced edits (`host pending changes`), the device has pushed-but-unsaved changes (`device pending changes`), or everything is `synced`
* Previews the exact CLI commands that `sync` would send
* Read-only — never connects to the device

### 5. Sync

```
c2sync sync [-y]

Options:
    -y    Push without an interactive confirmation prompt
```
Behavior:
* Displays the commands that will be sent, then pushes them if confirmed
* Re-fetches the running config and commits it to the project's git repository — this becomes the new baseline for the next diff

### 6. Commit

```
c2sync commit [-y]
```
Behavior:
* Saves the device's running config to its startup config
* Refuses to run while there are unsynced local edits — run `sync` first
* Records the save as a git commit (no file content changes, so it's an empty commit marking the milestone)

### 7. Discard

```
c2sync discard
```
Behavior:
* Reverts local edits back to the last confirmed sync (`device.config` at git `HEAD`) and clears anything staged

### 8. Revert

```
c2sync revert [COMMIT] [-y] [--force|-f]
```
Behavior:
* Recovers a device that's ended up in a bad state — e.g. a `sync` where one command in the middle of a batch got rejected after earlier ones already landed
* Fetches the running config from the device **right now** and diffs it against `COMMIT` (a past commit's `device.config`, `HEAD` if omitted) — not your local `device.config`, since after something's gone wrong that file isn't guaranteed to reflect what's actually running either
* Displays the commands needed to bring the device back to that commit's config, then pushes them if confirmed (or `-y`)
* Refuses to run if you have unsynced local edits, unless `--force`/`-f` is passed — reverting overwrites `device.config` with the post-revert device state, which would otherwise silently lose those edits. `-y` and `--force` are separate on purpose: `-y` only skips the push confirmation, `--force` is what's required to overwrite local edits — so skipping the prompt can never lose work by accident
* Records the recovery as a **new** git commit rather than moving `HEAD` backward, like `git revert` rather than `git reset --hard` — the incident stays visible in `git log` instead of being erased
* If the device already matches the target commit, it says so and doesn't push anything

## Credentials

`pull`/`sync`/`commit` need to log in to the device. In order of precedence:
1. `C2SYNC_USERNAME` / `C2SYNC_PASSWORD` / `C2SYNC_SECRET` environment variables
2. `username` from the global config file (see Configuration below) — password and enable-secret are never read from there
3. An interactive prompt for whatever's still missing

For CI/non-interactive use (e.g. a merged PR triggering `c2sync sync -y`), set `C2SYNC_USERNAME` and `C2SYNC_PASSWORD` from your CI system's own secrets manager. C2Sync never stores credentials itself, in this file or anywhere else.

## Configuration

An optional TOML file at `~/.config/c2sync/config.toml` (or `$XDG_CONFIG_HOME/c2sync/config.toml`) holds non-secret preferences shared across all your projects:

```toml
username = "admin"
baudrate = 115200
ssh_port = 22
prompt_for_unknown_ssh_hosts = false
timeout = 600
prompt_regex = '[>#]\s?$'
```

Every key is optional and already has a working default without this file. Passwords and enable-secrets are intentionally never stored here — see Credentials above.

`prompt_for_unknown_ssh_hosts` defaults to `false`: an SSH host not already in your `~/.ssh/known_hosts` fails to connect (see Init above) rather than prompting. Set it to `true` to get an OpenSSH-style prompt instead (fingerprint shown, `yes`/`no`), and a `yes` adds the key to `known_hosts` for next time. A host whose key *changed* (as opposed to one that's simply new) always fails hard either way; this setting only ever affects genuinely first-time connections.

## Potential Future Features

Ideas that have come up but aren't built or scheduled — see `CLAUDE.md`'s Roadmap for what's actually in progress.

### Multi-vendor support

C2Sync is Cisco IOS only today. Supporting another vendor means more than swapping Netmiko's `device_type` — the diff engine and the device's own workflow both matter:

* **NX-OS** — the more realistic near-term target. `ciscoconfparse2`'s diff engine (`hier_config`) already treats `nxos` as a first-class syntax rather than a fallback, and NX-OS keeps the same running-config/startup-config duality as IOS classic, so C2Sync's `sync`-then-`commit` model and state tracking would carry over largely unchanged. Would still need `device_type='cisco_nxos'`, plus NX-OS-specific error/save-confirmation patterns in `connector.py` — its "invalid command" and `copy run start` output wording differs from classic IOS.
* **JunOS** — a bigger lift. `ciscoconfparse2` parses JunOS config into a correct tree, but its diff/remediation engine currently falls back to IOS rules for `syntax='junos'` rather than real JunOS logic, and produces invalid syntax (`no set ...` instead of JunOS's `delete ...`). JunOS's candidate/commit model also has no separate running-vs-startup-config step the way IOS does, so the `sync`/`commit` split and `state_engine.py`'s dirty-state tracking would need real rework, not just a new device type.

### Pushing to a remote

Considered, and deliberately not built: auto-pushing the project's git repo to a remote after a confirmed `sync`/`commit`. Git already solves this better than C2Sync could — add a `post-commit` hook and every commit C2Sync makes gets mirrored automatically, with no new credential surface (it reuses whatever git push auth you already have set up) and no risk of a push failure ever affecting a device push that already succeeded:

```bash
cat > .c2sync/.git/hooks/post-commit <<'EOF'
#!/bin/sh
git push
EOF
chmod +x .c2sync/.git/hooks/post-commit
```

## Disclaimer
* This tool assumes familiarity with network device CLI. You must adhere to Cisco IOS' configuration syntax
* The tool does not validate commands before sending. You must review the preview yourself before confirming a `sync`

## License
MIT
