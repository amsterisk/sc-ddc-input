"""Pure-ish state engine: read monitor inputs, match against states, apply a state.

Kept free of GTK so it can be reasoned about and tested on its own. The only side
effects are the ddcutil reads/writes via ddc.py.
"""

from .ddc import get_input, set_input


def _norm(code) -> str | None:
    if code is None:
        return None
    c = str(code).strip().lower()
    if c.startswith("0x"):
        c = c[2:]
    elif c.startswith("x"):
        c = c[1:]
    return c.zfill(2)


def read_inputs(monitors: list[dict], quiet: bool = False) -> dict[str, str | None]:
    """Return {serial: current_hex_or_None} for every configured monitor."""
    readings = {}
    for m in monitors:
        serial = m.get("serial")
        if serial:
            readings[serial] = get_input(serial, quiet=quiet)
    return readings


def match_state(states: list[dict], readings: dict[str, str | None]) -> str | None:
    """Name of the first state whose present targets all match reality, else None.

    Monitors that are missing/unreadable (reading is None) are ignored. A state with
    no readable targets does not match (avoids matching scenes whose monitors are all
    absent).
    """
    for st in states:
        targets = st.get("targets", {})
        checked = 0
        ok = True
        for serial, want in targets.items():
            actual = readings.get(serial)
            if actual is None:
                continue  # missing monitor -> ignore
            checked += 1
            if _norm(actual) != _norm(want):
                ok = False
                break
        if ok and checked > 0:
            return st.get("name")
    return None


def next_state_name(states: list[dict], current_name: str | None) -> str | None:
    """Next state name in cycle order; first state if current is unknown/None."""
    names = [s.get("name") for s in states if s.get("name")]
    if not names:
        return None
    if current_name in names:
        return names[(names.index(current_name) + 1) % len(names)]
    return names[0]


def state_by_name(states: list[dict], name: str) -> dict | None:
    for s in states:
        if s.get("name") == name:
            return s
    return None


def apply_state(state: dict) -> dict[str, bool]:
    """Set each target's monitor to its input. Returns {serial: success}.

    Monitors that can't be reached simply report False and are otherwise ignored.
    """
    results = {}
    for serial, hex_code in state.get("targets", {}).items():
        results[serial] = set_input(serial, hex_code)
    return results
