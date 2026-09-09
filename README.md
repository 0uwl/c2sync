## C2Sync – Console Configuration Synchronizer

## Overview

C2Sync is a Python-based CLI tool that acts as a **middleman between a Cisco IOS device (over console/serial) and a local git repository**.

The tool lets you:

* Track a device's running configuration in a local git repository
* Edit the configuration locally using the text editor of your choice (e.g. VS Code)
* Diff your edits against a real Cisco IOS configuration tree, including deletions, not just added lines
* Push changes back to the device over the serial connection, verified against the device's own response before anything is considered synced
* Save the running configuration to the startup configuration

Console-only, Cisco IOS only, one device per project — a deliberate starting scope, not an oversight. SSH transport and multi-vendor support are designed-for future extensions, not current scope.

## Core Design Principles

* Focus on simplicity, reliability, and CLI correctness
* Simplify the experience of managing device config over a console connection
* Every project is a real git repository — c2sync commits at defined lifecycle points (confirmed sync/commit), but doesn't reimplement `diff`/`log`/`branch`/PR review. Use your normal git tooling directly against the project directory, and push it to GitHub/GitLab for review like any other repo
* Users should already be comfortable with Cisco IOS CLI syntax

## Key Features

Each `c2sync init` creates a project (`./.c2sync/`) for **one device**. The project directory is a real git repository:

* `device.config` — the file you edit, tracked in git with one commit per confirmed sync (plus an empty commit marking each save to startup-config)
* Local edits are diffed against the last confirmed sync — `device.config` as of git `HEAD` — on demand, not via a background watcher, the same model as `git status`
* Staged changes are rebuilt into Cisco IOS CLI commands that respect configuration context (see Local Editing below)
* Pushes are verified against the device's own response; a rejected command aborts the whole push instead of partially applying

## Installation

Not published to PyPI yet — install from a checkout of this repository:

```bash
pip install -e .
```

Requires Python 3.11+ (for reading the optional global config file, see Configuration below) and a real or mocked serial connection for anything beyond `init`/`status`/`discard` — `pull`/`sync`/`commit` all need to reach the device.

## Usage
### CLI Commands
```
c2sync COMMAND

Commands:
  init      SERIAL_DEVICE [BAUDRATE]  Start a project for one device in the current directory
  pull      [-y]                      Fetch the device's running config and make it the new baseline
  status                              Show whether there are unsynced local edits or an unsaved device change
  sync      [-y]                      Preview and push staged changes to the device
  commit    [-y]                      Save the device's running config to its startup config
  discard                             Revert local edits back to the last confirmed sync
```

## General workflow

### 1. Init

```
c2sync init SERIAL_DEVICE [BAUDRATE]
```
Behavior:
* Creates a new project in the current working directory (`./.c2sync/`)
* Initializes a git repository there and makes the first commit (an empty `device.config`)
* `BAUDRATE` defaults to 9600, or to the global config's `baudrate` if set (see Configuration below)

### 2. Pull

```
c2sync pull [-y]
```
Behavior:
* Connects to the device, fetches the running config, and commits it as the new baseline — this is how you onboard a device that's already configured (`init` alone only creates an empty `device.config`)
* Also useful later to resync the baseline if the device changed outside of C2Sync
* Refuses to run if you have unsynced local edits, unless `-y` is passed to overwrite them

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
timeout = 600
prompt_regex = '[>#]\s?$'
```

Every key is optional and already has a working default without this file. Passwords and enable-secrets are intentionally never stored here — see Credentials above.

## Potential Future Features

Ideas that have come up but aren't built or scheduled — see `CLAUDE.md`'s Roadmap for what's actually in progress.

### Multi-vendor support

C2Sync is Cisco IOS only today. Supporting another vendor means more than swapping Netmiko's `device_type` — the diff engine and the device's own workflow both matter:

* **NX-OS** — the more realistic near-term target. `ciscoconfparse2`'s diff engine (`hier_config`) already treats `nxos` as a first-class syntax rather than a fallback, and NX-OS keeps the same running-config/startup-config duality as IOS classic, so C2Sync's `sync`-then-`commit` model and state tracking would carry over largely unchanged. Would still need `device_type='cisco_nxos'`, plus NX-OS-specific error/save-confirmation patterns in `connector.py` — its "invalid command" and `copy run start` output wording differs from classic IOS.
* **JunOS** — a bigger lift. `ciscoconfparse2` parses JunOS config into a correct tree, but its diff/remediation engine currently falls back to IOS rules for `syntax='junos'` rather than real JunOS logic, and produces invalid syntax (`no set ...` instead of JunOS's `delete ...`). JunOS's candidate/commit model also has no separate running-vs-startup-config step the way IOS does, so the `sync`/`commit` split and `state_engine.py`'s dirty-state tracking would need real rework, not just a new device type.

## Disclaimer
* This tool assumes familiarity with network device CLI. You must adhere to Cisco IOS' configuration syntax
* The tool does not validate commands before sending. You must review the preview yourself before confirming a `sync`

## License
MIT
