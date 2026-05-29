# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

```bash
# Install in editable mode (required before running c2sync)
pip install -e .

# Run all tests
pytest c2sync/tests/

# Run a single test file
pytest c2sync/tests/test_differ.py

# Run a single test
pytest c2sync/tests/test_differ.py::test_name

# Run the CLI
c2sync --help
```

## Architecture

C2Sync is a CLI tool that synchronizes Cisco IOS device configs over serial. Users edit pulled config files in their editor, and c2sync translates those edits into CLI commands sent over pySerial.

### Data Flow

```
c2sync pull   → SerialConnection.get_running_config() → device.save_config() → git_ops.commit_all()
c2sync diff   → staging_builder.write_device() → diff_engine.build_staging() → staging file
c2sync sync   → reads staging file → SerialConnection.send_config() → re-pull → git commit
c2sync commit → SerialConnection.send_command("write memory")
```

### Module Responsibilities

- **`main.py`** — Click CLI entry points. Each command orchestrates calls to `project_manager`, `git_ops`, `serial_interface`, and `staging_builder`. Logger context (`set_log_context`) must be set before any logging calls.
- **`project_manager.py`** — Registry CRUD (`registry.json`). `get_device(name, tty)` upserts: creates a new Device if the name is absent from registry, otherwise returns the existing one. The TTY is only required on first pull.
- **`diff_engine.py`** — Pure pipeline: `build_staging(old_lines, new_lines) -> str`. Computes added lines via `difflib.ndiff`, walks up the config tree to reconstruct CLI context hierarchy, and groups commands sharing the same context.
- **`staging_builder.py`** — Bridges `git_ops` and `diff_engine`. Reads HEAD version and working-tree version of a config file, calls `build_staging`, writes to `device.staging_path`.
- **`git_ops.py`** — Thin wrapper around GitPython. All paths are relative to `REPO_PATH = Path(".")` (the user's working directory, not the package). `get_head_file` reads from the last commit; `get_working_file` reads from disk.
- **`serial_interface.py`** — `SerialConnection` wraps pySerial. Prompt detection uses `PROMPT_REGEX = re.compile(r"[>#]\s?$")`. `send_config` enters config mode, sends commands one-by-one, then exits. `is_config_synced` diffs running vs startup config on-device.
- **`models.py`** — `Device` dataclass (name, tty, config_path, staging_path). `ConfigLine` and `CommandBlock` are the intermediate types used by `diff_engine`. `DeviceState` holds state string constants.
- **`logger.py`** — Context-var-based logger. `setup_logging()` must run first (called by the Click group). `set_log_context(name)` sets the device prefix. `get_logger()` retrieves it — raises `RuntimeError` if called before setup.

### Project Directory (created by `c2sync init`)

```
<user cwd>/
├── .c2sync/
│   ├── registry.json      # device registry: {name: {name, tty, config_path, staging_path}}
│   ├── session.log        # rotating log
│   ├── <device>.config    # pulled running config (also tracked by git)
│   └── .<device>.staging  # generated CLI commands (hidden, not committed)
└── .git/
```

### Device States

Tracked in `Device.get_state()`:
- **SYNCED** — staging file is empty and running-config matches startup-config
- **HOST_PENDING** — staging file is non-empty (user has edited the config, not yet synced)
- **DEVICE_PENDING** — staging is empty but running-config differs from startup-config (synced, not yet written to flash)

### Diff Engine Pipeline

`diff_engine.build_staging` runs these steps in order:
1. `_build_config_model` — wraps new file lines in `ConfigLine(index, text, indent)`
2. `_parse_diff` — runs `difflib.ndiff(old, new)`, keeps only `+ ` lines, maps them back to `ConfigLine` entries
3. `_build_command_blocks` — for each changed line, calls `_build_context_tree` to walk upward through the config model collecting parent lines (lower indent), producing `CommandBlock(context, command)`
4. `_group_blocks_by_context` — groups commands sharing the same context tuple, emits context lines once then all commands under them

## Constraints

- No multi-line config support (e.g. banners)
- Deletions are not automatically handled — only added lines are processed
- Assumes Cisco IOS CLI-style single-space indentation
- No rollback, no concurrency handling
- `serial_interface.login()` call is currently commented out in `__init__`
