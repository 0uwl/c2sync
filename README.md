## C2Sync – Console Configuration Synchronizer

## Overview

C2Sync is a Python-based CLI tool that acts as a **middleman between a network device (over console/serial) and a local Git repository**.

The tool allows the user to:

* Pull a device’s running configuration into a local Git repository over a serial connection
* Edit the configuration locally using the text editor of your choice (e.g. VS Code)
* Rebuild the Cisco IOS configuration file context structure through a simple alorithm 
* Push changes back to the device over serial
* Save the running configuration to the startup configuration

## Core Design Principles

* Focus on simplicity, reliability, and CLI correctness
* Meant to simplify the experience of working with local console configuration
* Easy to set up and use, high portability with everything stored in a per-project directory structure
* Users should be experienced with the Cisco IOS CLI syntax

## Key Features

C2Sync creates "projects" which store all the necessarry files and information about the devices used in the project. 
The project directory contains: 
* A registry file containing information about all devices included in this project, such as its name and the tty-device is used to communicate with the device
* The most recently fetched device configurations of the devices used in the project
* The staged changes built by C2Sync's context rebuilder

One project can be used for many devices. 

## Installation

```bash
pip install c2sync
```

## Usage
### CLI Commands
```
c2sync COMMAND

Commands:
  init      Create a subdirectory in the repository for this device
  pull      Pull running config from a device
  sync      Push staged changes to the running config of a device and fetch new configuration
  status    Show the current status of a C2Sync session
  diff      Show the current staged changes for a device
  commit    Commit changes from running config to startup config on a device
```

## General workflow

### 1. Init

Command:

```
c2sync init
```

Behavior:
* Create a new empty project in the current working directory (./.c2sync/)

### 2. Pull

Command:

```
c2sync pull DEVICE [TTY_DEVICE]
```
Behavior:
* Connect to device via serial. 
* TTY_DEVICE must be specified the first time a device is pulled from
* Handle AAA login (username/password)
* Disable paging (`terminal length 0`)
* Runs:
  ```
  show running-config brief
  ```
* Save output to:
  ```
  ./.c2sync/<DEVICE>.config
  ```
* Auto-commit to project repository:
  ```
  git commit -m "pulled from <DEVICE>"
  ```

### 3. Local Editing

The user can edit the pulled config file with the text editor of their choise. When editing the file, the user should still adhere to the rules of Cisco IOS CLI configuration. This means that to delete a line, simply removing it from the file will not work. Instead, the user should do as they would in the CLI and append a negation command `no...`. Deleted lines are ingored by the tool and when the configuration file is fetched from the device again, the lines will reapear.
When the user writes their changes to the file, C2Sync rebuilds the configuration context to properly store configuration changes in a staging file `./.c2sync/.<DEVICE>.staging`

Example:
```
interface GigabitEthernet1/0/1
 description Sevrer
 switchport mode access
 switchport access vlan 100
```
To remove the descrption and disable link negotiation with C2Sync, you edit this interface by editing the configuration file like this:
```
interface GigabitEthernet1/0/1
 no description Sevrer
 switchport mode access
 switchport access vlan 100
 switchport nonegotiate
```
C2Sync diffs the original with the changed lines and adds the added or changed lines to the staging file. However, Cisco IOS configurations have hierarchical context structures that must be included in addition to the changed lines. Therefore, C2sync rebuilds the context and adds it to the staging file by searching upwards in the lines of the configuration file until it reaches a line with less leading white spaces. This line is added to the staging file which then looks like this:
```
interface GigabitEthernet1/0/1
 no description Sevrer
 switchport nonegotiate
```
This makes sure context is preserved and removes the need to send the entire configuration back to the device every time changes are commited. 
The resulting configuration would look like this in the device after it has been pushed:
```
interface GigabitEthernet1/0/1
 switchport mode access
 switchport access vlan 100
 switchport nonegotiate
```

> [!NOTE]  
> _This means that you must be mindful of spaces to declare contexts properly_

### 4. Status

Command:
```
c2sync status [DEVICE]
```
Behavior:
* Shows the current state of the devices in this project. This includes which devices have staged changes or unwritten changes in the running config
* Optionally filter to a specific device

### 5. Diff

Command:
```
c2sync diff DEVICE
```
Behavior:
* Shows the staged changes of the selected device. These changes have been reconstructed using C2Sync's context rebuilder to reflect the exact commands to be sent to the device

### 6. Sync

Command:
```
c2sync sync [options] DEVICE

Options:
    -y    Push changes without confirmation
    -m    Optional git commit message
```
Behavior:
* Displays the commands that will be sent to the device then pushes the staged changes to the running config if confirmed
* Makes a git commit to the local repository
* Fetches the new configuration file after changes have been pushed

### 7. Apply
Command:
```
c2sync apply [options] DEVICE

Options:
    -y    Apply changes without confirmation
```
Behavior:
* Apply changes in the device's running config to its startup config


## Disclamer
* This tool assumes familiarity with network device CLI. You must adhere to Cisco IOS' configuration syntax
* The tool does not validate commands before sending. You must review the preview yourself before commiting it.

## License
MIT