from c2sync import project_manager

def get_status(device=None) -> str:
    data = project_manager.load_registry()

    results = {}

    for name in data:
        dev = project_manager.get_device(name)

        if dev.staging_path.exists() and dev.staging_path.stat().st_size > 0:
            state = "HOST_PENDING"
        else:
            state = "SYNCED"

        results[name] = state

    if device:
        return {device: results.get(device)}

    return results