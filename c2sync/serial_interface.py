import serial
import re
import time


PROMPT_REGEX = re.compile(r"[>#]\s?$")


class SerialConnection:
    def __init__(self, port, baudrate=9600):
        self.port = port
        self.baudrate = baudrate
        self.conn = serial.Serial(self.port, self.baudrate, timeout=1)

    def send(self, cmd):
        self.conn.write((cmd + "\n").encode())

    def read_until_prompt(self):
        buffer = ""
        while True:
            data = self.conn.read(1024).decode(errors="ignore")
            buffer += data
            if PROMPT_REGEX.search(buffer):
                break
        return buffer

    def login(self):
        output = self.read_until_prompt()

        if "Username:" in output:
            self.send(input("Username: "))
        if "Password:" in output:
            self.send(input("Password: "))

        self.read_until_prompt()

    def send_command(self, cmd):
        self.send(cmd)
        return self.read_until_prompt()

    def enter_config(self):
        self.send_command("configure terminal")

    def exit_config(self):
        self.send_command("end")

    def send_config(self, commands):
        self.enter_config()
        for cmd in commands:
            self.send_command(cmd)
        self.exit_config()

    def get_running_config(self):
        self.send_command("terminal length 0")
        return self.send_command("show running-config brief")