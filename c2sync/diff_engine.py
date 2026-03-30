import difflib

from c2sync.logger import get_logger
from c2sync.models import ConfigLine, CommandBlock


def _get_indentation(line: str) -> int:
    return len(line) - len(line.lstrip())


def _build_config_model(file_lines: list[str]) -> list[ConfigLine]:
    """
    Convert raw file lines into structured ConfigLine objects.
    
    Args:
        file_lines (list[str]): A list of all lines in the full config file 

    Returns:
        list[ConfigLine]: A list of structured ConfigLines 
    """
    return [
        ConfigLine(
            index=i,
            text=line.rstrip(),
            indent=_get_indentation(line)
        )
        for i, line in enumerate(file_lines)
    ]


def _build_changed_lines_model(
    config_model: list[ConfigLine],
    changed_lines: list[str]
) -> list[ConfigLine]:
    """
    Match changed lines to their positions in the full config.
    
    Args:
        changed_lines (list[str]): A list of all changed lines in the new config file

    Returns:
        list[ConfigLine]: A list of the changed lines 
    """
    lookup = {line.text: line for line in config_model}

    result: list[ConfigLine] = []

    for line in changed_lines:
        if line.strip() in lookup:
            result.append(lookup[line.strip()])

    return result


def _build_command_blocks(
    config_model: list[ConfigLine],
    targets: list[ConfigLine]
) -> list[CommandBlock]:
    """
    Build command blocks by reconstructing CLI context.
    
    Args:
        targets (list[ConfigLine]): The list of commands to build the blocks from

    Returns:
        list[CommandBlock]: A list of structured CommandBlocks
    """
    blocks: list[CommandBlock] = []

    for target in targets:
        context = _build_context_tree(config_model, target)
        blocks.append(
            CommandBlock(
                context=tuple(context),
                command=target.text.strip()
            )
        )

    return blocks


def _build_context_tree(
    config_model: list[ConfigLine],
    target: ConfigLine
) -> list[str]:
    """
    Walk upward in config to reconstruct CLI hierarchy. Algorithm starts on target's index and walks up until it reaches a line with 0 leading white space.
    
    Args:
        target (ConfigLine): The starting line that the algorithm should start at

    Returns:
        list[str]: A list starting which starts with the root context and ends with the context closest to the target
    """

    # The context tree for the target line. Example:
    
    context_tree: list[str] = []
    current_indent = target.indent

    for i in range(target.index - 1, -1, -1):
        line = config_model[i]

        if line.indent < current_indent:
            context_tree.insert(0, line.text.strip())
            current_indent = line.indent

        if current_indent == 0:
            break

    return context_tree


def _group_blocks_by_context(blocks: list[CommandBlock]) -> list[str]:
    """
    Group commands by shared context and flatten into CLI order. Returns a list of commands sorted into their context

    Args:
        blocks (list[CommandBlock]): The unsorted command blocks

    Returns:
        list[str]: A list of commands that are sorted into their context
    """


    grouped: dict[tuple[str, ...], list[str]] = {}

    for block in blocks:
        grouped.setdefault(block.context, []).append(block.command)

    result: list[str] = []

    for context, commands in grouped.items():
        result.extend(context)
        result.extend(commands)

    return result


def _parse_diff(config_model: list[ConfigLine], old_lines: list[str], new_lines: list[str]) -> list[ConfigLine]:
        """
        Compute line-level differences using difflib and return added/changed lines.

        This replaces Git-based diff parsing with a more controlled and predictable
        diffing mechanism using Python's standard library.

        Behavior:
            - Returns lines that are added or modified
            - Ignores deleted lines
            - Preserves order of appearance in the new file

        Args:
            old_lines (list[str]):
                Previous version of the config (e.g., last committed)

            new_lines (list[str]):
                Current version of the config (edited by user)

        Returns:
            list[str]:
                Lines that should be processed into CLI commands
        """

        diff = difflib.ndiff(old_lines, new_lines)

        changed_lines: list[str] = []

        for line in diff:
            # Lines starting with "+ " are additions or modifications
            if line.startswith("+ "):
                changed_lines.append(line[2:])  # strip "+ "

        return _build_changed_lines_model(config_model, changed_lines)

# ---------------------------
# Pipeline Entry Point
# ---------------------------

def build_staging(
    old_file_lines: list[str],
    new_file_lines: list[str],
) -> str:
    """
    Turns raw file content into CLI commands.
    """
    # Build a structured model out of new file lines
    config_model = _build_config_model(new_file_lines)

    # Get the changed lines
    changed_lines = _parse_diff(config_model, old_file_lines, new_file_lines)

    # Build the blocks of commands with their context tree
    blocks = _build_command_blocks(config_model, changed_lines)

    # Group command blocks by their shared context
    grouped_blocks = _group_blocks_by_context(blocks)

    # Join lines into a full text
    staging = '\n'.join(grouped_blocks)

    return staging
