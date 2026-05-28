import threading
import time

from loguru import logger as log

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib

from PIL import Image, ImageDraw

from src.backend.PluginManager.ActionBase import ActionBase

from . import config, engine

DEFAULT_POLL = 5
UNKNOWN = "Unknown"

_SPINNER_FRAMES = None
_SPINNER_LOCK = threading.Lock()


def _spinner_frames(n: int = 12, size: int = 144) -> list[Image.Image]:
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


class CycleState(ActionBase):
    """Cycle the configured states (scenes), applying input switches across monitors."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.has_configuration = True
        self._busy = False
        self._spin_thread = None
        self._poll_ticks = 0
        self._polling = False
        self._poll_fail_streak = 0  # consecutive all-unreadable polls -> back off
        self._last_shown = None
        self._ddc_lock = threading.Lock()  # serializes poll reads vs press read+apply

    def _poll_secs(self) -> int:
        return int(self.get_settings().get("poll", DEFAULT_POLL) or 0)

    # ---------- lifecycle ----------
    def on_ready(self):
        threading.Thread(target=self._warmup, name="DDCWarmup", daemon=True).start()

    def _warmup(self, attempts: int = 10, delay: float = 1.5):
        # Retry only while reads are failing; a successful read with no match is a
        # legitimate "Unknown", not a transient.
        for _ in range(attempts):
            if self._busy:
                return
            cfg = self._cfg()
            readings = engine.read_inputs(cfg["monitors"], quiet=True)
            if readings and any(v is not None for v in readings.values()):
                self._show(engine.match_state(cfg["states"], readings) or UNKNOWN)
                return
            time.sleep(delay)
        self._show(UNKNOWN)

    def on_tick(self):
        poll = self._poll_secs()
        if poll <= 0 or self._busy or self._polling or not self.get_is_present():
            return
        self._poll_ticks += 1
        # Back off (up to 16x) while monitors are unreachable, so a wedged/missing
        # monitor isn't hammered with a slow scan every interval.
        required = poll * (2 ** min(self._poll_fail_streak, 4))
        if self._poll_ticks < required:
            return
        self._poll_ticks = 0
        self._polling = True
        threading.Thread(target=self._poll_once, name="DDCPoll", daemon=True).start()

    def _poll_once(self):
        try:
            # Skip entirely if a transition is in progress or starting (can't get
            # the DDC lock) — polling mid-switch reads transitional state.
            if self._busy or not self._ddc_lock.acquire(blocking=False):
                return
            cfg = self._cfg()
            try:
                # Cheap: single-try, quiet (no error spam) — polling shouldn't retry.
                readings = engine.read_inputs(cfg["monitors"], quiet=True, retries=0,
                                              debug=cfg.get("debug", False))
            finally:
                self._ddc_lock.release()
            if readings and all(v is None for v in readings.values()):
                self._poll_fail_streak += 1
            else:
                self._poll_fail_streak = 0
            if not self._busy:
                name = engine.match_state(cfg["states"], readings) or UNKNOWN
                if name != self._last_shown:
                    self._show(name)
        finally:
            self._polling = False

    # ---------- press ----------
    def on_key_down(self):
        if self._busy:
            return
        self._busy = True
        self._poll_fail_streak = 0
        self._poll_ticks = 0
        GLib.idle_add(self.set_center_label, "")
        self._start_spinner()
        threading.Thread(target=self._cycle, name="DDCCycle", daemon=True).start()

    def _cycle(self):
        applied = None
        ok = False
        try:
            cfg = self._cfg()
            debug = cfg.get("debug", False)
            states = cfg["states"]
            if not states:
                log.warning("DDCInput: no states configured")
                return
            with self._ddc_lock:
                t0 = time.monotonic()
                readings = engine.read_inputs(cfg["monitors"], debug=debug)
                t_read = time.monotonic()
                current = engine.match_state(states, readings)
                target_name = engine.next_state_name(states, current)
                target = engine.state_by_name(states, target_name)
                if target is None:
                    return
                applied = target_name
                results = engine.apply_state(target, current=readings, debug=debug)
                ok = (not results) or any(results.values())
                if debug:
                    t_end = time.monotonic()
                    log.info(f"DDCInput[timing]: {current!r} -> {target_name!r} | "
                             f"read {t_read - t0:.2f}s, apply {t_end - t_read:.2f}s, "
                             f"total {t_end - t0:.2f}s | results={results}")
                if not ok:
                    log.error(f"DDCInput: failed to apply state {target_name!r}: {results}")
        finally:
            self._busy = False
            if self._spin_thread is not None:
                self._spin_thread.join(timeout=0.5)
            if applied is None:
                GLib.idle_add(self._clear_image)
            elif ok:
                self._show(applied)
            else:
                self._last_shown = None
                GLib.idle_add(self._render_error, applied)

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
            return False
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

    # ---------- label ----------
    def _cfg(self) -> dict:
        return config.load(self.plugin_base)

    def _show(self, name: str):
        self._last_shown = name
        GLib.idle_add(self._render_label, name or UNKNOWN)

    def _render_label(self, text):
        if self._busy:
            return False  # a transition is in progress; don't paint over the spinner
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

    # ---------- per-key config ----------
    def get_config_rows(self):
        s = self.get_settings()

        info = Adw.ActionRow(title="Monitors & states")
        info.set_subtitle("Configured in the plugin settings (gear icon on the DDC Monitor Input plugin).")

        self.poll_row = Adw.SpinRow.new_with_range(0, 60, 1)
        self.poll_row.set_title("Poll interval (s)")
        self.poll_row.set_subtitle("Re-check the monitors to catch external switches (0 = off)")
        self.poll_row.set_value(int(s.get("poll", DEFAULT_POLL) or 0))
        self.poll_row.connect("changed", self._on_poll_changed)

        return [info, self.poll_row]

    def _on_poll_changed(self, *_):
        s = self.get_settings()
        s["poll"] = int(self.poll_row.get_value())
        self.set_settings(s)
