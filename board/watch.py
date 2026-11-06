"""PrintWatch watch loop, MONITOR ONLY: watches a camera and logs what the failure detector sees.
It never pauses the printer or sends notifications; it only prints "WOULD ALERT" where step 3 would act.

- A gst-launch child writes the camera into a ring of JPEGs in /dev/shm/pw_watch/:
  --camera usb (default): the toolhead UVC webcam (Innomaker, 2026-11-05), MJPEG at --size straight from the
  camera, so nothing is decoded or encoded on the board (about 30 small files a second into RAM).
  --camera tapo: decodes the Tapo's RTSP stream (stream2 = 1280x720 on the C100, measured 2026-10-31) at 1 fps.
  The detector only looks at 320 px either way.
- Every --interval seconds the model (loaded once) runs on the newest complete frame.
- Each run gets a folder runs/<start time>/ on the SD card: log.csv (one row per check), every checked frame
  (--keep all, the default, so misses can be reviewed afterwards; saved at 640 px wide, about 40 KB), and a
  full-size annotated copy of every frame with a detection >= --conf.
- "Streak" counts consecutive checks with spaghetti >= --conf; reaching --streak prints WOULD ALERT.

Run on the board (tools/watch.cmd does this over ssh): Ctrl-C stops it and the camera child.
  /home/root/sl2610-examples/.venv/bin/python3 watch.py [--camera usb|tapo] [--interval 2] [--conf 0.25]
      [--streak 3] [--stream 2] [--size 1280x720] [--rotate 0|180]
"""
import argparse
import csv
import glob
import os
import shutil
import signal
import subprocess
import sys
import time

import cv2

import failure_detect as fd

CAM_FILE = "/home/root/.printwatch/camera_url"
RING = "/dev/shm/pw_watch"
SPAGHETTI = fd.CLASSES.index("spaghetti")
LOG_CONF = 0.05  # log class maxima down to here (one int8 step is about 0.007) so weak signals show in the CSV


def find_usb_camera():
    """First /dev/video* driven by uvcvideo that offers MJPEG (the Pi camera's ISP nodes moved when it was added)."""
    for dev in sorted(glob.glob("/dev/video*")):
        info = subprocess.run(["v4l2-ctl", "-d", dev, "--info"], capture_output=True, text=True).stdout
        fmts = subprocess.run(["v4l2-ctl", "-d", dev, "--list-formats"], capture_output=True, text=True).stdout
        if "uvcvideo" in info and "MJPG" in fmts:
            return dev
    return None


def start_camera(a):
    shutil.rmtree(RING, ignore_errors=True)
    os.makedirs(RING)
    if a.camera == "usb":
        w, h = a.size.split("x")
        src = ["v4l2src", f"device={a.device}", "!", f"image/jpeg,width={w},height={h}"]
    else:
        base = open(CAM_FILE).read().strip()
        src = ["rtspsrc", f"location={base}/stream{a.stream}", "protocols=tcp",
               "!", "rtph264depay", "!", "h264parse", "!", "avdec_h264",
               "!", "videorate", "drop-only=true", "max-rate=1", "!", "videoconvert", "!", "jpegenc"]
    cmd = ["gst-launch-1.0", "-q"] + src + ["!", "multifilesink", f"location={RING}/f%06d.jpg", "max-files=4"]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def newest_frame():
    """Second-newest file in the ring: the newest may still be being written."""
    files = sorted(glob.glob(f"{RING}/f*.jpg"))
    return files[-2] if len(files) >= 2 else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=2.0, help="seconds between checks")
    ap.add_argument("--conf", type=float, default=0.25, help="detection threshold for saving and the streak")
    ap.add_argument("--streak", type=int, default=3, help="consecutive spaghetti checks that would alert")
    ap.add_argument("--camera", choices=["usb", "tapo"], default="usb", help="toolhead USB webcam or the Tapo")
    ap.add_argument("--stream", default="2", help="Tapo stream: 1 = 1080p, 2 = 720p")
    ap.add_argument("--size", default="1280x720", help="USB camera MJPEG size (1920x1080, 1280x720, 640x480, ...)")
    ap.add_argument("--rotate", type=int, choices=[0, 180], default=0, help="turn each frame before checking it")
    ap.add_argument("--keep", choices=["all", "hits"], default="all", help="save every checked frame, or only hits")
    a = ap.parse_args()

    if a.camera == "usb":
        a.device = find_usb_camera()
        if a.device is None:
            sys.exit("No USB (UVC, MJPEG) camera found on the board")
    elif not os.path.isfile(CAM_FILE):
        sys.exit("No camera saved on the board: run tools/tapo_grab.ps1 -SetCamera first")
    # Stop any gst-launch left by an earlier run or grab (the board has no pkill).
    subprocess.run("kill $(pidof gst-launch-1.0) 2>/dev/null", shell=True)

    run_dir = os.path.join(fd.HERE, "runs", time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(os.path.join(run_dir, "frames"))
    os.makedirs(os.path.join(run_dir, "hits"))
    runner, q = fd.load(os.path.join(fd.HERE, "printwatch_failures_320_int8.vmfb"))
    cam = start_camera(a)
    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    signal.signal(signal.SIGHUP, lambda *_: stop.append(1))  # ssh closed
    source = f"USB {a.device} {a.size}" if a.camera == "usb" else f"Tapo stream{a.stream}"
    print(f"PrintWatch MONITOR ONLY: {source}, every {a.interval:g} s, conf {a.conf}, "
          f"alert after {a.streak} in a row. Logging to {run_dir}. Ctrl-C to stop.", flush=True)

    f = open(os.path.join(run_dir, "log.csv"), "w", newline="")
    log = csv.writer(f)
    log.writerow(["time", "frame", "frame_age_s", "infer_ms", "total_ms", "n_dets"]
                 + [f"max_{c.replace(' ', '_')}" for c in fd.CLASSES] + ["streak", "would_alert"])
    streak, n, last_frame, restarts = 0, 0, None, 0
    last_new = time.time()  # when a new frame last appeared (the ring deletes old files, so don't stat them later)
    try:
        while not stop:
            if cam.poll() is not None or time.time() - last_new > 20:
                restarts += 1
                print(f"{time.strftime('%H:%M:%S')} camera stopped or stalled, restarting (#{restarts})", flush=True)
                cam.kill()
                cam.wait()
                cam = start_camera(a)
                last_frame, last_new = None, time.time()
                continue
            path = newest_frame()
            if path is None or path == last_frame:
                time.sleep(0.2)
                continue
            try:
                age = time.time() - os.path.getmtime(path)
            except FileNotFoundError:  # rotated out between listing and reading
                continue
            frame = cv2.imread(path)
            if frame is None:
                continue
            if a.rotate == 180:
                frame = cv2.rotate(frame, cv2.ROTATE_180)
            t_next = time.time() + a.interval
            last_frame, last_new = path, time.time()
            t0 = time.perf_counter()
            blob, pad = fd.preprocess(frame, q)
            t1 = time.perf_counter()
            raw = runner.infer(blob)
            t2 = time.perf_counter()
            dets = fd.postprocess(raw, q, frame.shape[:2], pad, LOG_CONF)
            total = (time.perf_counter() - t0) * 1000
            maxima = [max([s for c, s, _ in dets if c == k], default=0.0) for k in range(len(fd.CLASSES))]
            hits = [d for d in dets if d[1] >= a.conf]
            streak = streak + 1 if maxima[SPAGHETTI] >= a.conf else 0
            alert = streak >= a.streak
            n += 1
            stamp = time.strftime("%H%M%S")
            name = f"{n:05d}_{stamp}.jpg"
            if a.keep == "all":  # review copy at 640 px wide: about 1/6 the size of a 720p frame
                small = cv2.resize(frame, (640, round(frame.shape[0] * 640 / frame.shape[1])), interpolation=cv2.INTER_AREA)
                cv2.imwrite(os.path.join(run_dir, "frames", name), small, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if hits:
                ann = frame.copy()
                for c, s, b in hits:
                    x1, y1, x2, y2 = b.astype(int)
                    cv2.rectangle(ann, (x1, y1), (x2, y2), fd.COLOURS[c], 2)
                    cv2.putText(ann, f"{fd.CLASSES[c]} {s:.2f}", (x1, max(14, y1 - 5)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, fd.COLOURS[c], 1)
                cv2.imwrite(os.path.join(run_dir, "hits", name), ann)
            log.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), name, f"{age:.1f}", f"{(t2 - t1) * 1000:.1f}",
                          f"{total:.0f}", len(hits)] + [f"{m:.3f}" for m in maxima] + [streak, int(alert)])
            f.flush()
            top = ", ".join(f"{fd.CLASSES[c]} {s:.2f}" for c, s, _ in hits[:3]) or "-"
            print(f"{time.strftime('%H:%M:%S')} #{n} spaghetti {maxima[SPAGHETTI]:.2f} streak {streak}"
                  f"{'  WOULD ALERT' if alert else ''} | {top} | {total:.0f} ms, frame {age:.1f} s old", flush=True)
            time.sleep(max(0.0, t_next - time.time()))
    except KeyboardInterrupt:
        pass
    finally:
        cam.terminate()
        try:
            cam.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cam.kill()
        f.close()
        shutil.rmtree(RING, ignore_errors=True)
        print(f"Stopped after {n} checks. Run saved in {run_dir}", flush=True)


if __name__ == "__main__":
    main()
