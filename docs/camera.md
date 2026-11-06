# Camera

## Toolhead camera: Innomaker U20CAM-1080p (USB, UVC)

| What | Value |
|---|---|
| USB ID | `0c45:6366` |
| Formats | MJPG 640x480 to 1920x1080, YUYV |
| Our setting | MJPG 1280x720: the camera encodes, the board only saves files |
| Device | `watch.py` finds it on its own: the first `/dev/video*` that uses the `uvcvideo` driver and offers MJPG. Don't hard-code `/dev/video0`; the numbers change when other cameras are present. |
| Cable | USB, run along the toolhead cable bundle. A ribbon cable would fail from the constant flexing. |

**Focus:** the lens thread is 10 mm long. About **3 mm of thread showing** is sharp at 50 mm, and depth of field is generous. Fine-tune on the toolhead by grabbing frames until the nozzle tip is sharp.

```bat
tools\webcam_grab.cmd
```

**Orientation:** in our mount the image comes out upside down. Use `watch.py --rotate 180`, or turn the camera in the mount.

**Framing:** the nozzle sits top-centre. While printing, the bed fills the rest of the frame.

## Grabbing a frame

- **Board on the printer:** run `tools\webcam_grab.cmd` (ssh). It saves `snapshots\webcam_view.jpg`.
- **Board on the PC's USB:** run `tools\webcam_grab_usb.cmd` (adb, no password).
- **Directly on the board:**
  ```sh
  gst-launch-1.0 -q v4l2src device=/dev/videoN num-buffers=45 ! image/jpeg,width=1920,height=1080 ! multifilesink location=/dev/shm/cam.jpg
  ```
  The 45 frames give exposure time to settle.

## Network camera (Tapo C100, RTSP): works, but not recommended

`watch.py --camera tapo` decodes the RTSP H.264 stream in software (`avdec_h264`). The board's GStreamer has no hardware H.264 decoder.

Run `tools\tapo_grab.ps1 -SetCamera` once. It saves the stream URL on the board (`/home/root/.printwatch/camera_url`, mode 600). The camera account in it is percent-encoded, not encrypted.

From a frame-mounted camera, an early failure is too small to detect: see the README's results.
