import time
import re
import logging

from serial import Serial

from c2sync import Project

LOGGER = logging.getLogger(__name__)

class SerialInterface:
    def __init__(self, project: Project) -> None:
        self.serial = Serial(
            port=project.SERIAL_DEVICE,
            baudrate=project.BAUDRATE,
            timeout=project.TIMEOUT
        )
        self.timeout = project.TIMEOUT
        self.prompt_regex = project.PROMPT_REGEX


    def send_command(self, command: str):
        if self.serial.writable():
            LOGGER.debug(f'Sending command {command} to device')
            message = command + '\n'
            self.serial.write(message.encode())
            response = self.read_until_prompt()
            LOGGER.debug(f'Recieved response: {response}')
            return response


    def read_until_prompt(self, timeout: int=-1):
        timeout = self.timeout if timeout is -1 else timeout
        buffer = ''
        start_time = time.time()

        while True:
            if self.serial.in_waiting:
                chunk = self.serial.read(self.serial.in_waiting).decode(errors="ignore")
                buffer += chunk

                lines = buffer.strip().splitlines()
                if lines and re.search(self.prompt_regex, lines[-1]):
                    return buffer
                    
            if time.time() - start_time > timeout:
                return buffer

            time.sleep(0.1)


    def login(self, username: str, password: str, timeout: int):
        timeout = self.timeout if timeout is None else timeout
        buffer = ''
        start_time = time.time()

        while True:
            if self.serial.in_waiting:
                chunk = self.serial.read(self.serial.in_waiting).decode(errors="ignore")
                buffer += chunk

                print(chunk, end='')

                if 'Username:' in buffer:
                    message = username + '\n'
                    self.serial.write(message.encode())
                    buffer = ''
                elif 'Password:' in buffer:
                    message = password + '\n'
                    self.serial.write(message.encode())
                    buffer = ''
                else:
                    lines = buffer.strip().splitlines()
                    if lines and re.search(self.prompt_regex, lines[-1]):
                        LOGGER.info('Successfully logged in to device')
                        print('\nLogged in')
                        return True

            if time.time() - start_time > timeout:
                raise Exception('Login timedout')

            time.sleep(0.1)


    def initialize_session(self):
        self.send_command('enable')
        self.send_command('terminal length 0')


    def get_running_config(self):
        self.send_command('show running-config brief')
        time.sleep(0.5)
        return self.read_until_prompt()


    def apply_config_blocks(self, blocks: list[list[str]]):
        print('\nEntering configuration mode...')
        self.send_command('configure terminal')

        for block in blocks:
            for line in block:
                print(f'SENDING: {line}')
                self.send_command(line)

        self.send_command('end')


    def sync_config(self, blocks: list[list[str]]):
        self.apply_config_blocks(blocks)
        
        new_config = self.get_running_config()

        # TODO: Update edit file
