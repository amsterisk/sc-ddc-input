"""Pure-ish state engine: read monitor inputs, match against states, apply a state.

Kept free of GTK so it can be reasoned about and tested on its own. The only side
effects are the ddcutil reads/writes via ddc.py. Reads and writes run in parallel
across monitors, and applying a state skips monitors already at the wanted input.
"""

import time
from concurrent.futures import ThreadPoolExecutor

from loguru import logger as log

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


def _parallel(fn, items: list):
    """Map fn over items, concurrently when there's more than one."""
    items = list(items)
    if len(items) <= 1:
        return [fn(i) for i in items]
    with ThreadPoolExecutor(max_workers=len(items)) as ex:
        return list(ex.map(fn, items))


def read_inputs(monitors: list[dict], quiet: bool = False, debug: bool = False,
                retries: int = 1) -> dict[str, str | None]:
    """Return {serial: current_hex_or_None}, reading all monitors in parallel."""
    serials = [m["serial"] for m in monitors if m.get("serial")]

    def rd(serial):
        t = time.monotonic()
        value = get_input(serial, retries=retries, quiet=quiet)
        if debug:
            log.info(f"DDCInput[timing]: read {serial} = {value!r} in {time.monotonic() - t:.2f}s")
        return serial, value

    return dict(_parallel(rd, serials))


def match_state(states: list[dict], readings: dict[str, str | None]) -> str | None:
    """Name of the first state whose present targets all match reality, else None."""
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


def apply_state(state: dict, current: dict[str, str | None] | None = None,
                debug: bool = False) -> dict[str, bool]:
    """Set each target's monitor to its input, in parallel.

    Monitors already at the wanted input (per `current` readings) are skipped as
    no-ops and reported as success. Returns {serial: success}.
    """
    targets = state.get("targets", {})
    results: dict[str, bool] = {}
    to_set: list[tuple[str, str]] = []

    for serial, want in targets.items():
        cur = current.get(serial) if current else None
        if cur is not None and _norm(cur) == _norm(want):
            results[serial] = True  # already there -> no DDC write
            if debug:
                log.info(f"DDCInput[timing]: {serial} already at {want} — noop")
        else:
            to_set.append((serial, want))

    def do_set(item):
        serial, want = item
        t = time.monotonic()
        ok = set_input(serial, want)
        if debug:
            log.info(f"DDCInput[timing]: set {serial} = {want} -> {ok} in {time.monotonic() - t:.2f}s")
        return serial, ok

    for serial, ok in _parallel(do_set, to_set):
        results[serial] = ok
    return results
