"""Live view of a running PrintWatch watch loop, without touching the camera (watch.py owns it).

One ssh session (one password prompt) runs a loop on the board that sends each new review frame from the newest
run's frames/ folder (640 px, one per check, the second-newest so it's never half-written) as a 10-digit length plus the JPEG bytes. This script writes the
newest one to ../snapshots/live.jpg and keeps ../snapshots/live.html refreshing it every second; open that page
in a browser. No web server on the board (the live view that froze the board used one, see TODO.md).

  python watch_view.py        (or watch_view.cmd); Ctrl-C stops it
"""
import os
import subprocess
import sys

PRINTER = os.environ.get("PRINTWATCH_HOST") or sys.exit("Set PRINTWATCH_HOST to your printer's address first")
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "snapshots")
BOARD_LOOP = (
    "cd /home/root/printwatch/runs && R=$(ls | sort | tail -n 1) && cd $R/frames && last= ; "
    # second-newest frame: the newest may still be being written (one check, about 2 s, behind)
    "while true; do f=$(ls | tail -n 2 | head -n 1); "
    "if [ -n \"$f\" ] && [ \"$f\" != \"$last\" ]; then printf '%010d' $(wc -c < $f); cat $f; last=$f; fi; "
    "sleep 1; done"
)
PAGE = """<!doctype html><meta charset="utf-8"><title>PrintWatch live</title>
<style>body{margin:0;background:#111;color:#ccc;font:14px sans-serif;text-align:center}
img{max-width:100%;max-height:92vh}</style>
<img id="v" src="live.jpg"><div id="t"></div>
<script>setInterval(()=>{const s=Date.now();document.getElementById('v').src='live.jpg?'+s;
document.getElementById('t').textContent='refreshed '+new Date().toLocaleTimeString()},1000)</script>
"""


def read_exact(stream, n):
    buf = b""
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def main():
    os.makedirs(OUT, exist_ok=True)
    page = os.path.join(OUT, "live.html")
    with open(page, "w", encoding="utf-8") as f:
        f.write(PAGE)
    print(f"Open {os.path.abspath(page)} in a browser. Ctrl-C to stop.", flush=True)
    ssh = subprocess.Popen(["ssh", "-p", "2222", f"root@{PRINTER}", BOARD_LOOP], stdout=subprocess.PIPE)
    n = 0
    try:
        while True:
            head = read_exact(ssh.stdout, 10)
            if head is None:
                break
            data = read_exact(ssh.stdout, int(head))
            if data is None:
                break
            tmp = os.path.join(OUT, "live.tmp")
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, os.path.join(OUT, "live.jpg"))
            n += 1
            print(f"\rframes received: {n}", end="", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        ssh.terminate()
    print("\nStopped.")
    sys.exit(0)


if __name__ == "__main__":
    main()
