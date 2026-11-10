"""PrintWatcher service: runs on the CoralBoard at boot (printwatcher.service) and watches every print.

- Polls Moonraker (on the printer, over the USB link) every few seconds.
  printing      camera on, a check every --interval seconds
  paused        camera stays on (for the live view), no checks
  anything else camera off, so tools/watch_video.cmd can open it on its own
- Spaghetti >= --conf on --streak checks in a row sends ONE ntfy alert with the annotated frame attached and a
  Pause button (the phone calls Moonraker's pause directly; works on the home network). No automatic pause.
  It re-arms when the print is resumed, a new print starts, or after --cooldown minutes.
- "Not watching" alert, once per problem, while the printer is printing: no camera, frames stopped, or this
  script crashing. A "watching again" message follows if it recovers.
- No record keeping: nothing is written per check. Only the alert frames are kept, in alerts/ (last 20), and
  runs/ folders left by manual watch.py runs are deleted after 3 days.
- Logs go to the journal: journalctl -u printwatcher -f

Settings come from the environment (set in printwatcher.service):
  PRINTWATCH_MOONRAKER   Moonraker as the board sees it      default http://192.168.137.1 (the printer end of the USB link,
                         through nginx: Moonraker often listens on localhost only)
  PRINTWATCH_PRINTER_URL the printer as the PHONE sees it    (for the Pause and Fluidd buttons) e.g. http://192.168.1.50
  PRINTWATCH_NTFY        ntfy topic URL                       e.g. https://ntfy.sh/your-secret-topic

  python3 printwatcher_service.py [--interval 2] [--conf 0.25] [--streak 3] [--cooldown 15]
  python3 printwatcher_service.py --test-alert     send one alert with a current camera frame, then exit
"""
import argparse
import glob
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import traceback
import urllib.request
from types import SimpleNamespace

import cv2

import failure_detect as fd
from watch import RING, SPAGHETTI, find_usb_camera, newest_frame, start_camera

MOONRAKER = os.environ.get("PRINTWATCH_MOONRAKER", "http://192.168.137.1")
PRINTER_URL = os.environ.get("PRINTWATCH_PRINTER_URL", "")
NTFY = os.environ.get("PRINTWATCH_NTFY", "")
ALERT_DIR = os.path.join(fd.HERE, "alerts")
RUNS_DIR = os.path.join(fd.HERE, "runs")
KEEP_ALERTS = 20
STALL_S = 60  # no new frame for this long while printing -> "not watching"


def log(msg):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def ascii_header(s):
    return s.encode("ascii", "replace").decode("ascii").replace("\n", " ")


def ntfy(title, message, image=None, priority="default", tags="", actions=""):
    """Post to ntfy. With an image, the JPEG is the body (an attachment) and the text goes in the Message header."""
    if not NTFY:
        log(f"(no PRINTWATCH_NTFY set) would send: {title}: {message}")
        return
    headers = {"Title": ascii_header(title), "Priority": priority}
    if tags:
        headers["Tags"] = tags
    if actions:
        headers["Actions"] = ascii_header(actions)
    if image is not None:
        headers["Filename"] = "printwatcher.jpg"
        headers["Message"] = ascii_header(message.replace("\n", " | "))
        data, method = image, "PUT"
    else:
        data, method = message.encode("utf-8"), "POST"
    try:
        req = urllib.request.Request(NTFY, data=data, headers=headers, method=method)
        urllib.request.urlopen(req, timeout=20).read()
        log(f"ntfy sent: {title}")
    except Exception as e:
        log(f"ntfy failed ({e}): {title}")


def buttons():
    if not PRINTER_URL:
        return ""
    return (f"http, Pause print, {PRINTER_URL}/printer/print/pause, method=POST, clear=true; "
            f"view, Open Fluidd, {PRINTER_URL}/")


def print_status():
    """(state, file name, minutes printed) or (None, '', 0) if Moonraker doesn't answer."""
    try:
        with urllib.request.urlopen(f"{MOONRAKER}/printer/objects/query?print_stats", timeout=5) as r:
            ps = json.load(r)["result"]["status"]["print_stats"]
        return ps.get("state"), ps.get("filename", "").rsplit("/", 1)[-1], ps.get("print_duration", 0) / 60
    except Exception:
        return None, "", 0


def cleanup():
    os.makedirs(ALERT_DIR, exist_ok=True)
    for old in sorted(glob.glob(os.path.join(ALERT_DIR, "*.jpg")))[:-KEEP_ALERTS]:
        os.remove(old)
    for d in glob.glob(os.path.join(RUNS_DIR, "*")):
        if time.time() - os.path.getmtime(d) > 3 * 86400:
            shutil.rmtree(d, ignore_errors=True)


class Camera:
    def __init__(self, size):
        self.a = SimpleNamespace(camera="usb", size=size, stream="2", device=None)
        self.proc = None
        self.last_frame = None
        self.last_new = 0.0   # last new frame, or the last (re)start: drives the 20 s restart
        self.last_good = 0.0  # last new frame only, never reset by a restart: drives "not watching"

    def running(self):
        return self.proc is not None

    def start(self, restart=False):
        """True if started. Stops any other gst-launch first (e.g. the live view's own), as watch.py does."""
        self.a.device = find_usb_camera()
        if self.a.device is None:
            return False
        subprocess.run("kill $(pidof gst-launch-1.0) 2>/dev/null", shell=True)
        self.proc = start_camera(self.a)
        self.last_frame, self.last_new = None, time.time()
        if not restart:
            self.last_good = time.time()
        log(f"camera {self.a.device} {self.a.size} on")
        return True

    def stop(self):
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None
            shutil.rmtree(RING, ignore_errors=True)
            log("camera off")

    def restart_if_dead(self):
        if self.proc is not None and (self.proc.poll() is not None or time.time() - self.last_new > 20):
            log("camera stopped or stalled, restarting")
            self.proc.kill()
            self.proc.wait()
            self.proc = None
            self.start(restart=True)

    def frame(self):
        """Newest complete frame not seen before, or None."""
        path = newest_frame()
        if path is None or path == self.last_frame:
            return None
        img = cv2.imread(path)
        if img is None:
            return None
        self.last_frame, self.last_new = path, time.time()
        self.last_good = self.last_new
        return img


def detect(runner, q, frame, conf):
    blob, pad = fd.preprocess(frame, q)
    dets = fd.postprocess(runner.infer(blob), q, frame.shape[:2], pad, conf)
    spag = max([s for c, s, _ in dets if c == SPAGHETTI], default=0.0)
    return spag, dets


def annotate(frame, dets):
    ann = frame.copy()
    for c, s, b in dets:
        x1, y1, x2, y2 = b.astype(int)
        cv2.rectangle(ann, (x1, y1), (x2, y2), fd.COLOURS[c], 3)
        cv2.putText(ann, f"{fd.CLASSES[c]} {s:.2f}", (x1, max(18, y1 - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, fd.COLOURS[c], 2)
    return ann


def send_alert(frame, dets, spag, n_in_row, name, minutes, test=False):
    ann = annotate(frame, dets)
    os.makedirs(ALERT_DIR, exist_ok=True)
    path = os.path.join(ALERT_DIR, time.strftime("%Y%m%d_%H%M%S") + ".jpg")
    cv2.imwrite(path, ann, [cv2.IMWRITE_JPEG_QUALITY, 85])
    ok, jpg = cv2.imencode(".jpg", ann, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if test:
        title = "PrintWatcher: TEST alert"
        msg = f"Test only, nothing is wrong. Current camera frame (spaghetti score {spag:.2f})."
    else:
        title = "PrintWatcher: possible spaghetti"
        msg = (f"Spaghetti score {spag:.2f}, {n_in_row} checks in a row. {name} at {minutes:.0f} min. "
               f"Check the picture and tap Pause if it's failing.")
    ntfy(title, msg, image=jpg.tobytes() if ok else None, priority="high", tags="warning", actions=buttons())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--streak", type=int, default=3)
    ap.add_argument("--cooldown", type=float, default=15, help="minutes before alerting again in the same print")
    ap.add_argument("--size", default="1280x720")
    ap.add_argument("--poll", type=float, default=5.0, help="seconds between Moonraker checks")
    ap.add_argument("--test-alert", action="store_true")
    a = ap.parse_args()

    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    cleanup()
    runner, q = fd.load(os.path.join(fd.HERE, "printwatch_failures_320_int8.vmfb"))
    cam = Camera(a.size)

    if a.test_alert:
        if not cam.start():
            sys.exit("No USB camera found")
        t_end = time.time() + 10
        frame = None
        while frame is None and time.time() < t_end:
            time.sleep(0.3)
            frame = cam.frame()
        cam.stop()
        if frame is None:
            sys.exit("No frame from the camera in 10 s")
        spag, dets = detect(runner, q, frame, a.conf)
        send_alert(frame, dets, spag, 0, "", 0, test=True)
        return

    log(f"PrintWatcher service: Moonraker {MOONRAKER}, ntfy {NTFY or '(not set)'}, check every {a.interval:g} s, "
        f"conf {a.conf}, alert after {a.streak} in a row, cooldown {a.cooldown:g} min")
    state, prev_state = None, None
    name, minutes = "", 0
    next_poll = 0.0
    streak, alerted_at = 0, None
    problem = None  # current "not watching" reason, already notified
    moonraker_down_since = None
    try:
        while not stop:
            now = time.time()
            if now >= next_poll:
                next_poll = now + a.poll
                s, nm, mins = print_status()
                if s is None:
                    if moonraker_down_since is None:
                        moonraker_down_since = now
                        log("Moonraker not answering")
                    s = state  # keep doing what we were doing for now
                else:
                    if moonraker_down_since is not None:
                        log("Moonraker back")
                    moonraker_down_since = None
                    name, minutes = nm, mins
                prev_state, state = state, s
                if state != prev_state:
                    log(f"printer state: {prev_state} -> {state} {name}")
                    cam.last_good = cam.last_new = now  # frames weren't being read while paused
                    if state == "printing" and prev_state != "paused":  # a new print
                        streak, alerted_at, problem = 0, None, None
                    elif state == "printing" and prev_state == "paused":  # resumed: re-arm
                        streak, alerted_at = 0, None
                if state in ("printing", "paused"):
                    if not cam.running() and not cam.start() and state == "printing" and problem != "camera":
                        problem = "camera"
                        ntfy("PrintWatcher isn't watching", f"No USB camera found on the board. {name}",
                             tags="see_no_evil")
                else:
                    cam.stop()
                    problem = None
            if state != "printing" or not cam.running():
                time.sleep(0.5)
                continue
            cam.restart_if_dead()
            frame = cam.frame()
            if frame is None:
                if time.time() - cam.last_good > STALL_S and problem != "frames":
                    problem = "frames"
                    ntfy("PrintWatcher isn't watching", f"No camera frames for {STALL_S} s. {name}",
                         tags="see_no_evil")
                time.sleep(0.2)
                continue
            if problem:
                log(f"recovered from: {problem}")
                ntfy("PrintWatcher is watching again", f"Camera frames are back. {name}", priority="low",
                     tags="eyes")
                problem = None
            t_next = time.time() + a.interval
            spag, dets = detect(runner, q, frame, a.conf)
            streak = streak + 1 if spag >= a.conf else 0
            if spag >= a.conf:
                log(f"spaghetti {spag:.2f} streak {streak}")
            cooled = alerted_at is None or time.time() - alerted_at > a.cooldown * 60
            if streak >= a.streak and cooled:
                log(f"ALERT: spaghetti {spag:.2f}, {streak} in a row")
                send_alert(frame, [d for d in dets if d[1] >= a.conf], spag, streak, name, minutes)
                alerted_at = time.time()
                cleanup()
            time.sleep(max(0.0, t_next - time.time()))
    except Exception:
        log("crashed:\n" + traceback.format_exc())
        if state == "printing":
            ntfy("PrintWatcher isn't watching", "The watcher crashed; systemd restarts it in 10 s. "
                 "Details: journalctl -u printwatcher", tags="see_no_evil")
        raise
    finally:
        cam.stop()
        log("stopped")


if __name__ == "__main__":
    main()
