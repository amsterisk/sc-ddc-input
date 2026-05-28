"""Plugin-global settings UI: edit monitors (serial/name/inputs) and states (scenes).

Returned from PluginBase.get_settings_area() as a single Adw.PreferencesGroup. The
group is rebuilt from the config model on any structural change (add/remove/detect);
field edits update the model and save in place. The action reads the config fresh on
every press/poll, so edits take effect without restarting StreamController.
"""

import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, GLib

from . import config
from .ddc import detect_monitors, get_capabilities_inputs

NONE_LABEL = "—"  # em dash = "no target for this monitor"


def parse_inputs(text: str) -> list[dict]:
    out = []
    for pair in (text or "").split(","):
        pair = pair.strip()
        if not pair:
            continue
        if "=" in pair:
            h, n = pair.split("=", 1)
            out.append({"hex": h.strip().lower().zfill(2), "name": n.strip()})
        else:
            out.append({"hex": pair.lower().zfill(2), "name": ""})
    return out


def format_inputs(inputs: list[dict]) -> str:
    parts = []
    for i in inputs:
        h, n = i.get("hex", ""), i.get("name", "")
        parts.append(f"{h}={n}" if n else h)
    return ",".join(parts)


class PluginSettings:
    def __init__(self, plugin_base):
        self.plugin_base = plugin_base
        self.config = {"monitors": [], "states": []}
        self.group = None
        self._rows = []
        self._state_combos = {}  # serial -> [ComboRow]; retitled live on monitor rename

    def get_settings_area(self) -> Adw.PreferencesGroup:
        self.config = config.load(self.plugin_base)
        self.group = Adw.PreferencesGroup(title="Monitors and states")
        self._rows = []
        self._rebuild()
        return self.group

    # ---------- persistence + rebuild ----------
    def _save(self):
        config.save(self.plugin_base, self.config)

    def _add(self, row):
        self.group.add(row)
        self._rows.append(row)

    def _rebuild(self):
        for r in self._rows:
            self.group.remove(r)
        self._rows = []
        self._state_combos = {}

        self._add(self._header("Monitors"))
        for idx, mon in enumerate(self.config["monitors"]):
            self._add(self._monitor_row(idx, mon))
        self._add(self._monitor_actions_row())

        self._add(self._header("States (cycle order)"))
        for idx, st in enumerate(self.config["states"]):
            self._add(self._state_row(idx, st))
        self._add(self._state_add_row())
        return False  # usable as a GLib.idle_add callback

    def _header(self, text: str) -> Adw.ActionRow:
        row = Adw.ActionRow(title=f"<b>{text}</b>")
        row.set_activatable(False)
        return row

    # ---------- monitors ----------
    def _monitor_row(self, idx: int, mon: dict) -> Adw.ExpanderRow:
        exp = Adw.ExpanderRow(title=mon.get("name") or mon.get("serial") or f"Monitor {idx + 1}",
                              subtitle=mon.get("serial", ""))

        name = Adw.EntryRow(title="Name")
        name.set_text(mon.get("name", ""))
        name.connect("notify::text", self._on_mon_field, idx, "name", exp)
        exp.add_row(name)

        serial = Adw.EntryRow(title="Serial")
        serial.set_text(mon.get("serial", ""))
        serial.connect("notify::text", self._on_mon_field, idx, "serial", exp)
        exp.add_row(serial)

        inputs = Adw.EntryRow(title="Inputs (hex=Name, comma-separated)")
        inputs.set_text(format_inputs(mon.get("inputs", [])))
        inputs.connect("notify::text", self._on_mon_inputs, idx)
        exp.add_row(inputs)

        remove = Adw.ActionRow(title="Remove this monitor")
        btn = Gtk.Button(label="Remove", valign=Gtk.Align.CENTER)
        btn.add_css_class("destructive-action")
        btn.connect("clicked", self._on_mon_remove, idx)
        remove.add_suffix(btn)
        exp.add_row(remove)
        return exp

    def _monitor_actions_row(self) -> Adw.ActionRow:
        row = Adw.ActionRow(title="Add or detect monitors")
        add = Gtk.Button(label="Add", valign=Gtk.Align.CENTER)
        add.connect("clicked", self._on_mon_add)
        detect = Gtk.Button(label="Detect connected", valign=Gtk.Align.CENTER)
        detect.add_css_class("suggested-action")
        detect.connect("clicked", self._on_detect)
        row.add_suffix(add)
        row.add_suffix(detect)
        return row

    def _on_mon_field(self, entry, _pspec, idx, key, exp):
        self.config["monitors"][idx][key] = entry.get_text()
        if key == "name":
            serial = self.config["monitors"][idx].get("serial", "")
            exp.set_title(entry.get_text() or serial or "Monitor")
            for combo in self._state_combos.get(serial, []):
                combo.set_title(entry.get_text() or serial)
        elif key == "serial":
            exp.set_subtitle(entry.get_text())
        self._save()

    def _on_mon_inputs(self, entry, _pspec, idx):
        self.config["monitors"][idx]["inputs"] = parse_inputs(entry.get_text())
        self._save()

    def _on_mon_add(self, _btn):
        self.config["monitors"].append({"serial": "", "name": "", "inputs": []})
        self._save()
        GLib.idle_add(self._rebuild)

    def _on_mon_remove(self, _btn, idx):
        del self.config["monitors"][idx]
        self._save()
        GLib.idle_add(self._rebuild)

    def _on_detect(self, btn):
        btn.set_sensitive(False)
        btn.set_label("Detecting…")
        threading.Thread(target=self._detect_worker, name="DDCDetect", daemon=True).start()

    def _detect_worker(self):
        found = []
        for m in detect_monitors():
            found.append({
                "serial": m["serial"],
                "name": m.get("model") or m["serial"],
                "inputs": get_capabilities_inputs(m["serial"]),
            })
        GLib.idle_add(self._apply_detect, found)

    def _apply_detect(self, found):
        existing = {m.get("serial") for m in self.config["monitors"]}
        for m in found:
            if m["serial"] not in existing:
                self.config["monitors"].append(m)
        self._save()
        self._rebuild()
        return False

    # ---------- states ----------
    def _state_row(self, idx: int, st: dict) -> Adw.ExpanderRow:
        exp = Adw.ExpanderRow(title=st.get("name") or f"State {idx + 1}")

        name = Adw.EntryRow(title="Name")
        name.set_text(st.get("name", ""))
        name.connect("notify::text", self._on_state_name, idx, exp)
        exp.add_row(name)

        for mon in self.config["monitors"]:
            if mon.get("serial"):
                exp.add_row(self._state_target_row(idx, st, mon))

        remove = Adw.ActionRow(title="Remove this state")
        btn = Gtk.Button(label="Remove", valign=Gtk.Align.CENTER)
        btn.add_css_class("destructive-action")
        btn.connect("clicked", self._on_state_remove, idx)
        remove.add_suffix(btn)
        exp.add_row(remove)
        return exp

    def _state_target_row(self, state_idx: int, st: dict, mon: dict) -> Adw.ComboRow:
        serial = mon.get("serial", "")
        model = Gtk.StringList()
        model.append(NONE_LABEL)
        hexes = []
        for i in mon.get("inputs", []):
            model.append(i.get("name") or i.get("hex", ""))
            hexes.append(i.get("hex", ""))

        combo = Adw.ComboRow(title=mon.get("name") or serial, model=model)
        cur = st.get("targets", {}).get(serial)
        combo.set_selected(hexes.index(cur) + 1 if cur in hexes else 0)
        combo.connect("notify::selected", self._on_state_target, state_idx, serial, hexes)
        self._state_combos.setdefault(serial, []).append(combo)
        return combo

    def _on_state_name(self, entry, _pspec, idx, exp):
        self.config["states"][idx]["name"] = entry.get_text()
        exp.set_title(entry.get_text() or f"State {idx + 1}")
        self._save()

    def _on_state_target(self, combo, _pspec, state_idx, serial, hexes):
        sel = combo.get_selected()
        targets = self.config["states"][state_idx].setdefault("targets", {})
        if sel <= 0:
            targets.pop(serial, None)
        else:
            targets[serial] = hexes[sel - 1]
        self._save()

    def _state_add_row(self) -> Adw.ActionRow:
        row = Adw.ActionRow(title="Add state")
        add = Gtk.Button(label="Add", valign=Gtk.Align.CENTER)
        add.connect("clicked", self._on_state_add)
        row.add_suffix(add)
        return row

    def _on_state_add(self, _btn):
        self.config["states"].append({"name": "New state", "targets": {}})
        self._save()
        GLib.idle_add(self._rebuild)

    def _on_state_remove(self, _btn, idx):
        del self.config["states"][idx]
        self._save()
        GLib.idle_add(self._rebuild)
