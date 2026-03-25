from c2sync import Project
import logging
import difflib
from typing import List

from c2sync.models import Addition, Command, CommandBlock

LOGGER = logging.getLogger(__name__)


class Differ:
    """
    Responsible for computing configuration differences and transforming them
    into executable CLI command blocks.
    """

    def __init__(self, project: Project) -> None:
        self.staging_file = project.STAGING_FILE


    def save_to_staging(self, old_lines: List[str], new_lines: List[str]) -> None:
        """
        Generate CLI command blocks from config differences and append them
        to the staging file.
        """
        blocks = self._build_command_blocks(old_lines, new_lines)

        with open(self.staging_file, 'a') as file:
            for block in blocks:
                # Convert structured block into CLI lines
                file.write("\n".join(block.to_lines()) + "\n")


    def clear_staging(self) -> None:
        """
        Clear the staging file.
        """
        # Open in write mode truncates the file to zero length
        open(self.staging_file, 'w').close()


    @staticmethod
    def _get_indent_level(line: str) -> int:
        """
        Return the number of leading spaces in a line.
        """
        return len(line) - len(line.lstrip(' '))


    def _build_command_blocks(
        self,
        old_lines: List[str],
        new_lines: List[str]
    ) -> List[CommandBlock]:
        """
        Main pipeline:

        1. Compute diff
        2. Extract added lines with positions
        3. Build context-aware command tuples
        4. Group commands by shared context
        """

        additions = self._extract_additions(old_lines, new_lines)
        
        # Transform raw additions into structured command tuples
        commands = self._build_commands(additions, new_lines)
        
        # Merge commands that share the same context
        return self._group_commands_by_context(commands)


    def _extract_additions(
        self,
        old_lines: List[str],
        new_lines: List[str]
    ) -> List[Addition]:
        """
        Return a list of Additions for lines added in new config.

        Uses ndiff but tracks positions safely by walking new_lines.
        """
        diff = list(difflib.ndiff(old_lines, new_lines))

        additions: List[Addition] = []
        new_index = -1  # tracks position in new_lines

        for line in diff:
            tag = line[:2]      # e.g. "+ ", "- ", "  "
            content = line[2:]  # actual config line

            # IMPORTANT:
            # We only advance new_index when the line exists in new_lines.
            #
            # ndiff output includes:
            #   "  " -> exists in both old and new
            #   "+ " -> exists only in new
            #   "- " -> exists only in old
            #
            # So:
            #   - "  " and "+ " advance position in new_lines
            #   - "- " does NOT (it doesn't exist in new_lines)
            if tag in ("  ", "+ "):
                new_index += 1

            # We only care about added lines that are not empty/whitespace
            if tag == "+ " and content.strip():
                # Store BOTH index and content to avoid fragile lookups later
                additions.append(Addition(index=new_index, line=content))

        return additions


    def _build_commands(
        self,
        additions: List[Addition],
        new_lines: List[str]
    ) -> List[Command]:
        """
        Convert added lines into context-aware Commands.
        """

        commands = []

        for addition in additions:
            stripped = addition.line.strip()

            # Rebuild CLI context using indentation hierarchy
            context = self._build_context_block(
                new_lines,
                addition.index
            )

            commands.append(Command(
                context=context,
                action=stripped
            ))

        return commands


    def _build_context_block(self, full_context: list[str], start_index: int) -> list[str]:
        """
        Reconstruct the CLI context for a given line by walking upwards
        in the configuration and collecting parent lines.

        The logic is based on indentation hierarchy.
        """
        context_block = []
        current_indent = self._get_indent_level(full_context[start_index])

        for i in range(start_index - 1, -1, -1):
            line = full_context[i]
            line_indent = self._get_indent_level(line)

            if line_indent < current_indent:
                context_block.insert(0, line.strip())
                current_indent = line_indent

            if current_indent == 0:
                break

        return context_block


    def _group_commands_by_context(
        self,
        commands: List[Command]
    ) -> List[CommandBlock]:

        context_to_commands = {}
        for command in commands:
            # Lists are not hashable -> convert to tuple for dict key
            key = tuple(command.context)

            # If key doesn't exist, create an empty list and append the commands action to it
            context_to_commands.setdefault(key, []).append(command.action)

        grouped_blocks: List[CommandBlock] = []
        for context_tuple, actions in context_to_commands.items():
            grouped_blocks.append(CommandBlock(
                context=list(context_tuple),
                actions=actions
            ))

        return grouped_blocks