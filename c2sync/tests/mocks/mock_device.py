from typing import List


class MockCiscoDevice:
    """
    Very lightweight simulation of a Cisco IOS CLI.

    Supports:
    - Context navigation (interface, router, etc.)
    - Applying commands
    - Maintaining running config
    """

    def __init__(self, initial_config: List[str]):
        # Store config as list of lines
        self.running_config = initial_config.copy()

        # Track current CLI context
        self.current_context = []

    # ------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------

    def send_config_set(self, commands: List[str]):
        """
        Simulate sending a list of CLI commands to the device.
        """
        for cmd in commands:
            self._process_command(cmd.strip())

    def get_running_config(self) -> List[str]:
        """
        Return current running config.
        """
        return self.running_config

    # ------------------------------------------------------------
    # Internal logic
    # ------------------------------------------------------------

    def _process_command(self, cmd: str):
        """
        Interpret a CLI command.
        """

        # Enter context (e.g. "interface Gi1/0/1")
        if self._is_context_command(cmd):
            self.current_context = [cmd]
            return

        # Exit context
        if cmd == "exit":
            self.current_context = []
            return

        # Apply config command
        self._apply_command(cmd)

    def _is_context_command(self, cmd: str) -> bool:
        """
        Very simplified context detection.
        """
        return (
            cmd.startswith("interface ")
            or cmd.startswith("router ")
            or cmd.startswith("line ")
        )

    def _apply_command(self, cmd: str):
        """
        Apply a command inside current context.
        """

        if self.current_context:
            context_line = self.current_context[0]

            # Find context in config or create it
            try:
                index = self.running_config.index(context_line)
            except ValueError:
                # Create new context
                self.running_config.append(context_line)
                index = len(self.running_config) - 1

            # Insert command under context (indented)
            self.running_config.insert(index + 1, f" {cmd}")

        else:
            # Global command
            self.running_config.append(cmd)