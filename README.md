# DDC Monitor Input

A [StreamController](https://github.com/StreamController/StreamController) plugin that cycles a monitor's
input source from a Stream Deck key using [`ddcutil`](https://www.ddcutil.com/) (VCP feature `0x60`).

On press it reads the monitor's current input, switches to the next one in a configurable cycle, and
updates the key label. While switching it shows a spinner and ignores further presses; in the background
it polls the monitor so the label stays correct even if you switch inputs by other means (the monitor's
OSD or another machine).

## Action

**Cycle Monitor Input** (key action) — press to advance to the next input in the cycle.

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

Open the action's settings to set:

| Field | Meaning | Default |
|-------|---------|---------|
| **Monitor serial number** | Targets the monitor by EDID serial (`ddcutil --sn`), stable across reboots. | `8KKZ0S3` |
| **Input cycle** | Comma-separated VCP `0x60` values to cycle through, in order. | `1b,11` |
| **Labels** | `hex=Name` pairs shown on the key per input. | `1b=Work,11=Home,0f=DP-1,12=HDMI-2` |
| **Poll interval (s)** | How often to re-read the monitor to catch external switches. `0` disables. | `5` |

Find your monitor's serial and the input codes it supports with:

```sh
ddcutil detect
ddcutil --sn <SERIAL> capabilities   # look for "Feature: 60 (Input Source)"
```

The defaults target a Dell U4924DW cycling USB-C (`1b`, "Work") ⇄ HDMI-1 (`11`, "Home"). Adjust the
serial, cycle, and labels for your own monitor.

## Notes

- Switching the monitor *away* from the machine running StreamController still works as long as that
  machine can reach the monitor over DDC on its connected input (verified on the U4924DW). DDC reads are
  slightly flakier while the monitor is showing another input, so reads retry.
- Polling issues a `getvcp` every *N* seconds; raise the interval or set it to `0` if you'd rather it
  only update on press.

## License

MIT — see [LICENSE](LICENSE).
