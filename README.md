# DDC Monitor Input

A [StreamController](https://github.com/StreamController/StreamController) plugin that switches monitor
inputs from a Stream Deck key using [`ddcutil`](https://www.ddcutil.com/) (VCP feature `0x60`).

It models your setup as **monitors** and **states** (scenes). A *state* maps some or all of your monitors
to specific inputs — e.g. "Work" might put your main display on USB-C and a second display on HDMI-1.
Monitors and states are defined once (plugin-wide); each key then chooses which states it cycles and in
what order — or a single state, to act as a direct-select button.

## Action

**Cycle Monitor State** (key action). Each key draws a small **monitor icon** (top-left) and, when it
cycles more than one state, a **cycle icon** (top-right); the matched **state name** is shown below centre.

- Reads the monitors and shows the state matching reality, or **Unknown** if none match.
- A press applies the next state in the key's list (or the first if currently Unknown), switching every
  reachable monitor at once. A spinner shows during the switch and further presses are ignored until done.
- A key set to a **single** state is a direct-select button: it always shows that state's name and applies
  it on press.
- It polls in the background so the label stays correct when inputs change by other means (OSD, another
  machine). Multiple keys coordinate through the monitors' real state and share a lock, so they never drive
  the bus at the same time.

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

**Per key** (the key's action settings):
- **States to cycle (in order)** — comma-separated state names, e.g. `Work, Home`. The order is the cycle
  order. Leave **empty** to cycle all states (plugin order); enter a **single** name for a direct-select
  key. The *Available states* row lists the valid names.
- **Poll interval (s)** — how often to re-check the monitors; `0` disables (single-state keys never poll).

**Diagnostics:**
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
  match, the first in cycle order wins. A switch is only treated as "achieved" once every reachable
  monitor actually switched — otherwise the key shows the real state with an error indicator.
- **No-op**: applying a state only issues `setvcp` for monitors not already on the wanted input.
- **Parallel & fast**: monitors are targeted by i2c bus (resolved from serial, then cached) and read /
  switched concurrently, so a multi-monitor switch typically completes in well under a second.
- **Coordination**: all keys share one DDC lock, so concurrent presses queue rather than collide on the
  bus, and polling pauses during a switch.
- **Resilience**: a transient failed read keeps the last label instead of flashing "Unknown"; reads retry,
  since DDC is slightly flakier while a monitor shows a non-active input. Switching a monitor *away* from
  the machine running StreamController still works as long as that machine can reach it over DDC.

## License

MIT — see [LICENSE](LICENSE).
