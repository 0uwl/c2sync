# C2Sync (Console Config Sync)

C2Sync is a lightweight tool for editing network device configurations locally and syncing them over a console connection. Currently only Cisco IOS devices are supported

## How It Works

2. C2Sync fetches running config to a temporary file
3. You edit the file with the file editor of your choise
4. C2Sync detects changes when you save the file and builds a set of commands to send to the device
5. Shows proposed CLI commands
6. Applies after confirmation then fetches the new running config

## Features

* Edit configs in your editor (VS Code, Vim, etc.)
* Sync changes over serial (console port)
* Context-aware command generation
* Safe preview before applying changes
* No network connectivity required

### Context rebuilding
The script uses simple but effective logic for rebuilding Cisco IOS configuration contexts by checking white spaces before lines. 
This allows for simplified configuration file editing. For example, take this original configuration:
```
interface GigabitEthernet1/0/1
 description Sevrer
 switchport mode access
 switchport access vlan 100
 switchport nonegotiate
```
With C2Sync, you can edit this interface by editing the configuration file like this:
```
interface GigabitEthernet1/0/1
 no description Sevrer
 switchport mode access
 switchport access vlan 100
 switchport nonegotiate
```
C2Sync diffs the original with the changed lines and sees that `description Sevrer` has changed to `description Sevrer`. However, issuing only that command would not work since we need to be inside the interface context. Therefore, C2sync rebuilds the context and adds it to the staged changes by searching the lines of the configuration file upwards until it reaches a line with less leading white spaces. This line is added to the staged changes which then looks like this:
```
interface GigabitEthernet1/0/1
 no description Sevrer
```
This makes sure context is preserved and removes the need to send the entire configuration back to the device every time changes are commited. 

> [!NOTE]  
> _This means that you must be mindful of spaces to declare contexts properly_

## Installation

```bash
pip install c2sync
```

## Usage
### Basic commands
```
c2sync init         Start a C2Sync session in the current working directory
c2sync sync         Preview changes and confirm or abort them
c2sync commit       Issues the command to save the running config to the startup config on the device
c2sync discard      Cancel the current C2Sync session
```

### init
```
Usage:
c2sync init [options] TTY_DEVICE

* Initializes a C2Sync session over TTY_DEVICE. This creates .c2sync/ inside the current working directory 
* USERNAME and PASSWORD are used to authenticate to the local device user over TTY_DEVICE. They are prompted for if not given

Options:
    -u USERNAME     The username used for local device user authentication
    -p PASSWORD     The password used for local device user authentication 
```

### sync
```
Usage:
c2sync sync [options]

* Displays the current staged changes and asks for confirmation before sending them to the device.

Options:
    -y      Confirm changes without user input
```

### commit (NOT IMPLEMENTED)
```
Usage:
c2sync commit [options]

* Displays all changes made since the last commit and asks for confirmation before issuing the command to save the running config to the startup config on the device

Options:
    -y      Confirm without user input
```

### discard
```
Usage:
c2sync discard

* Displays the current staged changes and asks for confirmation before discarding changes.
```

## Disclamer
* This tool assumes familiarity with network device CLI. You must adhere to Cisco IOS' configuration syntax
* The tool does not validate commands before sending. You must review the preview yourself before commiting it.

## License
MIT