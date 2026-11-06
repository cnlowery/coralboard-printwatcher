# CoralBoard setup and gotchas

## Connecting the board to the printer

The board plugs into the printer's USB port and shows up there as a network interface (NCM gadget, `usb0`).

[`printer/printer-coral-link.sh`](../printer/printer-coral-link.sh) sets up the printer side. Run it as root on the Klipper host: `install`, `uninstall` or `status`. It was written for an Elegoo Neptune 4 Pro (Armbian Buster, dhcpcd).

| What | Setting |
|---|---|
| Printer's address on `usb0` | `192.168.137.1/24` (static) |
| Board's address | `192.168.137.2` (pinned on the board in `/etc/ncm-gadget-setup.machine`) |
| Board's internet | through the printer: IPv4 forwarding and NAT out of `eth0` |
| Port forwards | `2222` → board ssh; `8080` → board |
| After reboot | a systemd unit restores the rules |

So `ssh -p 2222 root@<printer>` logs in to the **board**.

**Port 8080 clash:** the printer's own webcam streamer (mjpg-streamer) already uses 8080, so the forward hijacks LAN requests to it. Fluidd's `/webcam/` is unaffected, because nginx reaches the streamer locally. Use 8081 if you add a web page on the board.

## Board shell (BusyBox)

The board has no `base64`, `ldd`, `pkill` or `timeout`. Work around them like this:
- **Stop background jobs:** `kill $!` or `kill $(pidof gst-launch-1.0)`.
- **Encode binary data:** `python3 -m base64 file`.
- **Send binary files:** the Windows tools move files as tar over ssh's stdin and stdout. Use `.cmd` scripts, because PowerShell 5.1 pipes corrupt binary data.
- **Tar format:** write ustar (`tar --format ustar`). BusyBox tar can't read the pax headers that Windows tar writes by default.
- **Running a remote script:** pass it as an ssh **argument**, not on stdin. When ssh still needs a password, it reads stdin as the password.

## Disk space

The root partition comes almost full: about 25 MB free after the examples are installed. Keep runs and models under `/home` (about 48 GB free), and keep temporary files in `/dev/shm` (RAM). `watch.py` writes its camera ring to `/dev/shm/pw_watch`.

## `import cv2` fails: `undefined symbol: eglQueryString`

The board's EGL/GLES libraries are wrappers that load `libmali.so.0`. The full error is `libQt6Gui.so.6: undefined symbol: eglQueryString`. It means `/usr/lib/libmali.so.0` points at the OpenCL-only library (`libmali_opencl.so.0.49.1`), which has no EGL. In our case something changed it; we never found what.

The factory link is:

```sh
cd /usr/lib && ln -sf libmali.so.0.49.1 libmali.so.0
```

We checked it against the SD card image before changing it.

## Model loading

Load the `.vmfb` once and keep it loaded: loading takes 0.2–0.5 s, while each inference takes about 14 ms. When the runtime exits, it prints `[torq] Skipping binding ranges in XRAM footprint for dmabuf mode`, which so far has been harmless.
