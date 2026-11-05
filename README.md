# Aiacos Umbrel app store

A community app store for [umbrelOS](https://umbrel.com).

To add it: in the Umbrel App Store open the menu at the top right, choose **Community App Stores**
and paste `https://github.com/Aiacos/umbrel-app-store`.

## Argon ONE

Fan curve, power button and system information for the Argon ONE case on a Raspberry Pi 5, from a
page in the Umbrel web UI. umbrelOS resets its system partition at every boot, so Argon's normal
`argon1.sh` install disappears after a restart; this app keeps it working as a regular Umbrel app.

Not affiliated with Argon 40. The icon is loaded from Argon's own server, and the image contains none of Argon's code: at first start the app
downloads the official scripts from `download.argon40.com`, keeps them in the app's data folder
and only replaces them when you press **Install update**.

### What it can access

| Service | Network | Host access |
| --- | --- | --- |
| `web`: the page | Umbrel network, behind the Umbrel login | none, runs as user 1000 |
| `daemon`: fan and button | none | `/dev/i2c-1`, `/dev/gpiochip0`, the D-Bus system socket and `/usr/lib/systemd/system-shutdown` |

The daemon uses D-Bus to reboot or shut down when you press the button, and installs a small hook
in `system-shutdown` so the case cuts power after a shutdown. Access to the system bus as root is
effectively broad access to the host: install this app only if you accept that.

From a terminal, `sudo docker exec -it aiacos-argon-one_daemon_1 argon-config` opens Argon's own
menu. IR, UPS and BLSTR DAC are left out: they need packages and boot settings that umbrelOS does
not keep.
