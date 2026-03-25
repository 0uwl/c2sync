import logging
import difflib

from c2sync import Project

LOGGER = logging.getLogger(__name__)

class Differ:
    def __init__(self, project: Project) -> None:
        pass


    def get_indent_level(self, line: str) -> int:
        return len(line) - len(line.lstrip(' '))


    def get_added_lines(self, old_lines: list[str], new_lines: list[str]):
        diff = difflib.ndiff(old_lines, new_lines)
        return [line[2:] for line in diff if line.startswith('+ ')]


    def build_context_block(self, lines: list[str], index: int):
        context_block = []
        current_indent = self.get_indent_level(lines[index])

        for i in range(index - 1, -1, -1):
            line = lines[i]
            line_indent = self.get_indent_level(line)

            # If the current line has fewer indents than the current indent level...
            if line_indent < current_indent:
                # Add the line to the context_block
                context_block.insert(0, line.strip())
                # Set the current indent level to this line's indent level
                current_indent = line_indent

            # If the current line has no indents, this line is at the root context...
            if current_indent == 0:
                # Stop building context block
                break

        return context_block
    

    def get_changes(self, old_lines: list[str], new_lines: list[str]) -> list[str]:
        """
            Get a list of commands to be sent to the device.
        """
        added = self.get_added_lines(old_lines, new_lines)
        commands = []

        for line in added:
            if not line.strip():
                continue

            try:
                index = new_lines.index(line)
            except ValueError:
                continue

            context_block = self.build_context_block(new_lines, index)

            if context_block:
                commands.append(tuple(context_block + [line.strip()]))
            else:
                commands.append(tuple(line.strip(),))

        # Group commands by context
        return group_commands(commands)
        

def group_commands(commands: list[tuple]) -> list[str]:
    grouped = {}
    for command in commands:
        key = tuple(command[:-1])
        grouped.setdefault(key, []).append(command[-1])

    blocks = []
    for context, subcommands in grouped.items():
        if context:
            blocks.append(list(context) + subcommands)
        else:
            blocks.append(subcommands)

    return blocks