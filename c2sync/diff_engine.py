from c2sync.logger import get_logger

LOGGER = get_logger()

def get_indentation(line):
    return len(line) - len(line.lstrip())


def build_context(file_lines, index):
    stack = []
    current_indent = get_indentation(file_lines[index])

    for i in range(index - 1, -1, -1):
        line = file_lines[i]
        if get_indentation(line) < current_indent:
            stack.insert(0, line.strip())
            current_indent = get_indentation(line)

        if current_indent == 0:
            break

    return stack


def recontextualize_lines(file_lines, changed_lines):
    commands = []

    for line in changed_lines:
        idx = file_lines.index(line)
        context = build_context(file_lines, idx)
        commands.append(context + [line.strip()])

    return group_commands_by_context(commands)


def group_commands_by_context(commands):
    grouped = {}

    for cmd in commands:
        key = tuple(cmd[:-1])
        grouped.setdefault(key, []).append(cmd[-1])

    result = []
    for ctx, cmds in grouped.items():
        result.extend(ctx)
        result.extend(cmds)

    return result