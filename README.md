## C2Sync – Console Configuration Synchronizer

## Overview

C2Sync is a Python-based CLI tool that acts as a **middleman between a Cisco IOS device (over console/serial) and a local git repository**.

The tool lets you:

* Track a device's running configuration in a local git repository
* Edit the configuration locally using the text editor of your choice (e.g. VS Code)
* Rebuild the Cisco IOS configuration context structure through a simple indentation-based algorithm
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

Requires Python 3.11+ (for reading the optional global config file, see Configuration below) and a real or mocked serial connection for anything beyond `init`/`status`/`discard`.

## Usage
### CLI Commands
```
c2sync COMMAND

Commands:
  init      SERIAL_DEVICE [BAUDRATE]  Start a project for one device in the current directory
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

### 2. Local Editing

The user edits `./.c2sync/device.config` with the text editor of their choice. When editing the file, the user should still adhere to the rules of Cisco IOS CLI configuration. This means that to delete a line, simply removing it from the file will not work — deletions are not detected at all today. Instead, do as you would in the CLI and add a negation command (`no ...`). If a line is just deleted, it's silently ignored, and the next time the config is pulled from the device, the line will reappear.

When `status`/`sync` recompute staging, C2Sync rebuilds the configuration context to produce the exact commands that would be sent, written to `./.c2sync/staging.txt`.

Example:
```
interface GigabitEthernet1/0/1
 description Server
 switchport mode access
 switchport access vlan 100
```
To remove the description and disable link negotiation with C2Sync, you edit this interface like this:
```
interface GigabitEthernet1/0/1
 no description Server
 switchport mode access
 switchport access vlan 100
 switchport nonegotiate
```
C2Sync diffs the original against the changed file and picks up the added/changed lines. Cisco IOS configurations have hierarchical context that must be included alongside the changed lines, so C2Sync rebuilds it by walking upward through the file until it reaches a line with less leading whitespace. The resulting staged commands look like this:
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

> [!NOTE]
> _This means that you must be mindful of spaces to declare contexts properly_

### 3. Status

```
c2sync status
```
Behavior:
* Shows whether the local file has unsynced edits (`host pending changes`), the device has pushed-but-unsaved changes (`device pending changes`), or everything is `synced`
* Previews the exact CLI commands that `sync` would send
* Read-only — never connects to the device

### 4. Sync

```
c2sync sync [-y]

Options:
    -y    Push without an interactive confirmation prompt
```
Behavior:
* Displays the commands that will be sent, then pushes them if confirmed
* Re-fetches the running config and commits it to the project's git repository — this becomes the new baseline for the next diff

### 5. Commit

```
c2sync commit [-y]
```
Behavior:
* Saves the device's running config to its startup config
* Refuses to run while there are unsynced local edits — run `sync` first
* Records the save as a git commit (no file content changes, so it's an empty commit marking the milestone)

### 6. Discard

```
c2sync discard
```
Behavior:
* Reverts local edits back to the last confirmed sync (`device.config` at git `HEAD`) and clears anything staged

## Credentials

`sync`/`commit` need to log in to the device. In order of precedence:
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

## Disclaimer
* This tool assumes familiarity with network device CLI. You must adhere to Cisco IOS' configuration syntax
* The tool does not validate commands before sending. You must review the preview yourself before confirming a `sync`

## License
MIT
