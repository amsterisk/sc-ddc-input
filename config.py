"""Plugin-global configuration: the monitors and named states (scenes).

Stored via PluginBase.get_settings()/set_settings() so every key action shares
one definition. Shape:

    {
      "monitors": [
        {"serial": "8KKZ0S3", "name": "Ultrawide",
         "inputs": [{"hex": "11", "name": "HDMI-1"}, ...]}
      ],
      "states": [                              # order == cycle order
        {"name": "Home", "targets": {"8KKZ0S3": "11", "CPBXTY2": "0f"}}
      ]
    }
"""


def load(plugin_base) -> dict:
    s = plugin_base.get_settings() or {}
    return {
        "monitors": s.get("monitors", []),
        "states": s.get("states", []),
        "debug": bool(s.get("debug", False)),
    }


def save(plugin_base, config: dict) -> None:
    s = plugin_base.get_settings() or {}
    s["monitors"] = config.get("monitors", [])
    s["states"] = config.get("states", [])
    if "debug" in config:
        s["debug"] = bool(config["debug"])
    plugin_base.set_settings(s)


def monitor_by_serial(config: dict, serial: str) -> dict | None:
    for m in config.get("monitors", []):
        if m.get("serial") == serial:
            return m
    return None


def input_name(config: dict, serial: str, hex_code: str) -> str | None:
    """Friendly name for a given monitor input, or None if not defined."""
    m = monitor_by_serial(config, serial)
    if not m:
        return None
    for i in m.get("inputs", []):
        if i.get("hex") == hex_code:
            return i.get("name") or hex_code.upper()
    return None
