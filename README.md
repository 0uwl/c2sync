## C2Sync – CLI Config Synchronizer

## Overview

C2Sync is a Python-based CLI tool that acts as a **middleman between a Cisco IOS device (over serial console or SSH) and a local git repository**.
Essentially treating the device as a remote repository to push and pull its configuration from.

The tool lets you:

* Track a device's running configuration in a local git repository
* Edit the configuration locally using the text editor of your choice (e.g. VS Code)
* Diff your edits against a real Cisco IOS configuration tree, including deletions, not just added lines
* Push changes back to the device over serial or SSH, verified against the device's own response before anything is considered applied
* Save the running configuration to the startup configuration

Cisco IOS only, one device per project — a deliberate starting scope, not an oversight. Both multi-device and multi-vendor support are wanted future directions rather than rejected ones; they are just not current scope (see Potential Future Features below).

## Core Design Principles

* Focus on simplicity, reliability, and CLI correctness
* Simplify the experience of managing device config over serial or SSH
* Every project is a real git repository. c2sync commits at defined lifecycle points (confirmed push/save), but doesn't reimplement `diff`/`log`/`branch`/PR review. Use your normal git tooling directly against the project directory, and push it to GitHub/GitLab for review like any other repo
* Users should already be comfortable with Cisco IOS CLI syntax

## Key Features

Each `c2sync init NAME ...` creates a project (`./NAME/` by default, or `--dir PATH`) for **one device**. The project directory is a real git repository, and only its operational scratch state (`.c2sync/`) is hidden — `device.config` (the file you actually edit) and `c2sync.toml` (the device's connection info) both sit right at the top level next to `.git/`, the same way any other git-tracked file does:

* `device.config` — the file you edit, tracked in git with one commit per confirmed push (plus an empty commit marking each save to startup-config)
* `c2sync.toml` — the device's connection info (transport, serial port/host, baud rate/SSH port, timeout), also tracked in git. This is what makes `git clone`ing a project directory actually work: connection details travel with the repo instead of living only in local, gitignored state, and no secret ever lives here — credentials are never stored by c2sync at all (see Credentials below)
* Local edits are diffed against the last confirmed push — `device.config` as of git `HEAD` — on demand, same as `git status`
* Staged changes are rebuilt into Cisco IOS CLI commands that respect configuration context (see Local Editing below)
* Pushes are verified against the device's own response; a rejected command aborts the rest of the push, but leaves successful commands on the device
* `c2sync fetch` checks the device for drift without changing any local state, closing the one gap the otherwise-offline `status` can't see on its own
* If the device changed out-of-band since the last push, `push` reconciles it with a real three-way merge (like `git merge` on any text file) — disjoint changes merge and proceed automatically, a real conflict stops with standard conflict markers for you to resolve by hand

## Installation

Not published to PyPI or any distro repository yet. There are two ways in.

### From a release archive (Linux)

Download the archive for your architecture from the
[releases page](https://github.com/0uwl/c2sync/releases), along with `SHA256SUMS.txt`
if you want to verify it (`sha256sum -c SHA256SUMS.txt`). Releases are built and
install-tested by CI, never uploaded by hand.

The archive holds a wheel for c2sync, wheels for every runtime dependency, and an
installer. Nothing is fetched from the network at install
time, which matters for the isolated management networks these devices usually sit on.

```bash
tar -xzf c2sync-0.1.0-linux-x86_64.tar.gz
./c2sync-0.1.0-linux-x86_64/install.sh
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

To remove it, run `./c2sync-0.1.0-linux-x86_64/uninstall.sh` (`-y` to skip the prompt). It deletes
only the two paths above — your project directories and their git history are left
alone.

### From a checkout

```bash
pip install -e ".[dev]"    # omit [dev] if you don't need the test suite
```

Requires Python 3.11+ (for reading the optional global config file, see Configuration
below) and a real or mocked device connection for anything beyond `init`/`status`/
`discard` — `pull`/`push`/`save` all need to reach the device, over serial or SSH.

## Building a release archive

```bash
./build.sh                    # test, build, write dist/c2sync-<version>-linux-<arch>.tar.gz
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

## Continuous integration

Two workflows, both on `ubuntu-24.04`.

**CI** (`.github/workflows/ci.yml`) runs on pull requests to `main` and on pushes to
`main`:

* the test suite against Python 3.11, 3.12 and 3.13
* `shellcheck` over `build.sh`, `install.sh` and `uninstall.sh`
* a full `./build.sh --skip-tests --test-install`, which builds the archive and installs
  it in a clean container per supported Python version

The built archive is uploaded as a workflow artifact, so a PR build can be downloaded
and tried by hand before merging.

**Release** (`.github/workflows/release.yml`) runs when a GitHub release is published.
It rebuilds the archive from scratch *with* tests, checksums it, and attaches the
archive and `SHA256SUMS.txt` to the release. Two things have to hold or the release
fails:

* the tagged commit is contained in `main`
* the tag matches the version in `pyproject.toml` (`v0.2.0` ↔ `version = "0.2.0"`)

The second one matters because `build.sh` names the artifact from `pyproject.toml` and
knows nothing about the tag, so without the check, releasing `v0.2.0` while
`pyproject.toml` still said `0.1.0` would quietly attach `c2sync-0.1.0-*` to it.

The runner is pinned rather than `ubuntu-latest` on purpose: manylinux wheel selection
depends on the build host's glibc, so the runner image sets the glibc floor of every
published archive. Pinning keeps that a deliberate change instead of one GitHub makes
for you.

## Cutting a release

### 1. Bump the version on `main`

The one step the pipeline cannot do for you, and the reason the version guard exists.

```bash
git checkout main && git pull
git checkout -b release-0.2.0
# edit pyproject.toml: version = "0.2.0"
git commit -am "Bump version to 0.2.0"
gh pr create --base main
```

CI runs on the PR (tests on 3.11/3.12/3.13, shellcheck, build + install-test). The
archive is uploaded as a workflow artifact if you want to install it by hand before
merging. Merge once it is green.

### 2. Create the release from `main`

```bash
gh release create v0.2.0 --target main --title "v0.2.0" --generate-notes
```

`--target main` is what puts the new tag on `main`. The `v` prefix is optional — the
guard strips a leading `v`, so `v0.2.0` and `0.2.0` both match `version = "0.2.0"`.

A **draft** release triggers nothing; publishing it does, so notes can be staged first.
Prereleases do trigger, deliberately.

### 3. What runs

```
verify the tagged commit is an ancestor of main
verify the tag matches pyproject.toml's version
pip install -e ".[dev]"
build.sh --test-install      # tests, vendor wheels x3, install-test x3
sha256sum -> SHA256SUMS.txt
gh release upload --clobber
```

Expect several minutes; three dependency downloads and three container builds dominate.

### 4. Verify

```bash
gh release view v0.2.0
# assets: c2sync-0.2.0-linux-x86_64.tar.gz, SHA256SUMS.txt
```

### When a guard fails

The guards run *after* GitHub has published the release — a workflow cannot run before
the event that triggers it. A failed guard therefore leaves a real, visible release with
no archive attached.

Fixing `main` and re-running the job does **not** help: the guard reads
`pyproject.toml` from the *tagged commit*, so a tag pointing at the old commit keeps
failing no matter what lands on `main` afterwards. Start over instead:

```bash
gh release delete v0.2.0 --cleanup-tag --yes
# bump pyproject.toml on main properly, then create the release again
```

Re-running the workflow on a release that already has assets is safe — `gh release
upload --clobber` replaces them rather than failing.

If that after-the-fact failure window is unwelcome, the alternative is triggering on tag
push (`on: push: tags: ['v*']`) and having the workflow call `gh release create` itself
once the guards pass, so a bad tag never produces a visible release.

## Usage
### CLI Commands

`c2sync --help` lists the commands:

```
Usage:
c2sync COMMAND [ARGS]

Commands:
    init      Start a project for one device
    pull      Fetch the device's running config and make it the new baseline
    fetch     Check the device for drift without changing any local state
    status    Show unpushed local edits and unsaved device changes
    push      Preview the staged commands and push them to the device
    save      Save the device's running config to its startup config
    discard   Throw away local edits and return to the last confirmed push
    revert    Push the device's running config back to a past commit
    help      Show this message (also -h, --help)

Run `c2sync COMMAND --help` for detail on a single command.
```

Arguments and flags live in each command's own help rather than the summary above:
`c2sync COMMAND --help` (or `c2sync help COMMAND`) prints that command's usage,
arguments and flags. `-h`/`--help` are recognized anywhere on the line, so
`c2sync init --help` shows help rather than being read as a device path. Bare `c2sync`,
`c2sync help` and `c2sync --help` all print the command list. An unrecognized command
prints usage to stderr and exits 1.

Each command is covered in detail under General workflow below.

## General workflow

### 1. Init

```
c2sync init NAME SERIAL_DEVICE [BAUDRATE] [--dir PATH] [--pull]
c2sync init NAME --ssh HOST [PORT] [--dir PATH] [--pull]
```
Behavior:
* Creates a new project for one device, reached over serial or SSH. `NAME` is required — it's both the project's identity and, unless `--dir PATH` says otherwise, the directory it's created in (`./NAME`)
* `cd` into the project directory before running any other command — they all expect to be run from inside it, the same way `git status` expects to be run from inside the repository rather than handed a path to one
* Initializes a git repository there and makes the first commit (an empty `device.config`, plus `c2sync.toml` holding the connection info). Refuses to run if the target directory already holds a c2sync project, rather than overwriting it
* Serial: `BAUDRATE` defaults to 9600, or to the global config's `baudrate` if set (see Configuration below)
* SSH: `PORT` defaults to 22, or to the global config's `ssh_port` if set. Host keys are verified against your `~/.ssh/known_hosts`
* `--pull` immediately fetches the device's current running config as the baseline — the same as running `c2sync pull` right afterward — so onboarding an already-configured device is one step instead of two, mirroring `git clone` doing an initial fetch for you. Without it, `init` alone just creates an empty `device.config`

### 2. Pull

```
c2sync pull [--force|-f]
```
Behavior:
* Connects to the device, fetches the running config, and commits it as the new baseline — this is how you onboard a device that's already configured (`init` alone only creates an empty `device.config`, unless `--pull` was also given)
* Also useful later to resync the baseline if the device changed outside of C2Sync
* Refuses to run if you have unpushed local edits, unless `--force`/`-f` is passed to overwrite them — there's no `-y` here, since pull has no other prompt to skip; a flag that only means "overwrite my local edits" shouldn't be spelled the same as "don't ask me anything"

### 3. Fetch

```
c2sync fetch
```
Behavior:
* Connects to the device, reads the running config, and reports how it differs from the local baseline (`device.config` at git `HEAD`) — `device.config`, staging, and local state are all left untouched either way
* The read-only counterpart to `pull`: mirrors git's own `fetch` (look only) vs. `pull` (fetch and merge) split
* Closes a real gap in `status`, which is deliberately offline and so can report `synced` even when the device has actually drifted out-of-band. Run `c2sync fetch` any time you want to check without committing to either side of the drift

### 4. Local Editing

The user edits `device.config`, right at the top level of the project directory, with the text editor of their choice, using normal Cisco IOS CLI syntax. Just delete a line to remove it — C2Sync parses the config into a real tree (via `ciscoconfparse2`) and generates the correct `no <command>` for you; you don't need to type the negation yourself, though it still works fine if you do.

When `status`/`push` recompute staging, C2Sync diffs the parsed tree against the last confirmed baseline and writes the exact commands that would be sent to `.c2sync/staging.txt` — one of the files you never need to open yourself.

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
This preserves context and means the entire configuration doesn't need to be sent back to the device every time changes are pushed. After `push`, the device's config would look like this:
```
interface GigabitEthernet1/0/1
 switchport mode access
 switchport access vlan 100
 switchport nonegotiate
```

### 5. Status

```
c2sync status
```
Behavior:
* Shows whether the local file has unpushed edits (`host pending changes`), the device has pushed-but-unsaved changes (`device pending changes`), everything is `synced`, or a `merge conflict pending` from a previous push needs resolving by hand (see Push below)
* Previews the exact CLI commands that `push` would send
* Read-only — never connects to the device

### 6. Push

```
c2sync push [-y] [--rollback-on-error]

Options:
    -y                   Push without an interactive confirmation prompt
    --rollback-on-error  Offer to undo a partially-applied push
```
Behavior:
* Reads the device's running config first and checks it still matches the baseline the staged commands were computed against — see below (`c2sync fetch` runs this same check on demand, without pushing anything)
* Displays the commands that will be sent, then pushes them if confirmed
* Re-fetches the running config and commits it to the project's git repository — this becomes the new baseline for the next diff
* If the device rejects a command partway, the commands before it stay on the device. C2Sync always re-reads the running config at that point and moves the baseline to match, so `status` shows only what's still outstanding and your edits are left alone — it does not undo the push
* `--rollback-on-error` additionally offers to push the device back to the pre-push baseline. It's opt-in because undoing means sending more config to a device that just rejected some, and a negation isn't always a safe inverse (undoing an address or interface change can cut the session doing it). It previews and asks first unless `-y` is also given

**If the device changed outside C2Sync**, the staged commands describe a device that no longer exists. A line you deleted locally still becomes `no <that line>`, which on IOS clears whatever is actually there — so it can destroy a colleague's replacement for it, with nothing in the preview hinting at that. `push` therefore reads the device before showing you anything, and stops if it has drifted, printing what changed on it — then reconciles it with a real three-way text merge (the same kind git itself does for any text file): the last confirmed baseline as the common ancestor, your edits and the device's current config as the two sides.

* **The two sides touched different parts of the config** — the merge is clean, and push just proceeds automatically. There's nothing to ask about: both your edits and the device's change survive. This is recorded as a merge commit rather than a plain one.
* **The two sides changed the same line differently** — a real conflict. Push always stops for this, unconditionally; no flag resolves it, the same way git itself never silently picks a side on a real conflict. Standard conflict markers (`<<<<<<<` / `|||||||` / `=======` / `>>>>>>>`) are written into `device.config` for you to resolve by hand — delete the markers, keep what should actually be sent, and re-run `c2sync push`. Nothing is sent to the device while a conflict is unresolved (`status` reports `merge conflict pending`, and every command but `discard` refuses to run). `c2sync discard` doubles as the abort path — it drops your edits and reverts to the last confirmed baseline, clearing the markers with it.

### 7. Save

```
c2sync save [-y]
```
Behavior:
* Saves the device's running config to its startup config (`write memory`), so it survives a reload
* Refuses to run while there are unpushed local edits — run `push` first
* Records the save as a git commit (no file content changes, so it's an empty commit marking the milestone)

### 8. Discard

```
c2sync discard
```
Behavior:
* Reverts local edits back to the last confirmed push (`device.config` at git `HEAD`) and clears anything staged

### 9. Revert

```
c2sync revert [COMMIT] [-y] [--force|-f]
```
Behavior:
* Recovers a device that's ended up in a bad state — e.g. a `push` where one command in the middle of a batch got rejected after earlier ones already landed
* Fetches the running config from the device **right now** and diffs it against `COMMIT` (a past commit's `device.config`, `HEAD` if omitted) — not your local `device.config`, since after something's gone wrong that file isn't guaranteed to reflect what's actually running either
* Displays the commands needed to bring the device back to that commit's config, then pushes them if confirmed (or `-y`)
* Refuses to run if you have unpushed local edits, unless `--force`/`-f` is passed — reverting overwrites `device.config` with the post-revert device state, which would otherwise silently lose those edits. `-y` and `--force` are separate on purpose: `-y` only skips the push confirmation, `--force` is what's required to overwrite local edits — so skipping the prompt can never lose work by accident
* Records the recovery as a **new** git commit rather than moving `HEAD` backward, like `git revert` rather than `git reset --hard` — the incident stays visible in `git log` instead of being erased
* If the device already matches the target commit, it says so and doesn't push anything

## Credentials

`pull`/`push`/`save` need to log in to the device. In order of precedence:
1. `C2SYNC_USERNAME` / `C2SYNC_PASSWORD` / `C2SYNC_SECRET` environment variables
2. `username` from the global config file (see Configuration below) — password and enable-secret are never read from there
3. An interactive prompt for whatever's still missing

For CI/non-interactive use (e.g. a merged PR triggering `c2sync push -y`), set `C2SYNC_USERNAME` and `C2SYNC_PASSWORD` from your CI system's own secrets manager. C2Sync never stores credentials itself, in this file or anywhere else. If something is missing and there's no terminal to prompt on, the command exits 1 and names the variables to set rather than hanging or crashing.

## Troubleshooting

### "Could not read a usable running config from the device"

Almost always the device's own log messages interleaving with command output. IOS sends syslog straight to the session when `logging console` is on (the default on a console connection) or `terminal monitor` is set on a VTY, and those lines arrive in the middle of `show running-config`:

```
end

Switch#
*Sep 11 12:33:35.565
```

C2Sync strips what it can recognise and refuses the read rather than storing something that isn't a config — an earlier version stored it, which made every later `fetch`/`push` report drift that didn't exist. On the device:

```
conf t
 logging buffered 16384
 no logging console
end
write memory
```

Log messages are still available with `show logging`. C2Sync deliberately does not change this setting for you: `fetch` is read-only by contract, and a dropped session would otherwise leave the device with logging disabled.

### `configure terminal` is rejected / "Failed to enter configuration mode"

The account is landing in privilege level 1. Check with `show privilege` — even with `username <name> privilege 15`, an `aaa authorization exec` line can put you at level 1 on login. C2Sync handles this itself (it runs `enable` when the session isn't already in enable mode), so this usually only shows up in your own scripts; set an enable secret in `C2SYNC_SECRET` if the device has one.

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

### Multi-device support

The original goal for the project, and still a wanted direction — one C2Sync project
currently tracks exactly one device, which is the starting scope of the rewrite rather
than a decision against fleets.

Most of the groundwork is already shaped for it. `Project` (`c2sync/__init__.py`)
already holds every per-device path as a field rather than assuming a fixed layout, and
`git_ops` already addresses files by a path relative to the repo root
(`Project.edit_file_relpath`), so per-device subdirectories would not require rethinking
the git layer. What would need designing:

* **One repo for the fleet, or one repo per device.** A single repo (`devices/<name>/
  device.config`) gives you one `git log` across the fleet and lets a change spanning
  several devices land as one reviewable commit — closest to the original intent. Per-
  device repos keep `revert` and `discard` semantics exactly as they are today. The
  single-repo option looks like the better fit but makes `revert`'s "restore this device
  to commit X" need a per-device path filter rather than a whole-tree checkout.
* **Per-device state.** `state.json`'s `host_dirty`/`device_dirty` pair is per-project
  today and would become per-device, as would `staging.txt`.
* **Device selection and bulk operations.** Commands would need a device selector, and
  `status` across a fleet is genuinely useful. `push` across many devices is the hard
  part: partial failure (device 3 of 8 rejects a command) needs a defined outcome, and
  the existing single-device answer — abort the batch, recover with `c2sync revert` —
  does not obviously generalise to a fleet.
* **Credentials.** `C2SYNC_USERNAME`/`C2SYNC_PASSWORD` assume one device. A fleet needs
  either shared credentials or a per-device lookup, without c2sync starting to store
  secrets itself (see Credentials above).

### Multi-vendor support

C2Sync is Cisco IOS only today. Supporting another vendor means more than swapping Netmiko's `device_type` — the diff engine and the device's own workflow both matter:

* **NX-OS** — the more realistic near-term target. `ciscoconfparse2`'s diff engine (`hier_config`) already treats `nxos` as a first-class syntax rather than a fallback, and NX-OS keeps the same running-config/startup-config duality as IOS classic, so C2Sync's `push`-then-`save` model and state tracking would carry over largely unchanged. Would still need `device_type='cisco_nxos'`, plus NX-OS-specific error/save-confirmation patterns in `connector.py` — its "invalid command" and `copy run start` output wording differs from classic IOS.
* **JunOS** — a bigger lift. `ciscoconfparse2` parses JunOS config into a correct tree, but its diff/remediation engine currently falls back to IOS rules for `syntax='junos'` rather than real JunOS logic, and produces invalid syntax (`no set ...` instead of JunOS's `delete ...`). JunOS's candidate/commit model also has no separate running-vs-startup-config step the way IOS does, so the `push`/`save` split and `state_engine.py`'s dirty-state tracking would need real rework, not just a new device type.

### Pushing to a remote

Considered, and deliberately not built: auto-pushing the project's git repo to a remote after a confirmed `push`/`save`. Git already solves this better than C2Sync could — add a `post-commit` hook and every commit C2Sync makes gets mirrored automatically, with no new credential surface (it reuses whatever git push auth you already have set up) and no risk of a push failure ever affecting a device push that already succeeded:

```bash
cat > .git/hooks/post-commit <<'EOF'
#!/bin/sh
git push
EOF
chmod +x .git/hooks/post-commit
```

### VS Code extension

**Phase 1, already true today, no extension needed:** when a `push` merge conflict leaves `<<<<<<< / ||||||| / ======= / >>>>>>>` markers in `device.config` (see Push above), that's standard git conflict-marker syntax — the exact format `git merge-file` produces. VS Code's built-in editor recognizes that format in *any* open file and shows inline "Accept Current Change / Accept Incoming Change / Accept Both Changes / Compare Changes" actions, regardless of whether the file is part of an actual git operation. Opening `device.config` in VS Code during a pending conflict already gets this, today, for free.

**Phase 2, planned:** a small, genuinely optional VS Code extension wrapping the CLI — a status bar indicator for `host pending changes` / `device pending changes` / `merge conflict pending`, one-click `pull`/`fetch`/`push`/`discard`, and syntax highlighting for `device.config`. It stays thin on purpose: the extension talks to the same `c2sync` CLI a terminal user would, never the other way around, so the CLI keeps working exactly as it does now with no extension installed, and nothing about the project format (`c2sync.toml`, `device.config`, the git repo) becomes VS Code-specific.

**Stretch goal, not yet scoped:** VS Code also has a richer, visual 3-way Merge Editor beyond the inline markers above. Wiring into it properly would mean giving `push` a real git-level conflict (actual unmerged index stages + `MERGE_HEAD` in `PROJECT_DIR`, rather than today's `git_ops.merge_file()` working on loose temp files) so VS Code's own built-in git integration detects and opens it the same way it does for a normal `git merge` conflict — no private VS Code API involved. Deliberately not the alternative: VS Code's merge editor has no stable, documented way for an extension to open it directly on an arbitrary file (the closest such request, a `git mergetool`-style CLI flag, was closed by the VS Code team as not planned), only an internal, unsupported command that other extensions rely on at their own risk. That's a bigger design question than Phase 2 - how much closer that pulls C2Sync's git integration toward modeling the device as an actual git remote - so it's left for a dedicated pass later rather than decided here.

## Disclaimer
* This tool assumes familiarity with network device CLI. You must adhere to Cisco IOS' configuration syntax
* The tool does not validate commands before sending. You must review the preview yourself before confirming a `push`

## License
MIT
