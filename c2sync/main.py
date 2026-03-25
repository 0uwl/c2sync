import logging
import sys

LOGGER = logging.getLogger(__name__)

USAGE = """
Usage:
c2sync COMMAND

Commands:
    init         Start a C2Sync session in the current working directory
    sync         Preview changes and confirm or abort them
    commit       Issues the command to save the running config to the startup config on the device
    discard      Cancel the current C2Sync session
"""

def main():
    try:
        command = sys.argv[1]
    except IndexError:
        print(USAGE)

    command_arguments = sys.argv[2:]

    match command:
        case 'init':
            init(command_arguments)
        case 'sync':
            sync(command_arguments)
        case 'commit':
            commit(command_arguments)
        case 'discard':
            discard(command_arguments)
        case _:
            LOGGER.error(f'Unknown command {command}')
            print(USAGE)


def init(arguments: list):
    LOGGER.debug(f'Given arguments: {arguments}')
    print(f'Command arugments: {arguments}')
    raise NotImplementedError

def sync(arguments: list):
    LOGGER.debug(f'Given arguments: {arguments}')
    print(f'Command arugments: {arguments}')
    raise NotImplementedError

def commit(arguments: list):
    LOGGER.debug(f'Given arguments: {arguments}')
    print(f'Command arugments: {arguments}')
    raise NotImplementedError

def discard(arguments: list):
    LOGGER.debug(f'Given arguments: {arguments}')
    print(f'Command arugments: {arguments}')
    raise NotImplementedError