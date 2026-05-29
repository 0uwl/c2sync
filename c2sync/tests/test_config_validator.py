from c2sync.config_validator import validate_config, validate_staging


# ---------------------------------------------------------------------------
# validate_config
# ---------------------------------------------------------------------------

def test_valid_config_returns_no_warnings():
    config = [
        "hostname Router1",
        "interface GigabitEthernet0/0",
        " ip address 192.168.1.1 255.255.255.0",
        " no shutdown",
    ]
    assert validate_config(config) == []


def test_empty_config_returns_warning():
    warnings = validate_config([])
    assert len(warnings) > 0
    assert "empty" in warnings[0].lower()


def test_missing_hostname_returns_warning():
    config = [
        "interface GigabitEthernet0/0",
        " no shutdown",
    ]
    warnings = validate_config(config)
    assert any("hostname" in w for w in warnings)


def test_duplicate_block_returns_warning():
    config = [
        "hostname Router1",
        "interface GigabitEthernet0/0",
        " no shutdown",
        "interface GigabitEthernet0/0",
        " shutdown",
    ]
    warnings = validate_config(config)
    assert any("Duplicate" in w for w in warnings)


def test_valid_config_with_multiple_interfaces_no_warnings():
    config = [
        "hostname Router1",
        "interface GigabitEthernet0/0",
        " ip address 10.0.0.1 255.255.255.0",
        " no shutdown",
        "interface GigabitEthernet0/1",
        " ip address 10.0.1.1 255.255.255.0",
        " no shutdown",
    ]
    assert validate_config(config) == []


# ---------------------------------------------------------------------------
# validate_staging
# ---------------------------------------------------------------------------

def test_valid_staging_returns_no_warnings():
    commands = ["interface GigabitEthernet0/0", "no shutdown"]
    assert validate_staging(commands) == []


def test_empty_staging_returns_no_warnings():
    assert validate_staging([]) == []


def test_blank_staging_command_returns_warning():
    commands = ["interface GigabitEthernet0/0", "", "no shutdown"]
    warnings = validate_staging(commands)
    assert len(warnings) > 0
    assert "blank" in warnings[0].lower()


def test_multiple_blank_commands_counted():
    commands = ["interface Gi0/0", "", "no shutdown", ""]
    warnings = validate_staging(commands)
    assert len(warnings) > 0
    assert "2" in warnings[0]
