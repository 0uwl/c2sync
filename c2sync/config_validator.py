from ciscoconfparse2 import CiscoConfParse


def validate_config(lines: list[str]) -> list[str]:
    """Parse a pulled running-config and return a list of warning strings.
    Returns an empty list when no issues are found."""
    if not lines:
        return ["Config is empty — pull may have failed or returned no output"]

    warnings = []

    try:
        parse = CiscoConfParse(lines, syntax='ios')
    except Exception as e:
        return [f"Config parse error: {e}"]

    if not parse.find_objects(r'^hostname\s'):
        warnings.append("No 'hostname' line found — config may be truncated")

    # Detect duplicate top-level parent blocks (e.g., same interface defined twice)
    seen: set[str] = set()
    for obj in parse.objs:
        if obj.indent == 0 and obj.has_children:
            text = obj.text.strip()
            if text in seen:
                warnings.append(f"Duplicate config block: '{text}'")
            seen.add(text)

    return warnings


def validate_staging(commands: list[str]) -> list[str]:
    """Validate a list of staging commands and return a list of warning strings.
    Returns an empty list when no issues are found."""
    if not commands:
        return []

    blank_count = sum(1 for cmd in commands if not cmd.strip())
    if blank_count:
        return [f"{blank_count} blank command(s) in staging — may cause unexpected device output"]

    return []
