"""Thin wrapper around ddcutil for reading/setting VCP feature 0x60 (input source).

StreamController runs inside a flatpak sandbox that cannot touch /dev/i2c directly,
so host commands are routed through `flatpak-spawn --host` (the sandbox grants
org.freedesktop.Flatpak for exactly this). The host user must be in the `i2c` group.
"""

import os
import subprocess

INPUT_SOURCE_VCP = "60"


def _in_flatpak() -> bool:
    return os.path.isfile("/.flatpak-info")


def _wrap(args: list[str]) -> list[str]:
    cmd = ["ddcutil", *args]
    if _in_flatpak():
        cmd = ["flatpak-spawn", "--host", *cmd]
    return cmd


def _run(args: list[str], timeout: float = 10.0) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(_wrap(args), capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def _selector(serial: str) -> list[str]:
    return ["--sn", serial] if serial else []


def _normalize(code: str) -> str:
    """Normalize a hex input code to a lowercase 2-char string, e.g. 'X11' -> '11'."""
    code = code.strip().lower()
    if code.startswith("0x"):
        code = code[2:]
    elif code.startswith("x"):
        code = code[1:]
    return code.zfill(2)


def get_input(serial: str) -> str | None:
    """Return the monitor's current input source as a 2-char hex string, or None on failure."""
    r = _run([*_selector(serial), "-t", "getvcp", INPUT_SOURCE_VCP])
    if r is None or r.returncode != 0:
        return None
    # Terse format: "VCP 60 SNC x11"
    parts = r.stdout.split()
    if len(parts) >= 4 and parts[3].lower().startswith("x"):
        return _normalize(parts[3])
    return None


def set_input(serial: str, code: str) -> bool:
    """Set the monitor's input source. Returns True on success."""
    r = _run([*_selector(serial), "setvcp", INPUT_SOURCE_VCP, _normalize(code)])
    return r is not None and r.returncode == 0
