"""Thin wrapper around ddcutil for reading/setting VCP feature 0x60 (input source).

StreamController runs inside a flatpak sandbox that cannot touch /dev/i2c directly,
so host commands are routed through `flatpak-spawn --host` (the sandbox grants
org.freedesktop.Flatpak for exactly this). The host user must be in the `i2c` group.
"""

import os
import re
import subprocess
import threading

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


# Serial -> i2c bus number cache. Targeting by --bus is far faster than --sn
# (which scans every bus) and, unlike --sn, is safe to run in parallel across
# monitors. The cache is refreshed via `ddcutil detect` on a miss and dropped
# for a serial whenever one of its commands fails (e.g. bus renumbered).
_BUS_CACHE: dict[str, str] = {}
_BUS_LOCK = threading.Lock()


def _detect_bus_map() -> dict[str, str]:
    out = {}
    for m in detect_monitors():
        serial = m.get("serial")
        bus = m.get("bus", "")  # e.g. "/dev/i2c-10"
        num = "".join(c for c in bus.rsplit("-", 1)[-1] if c.isdigit()) if bus else ""
        if serial and num:
            out[serial] = num
    return out


def _bus_for(serial: str) -> str | None:
    if not serial:
        return None
    if serial not in _BUS_CACHE:
        with _BUS_LOCK:
            if serial not in _BUS_CACHE:
                _BUS_CACHE.update(_detect_bus_map())
                # Sentinel: an undetected serial falls back to --sn without
                # re-running detect on every call (only re-tried after a failure).
                _BUS_CACHE.setdefault(serial, None)
    return _BUS_CACHE.get(serial)


def _forget_bus(serial: str) -> None:
    _BUS_CACHE.pop(serial, None)


def _selector(serial: str) -> list[str]:
    if not serial:
        return []
    bus = _bus_for(serial)
    if bus:
        return ["--bus", bus]
    # No cached bus: a "BUS:n" identity still names its bus directly. Synthetic
    # identities (no real ASCII serial) can't use --sn, so let the command fail
    # cleanly rather than silently target ddcutil's default display.
    if serial.startswith("BUS:"):
        return ["--bus", serial[4:]]
    if serial.startswith(("BSN:", "DRM:")):
        return ["--sn", "\x00__no_such_monitor__"]
    return ["--sn", serial]


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
        _forget_bus(serial)  # drop a possibly-stale bus mapping before retrying
    return None


def set_input(serial: str, code: str) -> bool:
    """Set the monitor's input source. Returns True on success.

    ddcutil's setvcp rejects bare hex ('1b'); the value must be 0x-prefixed.
    """
    value = "0x" + _normalize(code)
    for _ in range(2):
        r = _run([*_selector(serial), "setvcp", INPUT_SOURCE_VCP, value])
        if r is not None and r.returncode == 0:
            return True
        _forget_bus(serial)  # bus may have renumbered; re-resolve on retry
    return False


def _identity(ascii_sn: str, binary_sn: str, connector: str, bus: str) -> str:
    """A stable per-monitor key.

    Prefer the EDID ASCII serial; many monitors (e.g. some LG panels) leave it
    blank, so fall back to the binary serial (still unique per physical panel and
    stable across reboots), then the DRM connector, then the i2c bus.
    """
    if ascii_sn:
        return ascii_sn
    if binary_sn and binary_sn not in ("0", "0x0", "0x00000000"):
        return f"BSN:{binary_sn}"
    if connector:
        return f"DRM:{connector}"
    return f"BUS:{bus}" if bus else ""


def detect_monitors() -> list[dict]:
    """Return [{'serial', 'model', 'bus'}] for connected DDC-capable monitors.

    `serial` is a stable identity (see _identity); monitors without an ASCII EDID
    serial are kept and keyed by their binary serial, so identical-model panels
    (which often share a blank ASCII serial) are still told apart. The full (non
    -terse) `detect` output is parsed because `--terse` omits the binary serial.
    """
    r = _run(["detect"], timeout=30.0)
    if r is None or r.returncode != 0:
        return []
    monitors: list[dict] = []
    cur: dict = {}

    def flush():
        if not cur.get("bus"):
            return
        ident = _identity(cur.get("ascii_serial", ""), cur.get("binary_serial", ""),
                          cur.get("connector", ""), cur.get("bus", ""))
        if ident:
            monitors.append({"serial": ident, "model": cur.get("model", ""),
                             "bus": cur.get("bus", "")})

    for line in r.stdout.splitlines():
        s = line.strip()
        if s.startswith("Display "):
            flush()
            cur = {}
        elif s.startswith("I2C bus:"):
            cur["bus"] = s.split(":", 1)[1].strip()
        elif s.startswith("DRM connector:"):
            cur["connector"] = s.split(":", 1)[1].strip()
        elif s.startswith("Model:"):
            cur["model"] = s.split(":", 1)[1].strip()
        elif s.startswith("Serial number:"):
            cur["ascii_serial"] = s.split(":", 1)[1].strip()
        elif s.startswith("Binary serial number:"):
            # value format: "498849 (0x00079ca1)" -> take the decimal token
            cur["binary_serial"] = s.split(":", 1)[1].strip().split()[0]
    flush()
    return monitors


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
