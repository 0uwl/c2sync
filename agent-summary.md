# C2Sync – Git-Backed Console Config Sync Tool

## Overview

C2Sync is a Python-based CLI tool that acts as a **middleman between a network device (over console/serial) and a local Git repository**.

The tool allows a single user on a single machine to:

* Pull a device’s running configuration into a local Git repo
* Edit the configuration locally using any editor (e.g. VS Code)
* Use Git for diffing, staging, and committing
* Push only the changes back to the device over a serial console connection

The tool does NOT attempt to replace Git functionality. Instead, it leverages Git as the source of truth and diff engine.

## Core Design Principles

* Git is the **single source of truth**
* The tool is **stateless** (no custom staging system)
* Only **one user per repo/machine**. C2Sync is installed locally per machine per user. 
* No collaboration features (no PRs, no remotes required)
* Focus on **simplicity, reliability, and CLI correctness**
* Users are **experienced network engineers**

## Key Features

C2Sync uses a single git repository to store device configuration into separate subdirectories, called "projects".
The project directory contains: 
* The most recently fetched device configuration
* Tool configurations for this specific project, such as the TTY device used to communicate with the device
* The staged changes built by C2Sync's context rebuilder

### 1. Init

Command:

```
c2sync init [options] <device> TTY_DEVICE
```

Behavior:
* Create a new subdirectory in the Git repository
* 

### 2. Pull (device -> Git repo)

Command:

```
c2sync pull <device>
```

Behavior:
* Connect to device via serial
* Handle AAA login (username/password)
* Disable paging (`terminal length 0`)
* Run:
  ```
  show running-config brief
  ```
* Save output to:
  ```
  ~/.local/share/c2sync/<device>/running-config.txt
  ```
* Auto-commit:
  ```
  git commit -m "pulled from <device>"
  ```

### 3. Local Editing

The user can edit the pulled config file with the text editor of their choise. 
When the user writes their changes to the file, C2Sync rebuilds the configuration context to properly store configuration changes in a staging file.

Example:
```
interface GigabitEthernet1/0/1
 description Sevrer
 switchport mode access
 switchport access vlan 100
 switchport nonegotiate
```
To remove the descrption With C2Sync, you edit this interface by editing the configuration file like this:
```
interface GigabitEthernet1/0/1
 no description Sevrer
 switchport mode access
 switchport access vlan 100
 switchport nonegotiate
```
C2Sync diffs the original with the changed lines and sees that `description Sevrer` has changed to `no description Sevrer`. Issuing only that command would not work since we need to be inside the interface context to adhere to Cisco IOS syntax. Therefore, C2sync rebuilds the context and adds it to the staged changes by searching the lines of the configuration file upwards until it reaches a line with less leading white spaces. This line is added to the staged changes which then looks like this:
```
interface GigabitEthernet1/0/1
 no description Sevrer
```
This makes sure context is preserved and removes the need to send the entire configuration back to the device every time changes are commited. 

### 4. Push (Git repo -> device)

Command:

```
c2sync push <device> TTY_DEVICE
```

Behavior:

* Use GitPython to compute diff between:
  * Last committed version (HEAD)
  * Working tree or staged changes
* Extract **added lines only**
* Ignore deletions entirely
  * Users must explicitly add `no ...` commands

## Diff -> CLI Conversion Logic

This is the core feature of the tool.

### Requirements:

* Parse Git diff output
* Identify added lines (`+`)
* Ignore removed lines (`-`)
* Reconstruct CLI context using indentation

### Example:

Original config:
```
interface GigabitEthernet1/0/1
 description Sevrer
 switchport mode access
 switchport access vlan 100
```

Input diff:
```
+ no description Server
+ switchport nonegotiate
```

Generated commands sent to the device when pushed:
```
interface GigabitEthernet1/0/1
 no description Server
 switchport nonegotiate
```

Resulting config:
```
interface GigabitEthernet1/0/1
 switchport mode access
 switchport access vlan 100
 switchport nonegotiate
```

### Context Reconstruction Algorithm

* Determine indentation level of changed line
* Walk upward in file:
  * Find first line with lower indentation
  * Repeat until top-level (indent = 0)
* Build command stack

### Command Grouping

Group commands by shared context:

Instead of:

```
interface Gi1/0/1
 description A

interface Gi1/0/1
 no shutdown
```

Send:

```
interface Gi1/0/1
 description A
 no shutdown
```

## Serial Communication

Use pySerial.

Required features:

* Open serial port
* Prompt detection using regex (`[>#]\s?$`)
* AAA login handling:
  * Detect `Username:` / `Password:`
* Enable mode:
  ```
  enable
  ```
* Config mode:
  ```
  configure terminal
  ```
* Send commands line-by-line
* Exit config mode (`end`)

## Constraints / Simplifications

* No support for multi-line configs (e.g. banners)
* No automatic handling of deletions
* Assumes Cisco-style indentation
* No rollback support
* No concurrency concerns

## Project Structure
```
c2sync/
├── c2sync/
│   ├── tests/
|   │   ├── conftest.py
|   │   ├── constants.py
|   |   └── test_[...].py
|   |
│   ├── __init__.py
│   ├── diff_engine.py
│   ├── git_ops.py
│   ├── main.py
│   ├── models.py
│   ├── serial_interface.py
│   └── watcher.py
│
├── pyproject.toml
├── README.md
└── requirements.txt
```

## Required Libraries

* pyserial
* gitpython
* pytest

## CLI Commands
```
c2sync COMMAND

Commands:
  init      Create a subdirectory in the repository for this device
  pull      Pull running config from a device
  push      Push staged changes to the running config of a device
  commit    Commit changes from running config to startup config on a device
  status    Show the current status of a C2Sync session
  diff      Show the current staged changes for a device
```

## Example Workflow

```
c2sync init switch1
c2sync pull switch1

# edit config in VS Code

git diff
git commit -am "update interface config"

c2sync push switch1
```

## Goal

Build a simple, reliable tool that:
* Feels natural to network engineers
* Uses Git instead of reinventing version control
* Safely translates file edits into CLI commands
* Works entirely over console (no network dependency)