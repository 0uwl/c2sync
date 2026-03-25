from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class Addition:
    """
    Represents a line added in the new configuration.
    """
    index: int   # Position in new_lines
    line: str    # Raw line content


@dataclass(frozen=True)
class Command:
    """
    Represents a CLI command with its context.
    """
    context: List[str]  # Parent CLI context (e.g., ["interface Gi1/0/1"])
    action: str         # Actual command (e.g., "description test")


@dataclass
class CommandBlock:
    """
    Represents a group of commands sharing the same context.
    """
    context: List[str]
    actions: List[str]

    def to_lines(self) -> List[str]:
        """
        Convert block into CLI-ready list of lines.
        """
        return self.context + self.actions if self.context else self.actions