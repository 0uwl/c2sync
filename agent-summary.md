# C2Sync - Console Configuration Synchronizer

## Functions

### The project directory

C2Sync projects work similarly to how Git repositories work. When the user runs `c2sync init`, a hidden folder (`./.c2sync/`) is created inside the current working directory as well as a Git repository.

#### The registry
Every project has a registry file which stores and tracks information about the project and the pulled devices. This is stored in a JSON-file inside the project folder

#### Project directory structure
```
<cwd>/
├── .c2sync/
|   ├── register.json
|   └── <device staging files>
|
├── .git/
|   └── <normal git repository stuff>
|
└── <device configuration files>

```
### Diff -> CLI Conversion Logic

* Parse Git diff output
* Identify added lines (`+`)
* Ignore removed lines (`-`)
* Reconstruct CLI context using indentation

#### Example:

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

#### Context Reconstruction Algorithm

* Determine indentation level of changed line
* Walk upward in file:
  * Find first line with lower indentation
  * Repeat until top-level (indent = 0)
* Build command stack

#### Command Grouping

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

### Serial Communication

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

### Watcher

The file watcher looks for changes in the fetched configuration files, runs the context rebuilder and adds the result into a staging file. 

### State tracker

C2sync should be able to track which state the pulled devices are in. The states that a device can be in are:
* **Synced**: This state is reached when all configuration files are matched. Including the file on the host, the running config and also the startup config on the device. The device is in rest
* **Host pending changes**: This state is reached after the user edits the fetched config file and the staging file is not empty. Now the host config no longer matches the running config, but the running config still matches with the startup config on the device.
* **Device pending changes**: This state is reached after the user pushes their staged changes to the device, but the changes are not applied to the startup config of the device. Now the host config matches with the running config, but the running config does not match with the startup config

States are tracked in the device class

## Constraints / Simplifications

* No support for multi-line configs (e.g. banners)
* No automatic handling of deletions
* Assumes Cisco IOS CLI-style indentation
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

## Goal

Build a simple, reliable tool that:
* Feels natural to network engineers
* Uses Git instead of reinventing version control
* Safely translates file edits into CLI commands
* Works entirely over console (no network dependency)

## Example Workflow
```
c2sync init
c2sync pull switch1 /dev/ttyUSB0

# edit config in VS Code

c2sync diff switch1
c2sync sync switch1
c2sync apply switch1
```

## Required Libraries
* pyserial
* gitpython
* pytest