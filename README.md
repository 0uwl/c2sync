# C2Sync (Console Config Sync)

C2Sync is a lightweight tool for editing network device configurations locally and syncing them over a console connection. Currently only Cisco IOS devices are supported

## Features

* Edit configs in your editor (VS Code, Vim, etc.)
* Sync changes over serial (console port)
* Context-aware command generation
* Safe preview before applying changes
* No network connectivity required

## How It Works

1. C2Sync fetches running config to a temporary file
2. You edit the file with the file editor of your choise
3. C2Sync detects changes when you save the file
4. Shows proposed CLI commands
5. Applies after confirmation

## Installation

```bash
pip install c2sync
```

## Usage
### Basic commands
```
c2sync init     Start a C2Sync session
c2sync commit   Preview changes and confirm or abort them
c2sync cancel   Cancel the current C2Sync session
```
### init
```
Usage:
c2sync init [options] TTY_DEVICE

Options:
    -f FILE         The path to the temporary file where config is stored and edited. Defaults to ./c2sync.config
    -u USERNAME     The username to log in to the console with
    -p PASSWORD     The password to log in to the console with
```
This command starts a C2Sync session. 

### commit
```
Usage:
c2sync commit [options]

Options:
```

### cancel
```
Usage:
c2sync cancel [options]

Options:
```

## Disclamer
* This tool assumes familiarity with network device CLI.
* The tool does not validate commands before sending. You must review the preview yourself before commiting it.

## License
MIT