"""Live video from the toolhead camera in a window on this PC (ffplay). Asks for the CoralBoard password once.

The board script (board/stream_frames.py) writes the newest camera frames back to back (raw MJPEG) to ssh's
stdout, and ffplay plays them. It streams watch.py's frames if watch.py is running, or opens the camera itself.
Nothing listens on the board.

Why Python and not a plain cmd pipe: Windows' ssh doesn't exit when ffplay closes, so the board kept the camera
open. Here ssh is ended as soon as the window closes, and the board script then cleans up.

  python watch_video.py [--rotate] [--fps 15]     (or watch_video.cmd [rotate]); close the window or press q to stop
"""
import argparse
import os
import subprocess
import sys

PRINTER = os.environ.get("PRINTWATCH_HOST") or sys.exit("Set PRINTWATCH_HOST to your printer's address first")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rotate", action="store_true", help="turn 180 degrees (camera mounted upside down)")
    ap.add_argument("--fps", type=int, default=15)
    a = ap.parse_args()

    ssh = subprocess.Popen(["ssh", "-p", "2222", f"root@{PRINTER}",
                            f"cd /home/root/printwatch && python3 -u stream_frames.py --fps {a.fps}"],
                           stdout=subprocess.PIPE)
    play = ["ffplay", "-hide_banner", "-loglevel", "error", "-fflags", "nobuffer", "-flags", "low_delay",
            "-framedrop", "-f", "mjpeg", "-framerate", str(a.fps), "-window_title", "PrintWatcher toolhead camera"]
    if a.rotate:
        play += ["-vf", "hflip,vflip"]
    ffplay = subprocess.Popen(play + ["-i", "-"], stdin=ssh.stdout)
    ssh.stdout.close()  # ffplay owns the pipe now
    try:
        ffplay.wait()
    except KeyboardInterrupt:
        ffplay.terminate()
    finally:
        if ssh.poll() is None:
            ssh.terminate()  # closes the channel: the board script gets a broken pipe and stops its camera
        ssh.wait()
    print("Stopped.")
    sys.exit(0)


if __name__ == "__main__":
    main()
