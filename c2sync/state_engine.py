from c2sync import models

def get_status(device=None) -> str:
    data = models.load_registry()

    results = {}

    for name in data:
        dev = models.get_device(name)

        if dev.staging_path.exists() and dev.staging_path.stat().st_size > 0:
            state = "HOST_PENDING"
        else:
            state = "SYNCED"

        results[name] = state

    if device:
        return {device: results.get(device)}

    return results