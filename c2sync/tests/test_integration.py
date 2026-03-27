from c2sync.diff_engine import Differ

from constants import PROJECT

def test_differ_applies_to_mock_device(mock_device):
    """
    Full integration test:
    1. Diff configs
    2. Generate commands
    3. Send to mock device
    4. Validate resulting config
    """

    old_config = mock_device.get_running_config()

    new_config = [
        "hostname Router1",
        "interface Gi1/0/1",
        " description OLD",
        " shutdown",
    ]

    differ = Differ(PROJECT)

    # Generate commands
    differ.save_to_staging(old_config, new_config)

    # Read staged commands
    with open(PROJECT.STAGING_FILE) as f:
        commands = [line.strip() for line in f if line.strip()]

    # Send commands to mock device
    mock_device.send_config_set(commands)

    updated_config = mock_device.get_running_config()

    # Validate result
    assert " shutdown" in updated_config