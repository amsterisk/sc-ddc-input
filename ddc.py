"""Thin wrapper around ddcutil for reading/setting VCP feature 0x60 (input source).

StreamController runs inside a flatpak sandbox that cannot touch /dev/i2c directly,
so host commands are routed through `flatpak-spawn --host` (the sandbox grants
org.freedesktop.Flatpak for exactly this). The host user must be in the `i2c` group.
"""

import os
import re
import subprocess

from loguru import logger as log

INPUT_SOURCE_VCP = "60"


def _in_flatpak() -> bool:
    return os.path.isfile("/.flatpak-info")


def _wrap(args: list[str]) -> list[str]:
    cmd = ["ddcutil", *args]
    if _in_flatpak():
        cmd = ["flatpak-spawn", "--host", *cmd]
    return cmd


def _run(args: list[str], timeout: float = 20.0, quiet: bool = False) -> subprocess.CompletedProcess | None:
    cmd = _wrap(args)
    try:
        # flatpak-spawn --host inherits StreamController's cwd (/app/bin/StreamController),
        # which doesn't exist on the host and makes the portal spawn fail. Run from $HOME.
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           cwd=os.path.expanduser("~"))
    except Exception as e:
        if not quiet:
            log.error(f"DDCInput: failed to run {cmd}: {type(e).__name__}: {e}")
        return None
    if r.returncode != 0 and not quiet:
        log.error(f"DDCInput: {' '.join(cmd)} -> rc={r.returncode} stderr={r.stderr.strip()!r}")
    return r


def _selector(serial: str) -> list[str]:
    return ["--sn", serial] if serial else []


def _normalize(code: str) -> str:
    """Normalize a hex input code to a bare lowercase 2-char string, e.g. 'X11' -> '11'."""
    code = code.strip().lower()
    if code.startswith("0x"):
        code = code[2:]
    elif code.startswith("x"):
        code = code[1:]
    return code.zfill(2)


def get_input(serial: str, retries: int = 1, quiet: bool = False) -> str | None:
    """Return the monitor's current input source as a bare 2-char hex string, or None.

    DDC reads can intermittently fail (esp. the first read after the bus is idle),
    so retry a couple of times before giving up. Pass quiet=True to suppress error
    logging for expected-to-fail probes (e.g. the startup warm-up).
    """
    for _ in range(retries + 1):
        r = _run([*_selector(serial), "-t", "getvcp", INPUT_SOURCE_VCP], quiet=quiet)
        if r is not None and r.returncode == 0:
            # Terse format: "VCP 60 SNC x11"
            parts = r.stdout.split()
            if len(parts) >= 4 and parts[3].lower().startswith("x"):
                return _normalize(parts[3])
            if not quiet:
                log.error(f"DDCInput: could not parse getvcp output {r.stdout!r}")
    return None


def set_input(serial: str, code: str) -> bool:
    """Set the monitor's input source. Returns True on success.

    ddcutil's setvcp rejects bare hex ('1b'); the value must be 0x-prefixed.
    """
    r = _run([*_selector(serial), "setvcp", INPUT_SOURCE_VCP, "0x" + _normalize(code)])
    return r is not None and r.returncode == 0


def detect_monitors() -> list[dict]:
    """Return [{'serial', 'model', 'bus'}] for connected DDC-capable monitors."""
    r = _run(["detect", "--terse"], timeout=30.0)
    if r is None or r.returncode != 0:
        return []
    monitors: list[dict] = []
    cur: dict = {}
    for line in r.stdout.splitlines():
        s = line.strip()
        if s.startswith("Display "):
            if cur:
                monitors.append(cur)
            cur = {}
        elif s.startswith("I2C bus:"):
            cur["bus"] = s.split(":", 1)[1].strip()
        elif s.startswith("Monitor:"):
            # value format: "MFG:Model:Serial"
            parts = s.split(":", 1)[1].strip().split(":")
            if len(parts) >= 3:
                cur["model"] = parts[1].strip()
                cur["serial"] = parts[2].strip()
    if cur:
        monitors.append(cur)
    return [m for m in monitors if m.get("serial")]


def get_capabilities_inputs(serial: str) -> list[dict]:
    """Return [{'hex', 'name'}] for the input sources VCP 0x60 reports as supported."""
    r = _run([*_selector(serial), "capabilities"], timeout=30.0)
    if r is None or r.returncode != 0:
        return []
    inputs: list[dict] = []
    in_feature = False
    for line in r.stdout.splitlines():
        s = line.strip()
        if s.startswith("Feature: 60"):
            in_feature = True
            continue
        if in_feature:
            if s.startswith("Feature:"):
                break
            m = re.match(r"^([0-9a-fA-F]{1,2}):\s*(.*)$", s)
            if m:
                name = m.group(2).strip()
                if name.lower() == "unrecognized value":
                    name = ""
                inputs.append({"hex": m.group(1).lower().zfill(2), "name": name})
    return inputs
