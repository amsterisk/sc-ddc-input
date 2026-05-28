import threading
import time

from loguru import logger as log

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib

from PIL import Image, ImageDraw

from src.backend.PluginManager.ActionBase import ActionBase

from .ddc import get_input, set_input

# Defaults target the Dell U4924DW: cycle USB-C (Work) <-> HDMI-1 (Home).
DEFAULT_SERIAL = "8KKZ0S3"
DEFAULT_CYCLE = "1b,11"
DEFAULT_LABELS = "1b=Work,11=Home,0f=DP-1,12=HDMI-2"
DEFAULT_POLL = 5  # seconds between background state polls; 0 disables

_SPINNER_FRAMES = None
_SPINNER_LOCK = threading.Lock()


def _spinner_frames(n: int = 12, size: int = 144) -> list[Image.Image]:
    """Lazily build and cache a rotating-arc spinner animation."""
    global _SPINNER_FRAMES
    with _SPINNER_LOCK:
        if _SPINNER_FRAMES is None:
            margin = size * 0.2
            bbox = [margin, margin, size - margin, size - margin]
            width = max(3, size // 10)
            frames = []
            for i in range(n):
                img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
                start = (360 / n) * i
                ImageDraw.Draw(img).arc(bbox, start, start + 270,
                                        fill=(255, 255, 255, 255), width=width)
                frames.append(img)
            _SPINNER_FRAMES = frames
        return _SPINNER_FRAMES


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
        self._busy = False            # locks presses + drives spinner while a switch runs
        self._spin_thread = None
        self._poll_ticks = 0
        self._polling = False
        self._last_shown = None

    def _config(self) -> tuple[str, list[str], dict[str, str], int]:
        s = self.get_settings()
        return (
            s.get("serial", DEFAULT_SERIAL),
            _parse_cycle(s.get("cycle", DEFAULT_CYCLE)),
            _parse_labels(s.get("labels", DEFAULT_LABELS)),
            int(s.get("poll", DEFAULT_POLL) or 0),
        )

    # ---------- lifecycle ----------
    def on_ready(self):
        # ddcutil's first probes right after app launch often fail ("Display not
        # found") for a few seconds, so keep trying briefly instead of showing "?".
        threading.Thread(target=self._warmup, name="DDCWarmup", daemon=True).start()

    def _warmup(self, attempts: int = 10, delay: float = 1.5):
        serial, _, labels, _ = self._config()
        for _ in range(attempts):
            if self._busy:
                return  # a press already took over
            current = get_input(serial, retries=0, quiet=True)
            if current:
                self._show(current, labels)
                return
            time.sleep(delay)
        self._show(None, labels)

    def on_tick(self):
        # Called ~1x/second. Poll the monitor periodically so the label stays
        # correct if the input is switched by other means (OSD, the other machine).
        _, _, _, poll = self._config()
        if poll <= 0 or self._busy or self._polling or not self.get_is_present():
            return
        self._poll_ticks += 1
        if self._poll_ticks < poll:
            return
        self._poll_ticks = 0
        self._polling = True
        threading.Thread(target=self._poll_once, name="DDCPoll", daemon=True).start()

    def _poll_once(self):
        try:
            serial, _, labels, _ = self._config()
            current = get_input(serial)
            if current and current != self._last_shown:
                self._show(current, labels)
        finally:
            self._polling = False

    # ---------- press ----------
    def on_key_down(self):
        if self._busy:
            return  # locked out until the in-flight switch completes
        self._busy = True
        GLib.idle_add(self.set_center_label, "")
        self._start_spinner()
        threading.Thread(target=self._cycle, name="DDCCycle", daemon=True).start()

    def _cycle(self):
        serial, cycle, labels, _ = self._config()
        ok = False
        target = None
        try:
            if not cycle:
                log.warning("DDCInput: input cycle is empty; nothing to do")
                return
            current = get_input(serial)
            target = cycle[(cycle.index(current) + 1) % len(cycle)] if current in cycle else cycle[0]
            ok = set_input(serial, target)
            if not ok:
                log.error(f"DDCInput: failed to set input {target} on monitor {serial!r}")
        finally:
            # Drop the lock first so any queued spinner frames become no-ops,
            # wait for the spinner thread, then render the final state.
            self._busy = False
            if self._spin_thread is not None:
                self._spin_thread.join(timeout=0.5)
            if target is None:
                GLib.idle_add(self._clear_image)
            elif ok:
                self._show(target, labels)
            else:
                actual = get_input(serial)
                self._last_shown = actual
                text = labels.get(actual, actual.upper()) if actual else "?"
                GLib.idle_add(self._render_error, text)

    # ---------- spinner ----------
    def _start_spinner(self):
        if self._spin_thread is not None and self._spin_thread.is_alive():
            return
        self._spin_thread = threading.Thread(target=self._spin, name="DDCSpin", daemon=True)
        self._spin_thread.start()

    def _spin(self):
        frames = _spinner_frames()
        i = 0
        while self._busy:
            GLib.idle_add(self._draw_spin_frame, frames[i % len(frames)])
            i += 1
            time.sleep(0.08)

    def _draw_spin_frame(self, image):
        if not self._busy:
            return False  # switch finished; ignore stale frame
        try:
            self.set_media(image=image)
        except Exception:
            pass
        return False

    def _clear_image(self):
        try:
            self.set_media(image=None)
        except Exception:
            pass
        return False

    # ---------- label rendering ----------
    def _refresh_async(self):
        threading.Thread(target=self._refresh, name="DDCRefresh", daemon=True).start()

    def _refresh(self):
        serial, _, labels, _ = self._config()
        self._show(get_input(serial), labels)

    def _show(self, code, labels):
        self._last_shown = code
        text = labels.get(code, code.upper()) if code else "?"
        GLib.idle_add(self._render_label, text)

    def _render_label(self, text):
        # Stage the label (update=False), then let set_media's update do the single
        # final composite — set_center_label's own redraw doesn't repaint reliably
        # after the spinner has driven the image layer.
        try:
            self.hide_error()
        except Exception:
            pass
        try:
            self.set_center_label(text, update=False)
        except Exception as e:
            log.error(f"DDCInput: failed to set label {text!r}: {e}")
        try:
            self.set_media(image=None, update=True)
        except Exception:
            pass
        return False

    def _render_error(self, text):
        try:
            self.set_center_label(text, update=False)
        except Exception:
            pass
        try:
            self.set_media(image=None, update=True)
        except Exception:
            pass
        try:
            self.show_error()
        except Exception:
            pass
        return False

    # ---------- config ----------
    def get_config_rows(self):
        s = self.get_settings()

        self.serial_row = Adw.EntryRow(title="Monitor serial number")
        self.serial_row.set_text(s.get("serial", DEFAULT_SERIAL))

        self.cycle_row = Adw.EntryRow(title="Input cycle (hex, comma-separated)")
        self.cycle_row.set_text(s.get("cycle", DEFAULT_CYCLE))

        self.labels_row = Adw.EntryRow(title="Labels (hex=Name, comma-separated)")
        self.labels_row.set_text(s.get("labels", DEFAULT_LABELS))

        self.poll_row = Adw.SpinRow.new_with_range(0, 60, 1)
        self.poll_row.set_title("Poll interval (s)")
        self.poll_row.set_subtitle("Re-read the monitor to catch manual switches (0 = off)")
        self.poll_row.set_value(int(s.get("poll", DEFAULT_POLL) or 0))

        for row in (self.serial_row, self.cycle_row, self.labels_row):
            row.connect("notify::text", self._on_config_changed)
        self.poll_row.connect("changed", self._on_config_changed)

        return [self.serial_row, self.cycle_row, self.labels_row, self.poll_row]

    def _on_config_changed(self, *_):
        s = self.get_settings()
        s["serial"] = self.serial_row.get_text()
        s["cycle"] = self.cycle_row.get_text()
        s["labels"] = self.labels_row.get_text()
        s["poll"] = int(self.poll_row.get_value())
        self.set_settings(s)
        self._refresh_async()
