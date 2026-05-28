import threading

from loguru import logger as log

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib

from src.backend.PluginManager.ActionBase import ActionBase

from .ddc import get_input, set_input

# Defaults target the Dell U4924DW: cycle USB-C (Work) <-> HDMI-1 (Home).
DEFAULT_SERIAL = "8KKZ0S3"
DEFAULT_CYCLE = "1b,11"
DEFAULT_LABELS = "1b=Work,11=Home,0f=DP-1,12=HDMI-2"


def _parse_cycle(text: str) -> list[str]:
    return [t.strip().lower().zfill(2) for t in (text or "").split(",") if t.strip()]


def _parse_labels(text: str) -> dict[str, str]:
    labels = {}
    for pair in (text or "").split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            labels[k.strip().lower().zfill(2)] = v.strip()
    return labels


class CycleInput(ActionBase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.has_configuration = True

    def _config(self) -> tuple[str, list[str], dict[str, str]]:
        s = self.get_settings()
        return (
            s.get("serial", DEFAULT_SERIAL),
            _parse_cycle(s.get("cycle", DEFAULT_CYCLE)),
            _parse_labels(s.get("labels", DEFAULT_LABELS)),
        )

    def on_ready(self):
        self._refresh_label_async()

    def on_key_down(self):
        threading.Thread(target=self._cycle, name="DDCCycle", daemon=True).start()

    def _cycle(self):
        serial, cycle, labels = self._config()
        if not cycle:
            log.warning("DDCInput: input cycle is empty; nothing to do")
            return

        current = get_input(serial)
        if current in cycle:
            target = cycle[(cycle.index(current) + 1) % len(cycle)]
        else:
            # Unknown/unreadable current input: jump to the first entry.
            target = cycle[0]

        if set_input(serial, target):
            self._set_label(labels.get(target, target.upper()))
        else:
            log.error(f"DDCInput: failed to set input {target} on monitor {serial!r}")
            self._set_label("ERR")

    def _refresh_label_async(self):
        threading.Thread(target=self._refresh_label, name="DDCRefresh", daemon=True).start()

    def _refresh_label(self):
        serial, _, labels = self._config()
        current = get_input(serial)
        self._set_label(labels.get(current, current.upper()) if current else "?")

    def _set_label(self, text: str):
        # Marshal GTK/key updates back onto the main loop (called from worker threads).
        GLib.idle_add(self.set_center_label, text)

    def get_config_rows(self):
        s = self.get_settings()

        self.serial_row = Adw.EntryRow(title="Monitor serial number")
        self.serial_row.set_text(s.get("serial", DEFAULT_SERIAL))

        self.cycle_row = Adw.EntryRow(title="Input cycle (hex, comma-separated)")
        self.cycle_row.set_text(s.get("cycle", DEFAULT_CYCLE))

        self.labels_row = Adw.EntryRow(title="Labels (hex=Name, comma-separated)")
        self.labels_row.set_text(s.get("labels", DEFAULT_LABELS))

        for row in (self.serial_row, self.cycle_row, self.labels_row):
            row.connect("notify::text", self._on_config_changed)

        return [self.serial_row, self.cycle_row, self.labels_row]

    def _on_config_changed(self, *_):
        s = self.get_settings()
        s["serial"] = self.serial_row.get_text()
        s["cycle"] = self.cycle_row.get_text()
        s["labels"] = self.labels_row.get_text()
        self.set_settings(s)
        self._refresh_label_async()
