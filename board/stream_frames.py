"""Live video from the toolhead USB camera: writes the newest camera frames to stdout back to back (a raw MJPEG
stream) for tools/watch_video.cmd, which pipes it over ssh into ffplay on the PC. No server on the board.

- If watch.py is running, its frame ring (/dev/shm/pw_watch, about 30 fps) is used, so the detector isn't touched.
- If not, this starts its own camera ring in /dev/shm/pw_view (same MJPEG pipeline as watch.py) and stops it on exit.
  When watch.py starts later it kills every gst-launch, including ours, and the stream switches to its ring.
- Plain python3 (no OpenCV), so it doesn't need the examples' venv.

  python3 -u stream_frames.py [--fps 15] [--size 1280x720]
"""
import argparse
import glob
import os
import shutil
import signal
import subprocess
import sys
import time

WATCH_RING = "/dev/shm/pw_watch"
OWN_RING = "/dev/shm/pw_view"


def find_usb_camera():
    """First /dev/video* driven by uvcvideo that offers MJPEG (same test as watch.py)."""
    for dev in sorted(glob.glob("/dev/video*")):
        info = subprocess.run(["v4l2-ctl", "-d", dev, "--info"], capture_output=True, text=True).stdout
        fmts = subprocess.run(["v4l2-ctl", "-d", dev, "--list-formats"], capture_output=True, text=True).stdout
        if "uvcvideo" in info and "MJPG" in fmts:
            return dev
    return None


def ring_files(ring):
    return sorted(glob.glob(f"{ring}/f*.jpg"))


def fresh(ring, max_age=2.0):
    """True if the ring's newest frame was written within max_age seconds."""
    files = ring_files(ring)
    try:
        return bool(files) and time.time() - os.path.getmtime(files[-1]) < max_age
    except FileNotFoundError:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fps", type=float, default=15.0)
    ap.add_argument("--size", default="1280x720", help="camera size when this script opens the camera itself")
    a = ap.parse_args()

    own = None  # our gst child, when watch.py isn't running

    def stop_own():
        nonlocal own
        if own is not None:
            if own.poll() is None:
                own.terminate()
                try:
                    own.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    own.kill()
            own = None
        shutil.rmtree(OWN_RING, ignore_errors=True)

    def start_own():
        nonlocal own
        dev = find_usb_camera()
        if dev is None:
            sys.stderr.write("No USB (UVC, MJPEG) camera found on the board\n")
            return
        w, h = a.size.split("x")
        shutil.rmtree(OWN_RING, ignore_errors=True)
        os.makedirs(OWN_RING)
        own = subprocess.Popen(["gst-launch-1.0", "-q", "v4l2src", f"device={dev}", "!", f"image/jpeg,width={w},height={h}",
                                "!", "multifilesink", f"location={OWN_RING}/f%06d.jpg", "max-files=4"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        sys.stderr.write(f"camera {dev} {a.size} opened for the stream (watch.py isn't running)\n")

    def bye(*_):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, bye)
    signal.signal(signal.SIGHUP, bye)  # ssh closed
    out = sys.stdout.buffer
    period = 1.0 / a.fps
    last, ring, next_check = None, None, 0.0
    try:
        while True:
            now = time.time()
            if now >= next_check:  # pick the source once a second
                next_check = now + 1.0
                if fresh(WATCH_RING):
                    if ring != WATCH_RING:
                        sys.stderr.write("streaming watch.py's frames\n")
                    stop_own()
                    ring = WATCH_RING
                else:
                    if own is None or own.poll() is not None:
                        stop_own()
                        start_own()
                        time.sleep(1.0)  # let the camera start before looking for frames
                    ring = OWN_RING
            files = ring_files(ring) if ring else []
            path = files[-2] if len(files) >= 2 else None  # the newest may still be being written
            if path and path != last:
                try:
                    with open(path, "rb") as f:
                        data = f.read()
                except FileNotFoundError:  # rotated out between listing and reading
                    data = b""
                if data:
                    out.write(data)
                    out.flush()
                    last = path
            time.sleep(max(0.0, now + period - time.time()))
    except (BrokenPipeError, KeyboardInterrupt, SystemExit):
        pass
    finally:
        stop_own()
        try:
            sys.stdout = None  # don't complain about the closed pipe on exit
        except Exception:
            pass


if __name__ == "__main__":
    main()
