import difflib
from dataclasses import dataclass

from ciscoconfparse2 import CiscoConfParse

from c2sync.logger import get_logger


@dataclass(frozen=True)
class CommandBlock:
    context: tuple[str, ...]
    command: str


def _parse_diff(old_lines: list[str], new_lines: list[str]) -> tuple[list[int], list[int]]:
    """Walk ndiff output, returning 0-based indices of added lines in new_lines
    and deleted lines in old_lines."""
    added: list[int] = []
    deleted: list[int] = []
    new_i = old_i = 0

    for token in difflib.ndiff(old_lines, new_lines):
        tag = token[:2]
        if tag == '  ':
            new_i += 1
            old_i += 1
        elif tag == '+ ':
            added.append(new_i)
            new_i += 1
        elif tag == '- ':
            deleted.append(old_i)
            old_i += 1
        # '? ' hint lines don't correspond to any real line

    return added, deleted


def _build_command_blocks(
    added_indices: list[int],
    deleted_indices: list[int],
    new_parse: CiscoConfParse,
    old_parse: CiscoConfParse,
) -> list[CommandBlock]:
    new_objs = new_parse.objs
    old_objs = old_parse.objs
    deleted_idx_set = set(deleted_indices)

    blocks: list[CommandBlock] = []

    for idx in added_indices:
        if idx >= len(new_objs):
            continue
        obj = new_objs[idx]
        text = obj.text.strip()
        if not text or text.startswith('!'):
            continue
        context = tuple(p.text.strip() for p in obj.all_parents)
        blocks.append(CommandBlock(context=context, command=text))

    for idx in deleted_indices:
        if idx >= len(old_objs):
            continue
        obj = old_objs[idx]
        text = obj.text.strip()
        if not text or text.startswith('!'):
            continue
        # Skip if any ancestor was also deleted — the ancestor's 'no' covers the whole block
        if {p.linenum for p in obj.all_parents} & deleted_idx_set:
            continue
        context = tuple(p.text.strip() for p in obj.all_parents)
        blocks.append(CommandBlock(context=context, command=f"no {text}"))

    return blocks


def _group_blocks_by_context(blocks: list[CommandBlock]) -> list[str]:
    grouped: dict[tuple[str, ...], list[str]] = {}
    for block in blocks:
        cmds = grouped.setdefault(block.context, [])
        if block.command not in cmds:
            cmds.append(block.command)

    result: list[str] = []
    for context, commands in grouped.items():
        result.extend(context)
        result.extend(commands)

    return result


def build_staging(old_file_lines: list[str], new_file_lines: list[str]) -> str:
    """Turn raw file content into CLI commands."""
    if not new_file_lines:
        return ""

    added_indices, deleted_indices = _parse_diff(old_file_lines, new_file_lines)
    if not added_indices and not deleted_indices:
        return ""

    new_parse = CiscoConfParse(new_file_lines, syntax='ios')
    old_parse = CiscoConfParse(old_file_lines or ['!'], syntax='ios')

    blocks = _build_command_blocks(added_indices, deleted_indices, new_parse, old_parse)
    grouped = _group_blocks_by_context(blocks)

    return '\n'.join(grouped)
