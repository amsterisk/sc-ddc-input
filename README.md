# DDC Monitor Input

A [StreamController](https://github.com/StreamController/StreamController) plugin that switches monitor
inputs from a Stream Deck key using [`ddcutil`](https://www.ddcutil.com/) (VCP feature `0x60`).

It models your setup as **monitors** and **states** (scenes). A *state* maps some or all of your monitors
to specific inputs — e.g. "Work" might put your main display on USB-C and a second display on HDMI-1. A
single key cycles through your states, applying the input switches across every monitor at once.

## Action

**Cycle Monitor State** (key action):
- The label shows the state that currently matches reality, or **Unknown** if none match.
- A press applies the next state in the cycle (or the first state if currently Unknown).
- While switching it shows a spinner and ignores further presses; in the background it polls so the label
  stays correct if inputs change by other means (OSD, another machine).

## Requirements

- `ddcutil` installed on the host.
- Your user in the `i2c` group so `ddcutil` can reach `/dev/i2c-*`:
  ```sh
  sudo usermod -aG i2c "$USER"
  ```
  Because StreamController runs as a Flatpak and reaches `ddcutil` via `flatpak-spawn --host`, the host
  command inherits your **login session's** groups. Adding the group is not enough on its own — fully
  **log out and back in, or reboot**, so a fresh `systemd --user` manager picks up the new group. (A
  graphical relogin alone may not restart that manager.)

## Install (development)

Symlink the plugin into StreamController's Flatpak plugin directory, then restart the app:

```sh
ln -sfn "$PWD" ~/.var/app/com.core447.StreamController/data/plugins/com_amsterisk_DDCInput
flatpak kill com.core447.StreamController; flatpak run com.core447.StreamController
```

## Configuration

Monitors and states are **plugin-global**: open **Settings → Plugins → DDC Monitor Input → Open
Settings**. Edits save live (no restart needed).

**Monitors** — add each monitor you want to control:
- **Name** — a friendly label (shown in the state dropdowns).
- **Serial** — the monitor's EDID serial (stable across reboots). Leave blank to use ddcutil's default
  display.
- **Inputs** — the input sources, as `hex=Name` pairs, e.g. `1b=USB-C,0f=DP-1,11=HDMI-1,12=HDMI-2`.
- **Detect connected** auto-adds the monitors ddcutil sees, pre-filling serials and inputs from each
  monitor's reported capabilities.

**States** (cycle order) — add each scene:
- **Name** — e.g. Home, Work, Games.
- A dropdown **per monitor** selects that monitor's input for the state (or `—` to leave it out).

Per-key and diagnostics:
- **Poll interval (s)** (on the key's action settings) — how often to re-check the monitors; `0` disables.
- **Debug timing logs** (plugin settings) — logs per-read/per-set/total DDC timings to the app log
  (`~/.var/app/com.core447.StreamController/data/logs/logs.log`).

Find serials and supported input codes manually with:

```sh
ddcutil detect
ddcutil --sn <SERIAL> capabilities   # look for "Feature: 60 (Input Source)"
```

## Behaviour notes

- **Matching**: a state matches when every monitor it lists that is currently present matches its wanted
  input. Monitors that can't be found are ignored (in both matching and applying). If several states
  match, the first in cycle order wins.
- **No-op**: applying a state only issues `setvcp` for monitors that aren't already on the wanted input.
- **Parallel**: monitors are read and switched concurrently.
- Switching a monitor *away* from the machine running StreamController still works as long as that machine
  can reach the monitor over DDC; reads retry, since DDC is slightly flakier on a non-active input.

## License

MIT — see [LICENSE](LICENSE).
