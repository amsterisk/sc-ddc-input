import glob
import math
import os
import threading
import time

from loguru import logger as log

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib

from PIL import Image, ImageDraw, ImageFont

from src.backend.PluginManager.ActionBase import ActionBase

from . import config, engine

DEFAULT_POLL = 5
UNKNOWN = "Unknown"

# Shared by every CycleState key instance so presses/polls across *all* keys
# serialize on the i2c bus (concurrent DDC from two keys would contend).
_DDC_LOCK = threading.Lock()

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


_FONT_PATH = None


def _font_path() -> str | None:
    global _FONT_PATH
    if _FONT_PATH is None:
        prefs = ["/usr/share/fonts/google-crosextra-carlito/Carlito-Bold.ttf",
                 "/usr/share/fonts/cantarell/Cantarell-Bold.ttf"]
        for p in prefs:
            if os.path.exists(p):
                _FONT_PATH = p
                break
        else:
            for pat in ("/usr/share/fonts/**/*Bold.ttf", "/usr/share/fonts/**/*.ttf"):
                found = sorted(glob.glob(pat, recursive=True))
                if found:
                    _FONT_PATH = found[0]
                    break
    return _FONT_PATH


def _font(px: int):
    try:
        p = _font_path()
        return ImageFont.truetype(p, px) if p else ImageFont.load_default()
    except Exception:
        return ImageFont.load_default()


_ICON_CY = 0.15        # shared vertical centre for both corner icons (fraction of size)
_ICON_MARGIN = 0.09    # horizontal inset from the edges (fraction of size)
_LABEL_MAX = 0.272     # max state-label font size (fraction of size); longer text shrinks to fit


def _draw_monitor(d, size, col, cy):
    """A small monitor glyph in the top-left corner, vertically centred on cy."""
    w = max(2, int(size * 0.022))
    sw, sh, st = size * 0.20, size * 0.135, size * 0.045  # screen + stand heights
    x0 = size * _ICON_MARGIN
    y0 = cy - (sh + st) / 2.0
    x1, y1 = x0 + sw, y0 + sh
    try:
        d.rounded_rectangle([x0, y0, x1, y1], radius=size * 0.02, outline=col, width=w)
    except Exception:
        d.rectangle([x0, y0, x1, y1], outline=col, width=w)
    cxm = (x0 + x1) / 2
    d.line([(cxm, y1), (cxm, y1 + st)], fill=col, width=w)                        # stand
    d.line([(cxm - sw * 0.28, y1 + st), (cxm + sw * 0.28, y1 + st)], fill=col, width=w)  # base


def _draw_cycle(d, size, col, cy):
    """A small circular-arrow glyph in the top-right corner, centred on cy."""
    r = size * 0.085
    cx = size - size * _ICON_MARGIN - r
    cy = cy + size * 0.014  # nudge down so the visual centre matches the monitor glyph
    w = max(2, int(size * 0.025))
    d.arc([cx - r, cy - r, cx + r, cy + r], 40, 300, fill=col, width=w)
    a = math.radians(300)
    px, py = cx + r * math.cos(a), cy + r * math.sin(a)
    tx, ty = -math.sin(a), math.cos(a)
    nx, ny = math.cos(a), math.sin(a)
    ah = size * 0.055
    d.polygon([(px + tx * ah, py + ty * ah),
               (px + nx * ah * 0.7, py + ny * ah * 0.7),
               (px - nx * ah * 0.7, py - ny * ah * 0.7)], fill=col)


def _state_image(text: str, cycle: bool, size: int = 144) -> Image.Image:
    """Full key face: monitor icon (top-left), cycle icon (top-right, multi-state),
    and the state name drawn large, just below centre."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    col = (255, 255, 255, 255)
    cy = size * _ICON_CY
    _draw_monitor(d, size, col, cy)
    if cycle:
        _draw_cycle(d, size, col, cy)
    if text:
        max_w = size - 2 * (size * 0.08)
        fs = int(size * _LABEL_MAX)
        while fs > 10:
            try:
                tb = d.textbbox((0, 0), text, font=_font(fs))
                tw = tb[2] - tb[0]
            except Exception:
                tw = fs * len(text) * 0.6
            if tw <= max_w:
                break
            fs -= 2
        d.text((size / 2, size * 0.60), text, font=_font(fs), fill=col, anchor="mm",
               stroke_width=max(1, int(size * 0.014)), stroke_fill=(0, 0, 0, 255))
    return img


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

    def _poll_secs(self) -> int:
        return int(self.get_settings().get("poll", DEFAULT_POLL) or 0)

    # ---------- lifecycle ----------
    def on_ready(self):
        threading.Thread(target=self._warmup, name="DDCWarmup", daemon=True).start()

    def _warmup(self, attempts: int = 10, delay: float = 1.5):
        # Single-state key shows its fixed name immediately; no read needed.
        if not self._is_cycle():
            self._show(self._label_for(None))
            return
        # Retry only while reads are failing; a successful read with no match is a
        # legitimate "Unknown", not a transient.
        for _ in range(attempts):
            if self._busy:
                return
            cfg = self._cfg()
            readings = engine.read_inputs(cfg["monitors"], quiet=True)
            if readings and any(v is not None for v in readings.values()):
                self._show(self._label_for(engine.match_state(cfg["states"], readings)))
                return
            time.sleep(delay)
        self._show(self._label_for(None))

    def on_tick(self):
        poll = self._poll_secs()
        if poll <= 0 or self._busy or self._polling or not self.get_is_present():
            return
        if not self._is_cycle():
            return  # single-state key shows a fixed name; nothing to poll
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
            if self._busy or not _DDC_LOCK.acquire(blocking=False):
                return
            cfg = self._cfg()
            try:
                # Cheap: single-try, quiet (no error spam) — polling shouldn't retry.
                readings = engine.read_inputs(cfg["monitors"], quiet=True, retries=0,
                                              debug=cfg.get("debug", False))
            finally:
                _DDC_LOCK.release()
            all_failed = bool(readings) and all(v is None for v in readings.values())
            self._poll_fail_streak = self._poll_fail_streak + 1 if all_failed else 0
            # A failed read isn't a real "Unknown" — keep the last label rather than
            # flicker to Unknown when the bus momentarily doesn't answer.
            if not self._busy and not all_failed:
                name = self._label_for(engine.match_state(cfg["states"], readings))
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
        achieved = False
        actual_name = None
        try:
            cfg = self._cfg()
            debug = cfg.get("debug", False)
            states = cfg["states"]
            if not states:
                log.warning("DDCInput: no states configured")
                return
            with _DDC_LOCK:
                t0 = time.monotonic()
                readings = engine.read_inputs(cfg["monitors"], debug=debug)
                t_read = time.monotonic()
                current = engine.match_state(states, readings)
                # Cycle within THIS key's ordered subset; reality is matched
                # against all plugin states so we know where we are.
                target_name = engine.next_state_name(self._cycle_states(states), current)
                target = engine.state_by_name(states, target_name)
                if target is None:
                    return
                applied = target_name
                results = engine.apply_state(target, current=readings, debug=debug)
                # Achieved only if every *reachable* target monitor switched (or was
                # already correct). Unreachable monitors are ignored; if none were
                # reachable, the state wasn't realised.
                present = [s for s in target.get("targets", {}) if readings.get(s) is not None]
                achieved = bool(present) and all(results.get(s) for s in present)
                if debug:
                    t_end = time.monotonic()
                    log.info(f"DDCInput[timing]: {current!r} -> {target_name!r} | "
                             f"read {t_read - t0:.2f}s, apply {t_end - t_read:.2f}s, "
                             f"total {t_end - t0:.2f}s | achieved={achieved} results={results}")
                if not achieved:
                    log.error(f"DDCInput: state {target_name!r} not fully applied: "
                              f"readings={readings} results={results}")
                    # Show reality, not the target we failed to reach.
                    actual = engine.read_inputs(cfg["monitors"], quiet=True, retries=0)
                    actual_name = self._label_for(engine.match_state(states, actual))
        finally:
            self._busy = False
            if self._spin_thread is not None:
                self._spin_thread.join(timeout=0.5)
            if applied is None:
                GLib.idle_add(self._clear_image)
            elif achieved:
                self._show(applied)
            else:
                self._last_shown = actual_name
                GLib.idle_add(self._render_error, actual_name or UNKNOWN)

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
            self.set_media(image=_state_image("", self._is_cycle()))
        except Exception:
            pass
        return False

    # ---------- per-key state set ----------
    def _cfg(self) -> dict:
        return config.load(self.plugin_base)

    def _key_state_names(self, plugin_states) -> list[str]:
        """This key's ordered cycle of state names; empty selection = all states."""
        valid = [s["name"] for s in plugin_states if s.get("name")]
        sel = [n for n in self.get_settings().get("state_names", []) if n in valid]
        return sel if sel else valid

    def _cycle_states(self, plugin_states) -> list[dict]:
        by_name = {s["name"]: s for s in plugin_states if s.get("name")}
        return [by_name[n] for n in self._key_state_names(plugin_states) if n in by_name]

    def _is_cycle(self) -> bool:
        return len(self._key_state_names(self._cfg()["states"])) > 1

    def _label_for(self, matched) -> str:
        # A single-state key is a label/direct-select: always show its own state
        # name. A cycle key reflects the matched reality (or Unknown).
        if not self._is_cycle():
            names = self._key_state_names(self._cfg()["states"])
            return names[0] if names else UNKNOWN
        return matched or UNKNOWN

    def _refresh_display(self):
        GLib.idle_add(self._render_label, self._label_for(self._last_shown))

    # ---------- label ----------

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
            self.set_center_label("", update=False)  # state name is drawn in the image
        except Exception:
            pass
        try:
            self.set_media(image=_state_image(text, self._is_cycle()), update=True)
        except Exception as e:
            log.error(f"DDCInput: render failed {text!r}: {e}")
        return False

    def _render_error(self, text):
        try:
            self.set_center_label("", update=False)
        except Exception:
            pass
        try:
            self.set_media(image=_state_image(text, self._is_cycle()), update=True)
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
        plugin_names = [st["name"] for st in self._cfg()["states"] if st.get("name")]

        self.states_row = Adw.EntryRow(title="States to cycle (in order)")
        self.states_row.set_text(", ".join(s.get("state_names", [])))
        self.states_row.connect("notify::text", self._on_states_text)

        avail = Adw.ActionRow(title="Available states")
        avail.set_subtitle(", ".join(plugin_names) if plugin_names
                           else "none yet — add them in the plugin settings")

        self.poll_row = Adw.SpinRow.new_with_range(0, 60, 1)
        self.poll_row.set_title("Poll interval (s)")
        self.poll_row.set_subtitle("Re-check the monitors to catch external switches (0 = off)")
        self.poll_row.set_value(int(s.get("poll", DEFAULT_POLL) or 0))
        self.poll_row.connect("changed", self._on_poll_changed)

        return [self.states_row, avail, self.poll_row]

    def _on_states_text(self, entry, _pspec):
        names = [n.strip() for n in entry.get_text().split(",") if n.strip()]
        s = self.get_settings()
        s["state_names"] = names
        self.set_settings(s)
        self._refresh_display()  # badge reflects cycle (>1) vs single

    def _on_poll_changed(self, *_):
        s = self.get_settings()
        s["poll"] = int(self.poll_row.get_value())
        self.set_settings(s)
