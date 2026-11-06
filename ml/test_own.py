"""Score models on the held-out real failure (run 20261031_204131) the way watch.py would: per frame max
spaghetti score, frames >= --conf, and when a --streak of consecutive hits would first alert.
Float .pt models on the PC, so this is an upper bound for the int8 NPU model (check that after converting).

  python test_own.py --test ~/printwatch/data/own/test/images MODEL.pt [MODEL2.pt ...]
"""
import argparse
from pathlib import Path

from ultralytics import YOLO

FAIL_FROM = "204548"  # the part was lifted off and printing resumed into the air at about 20:45:48


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("models", nargs="+")
    ap.add_argument("--test", required=True)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--streak", type=int, default=3)
    a = ap.parse_args()
    frames = sorted(Path(a.test).glob("*.jpg"), key=lambda p: p.stem.split("_")[-1])
    for m in a.models:
        model = YOLO(m)
        scores = []
        for p in frames:
            r = model.predict(str(p), imgsz=320, conf=0.01, verbose=False)[0]
            s = [c for k, c in zip(r.boxes.cls.tolist(), r.boxes.conf.tolist()) if int(k) == 1]
            scores.append((p.stem.split("_")[-1], max(s, default=0.0)))
        neg = [s for t, s in scores if t < FAIL_FROM]
        pos = [s for t, s in scores if t >= FAIL_FROM]
        streak, alerts, best_streak_neg = 0, [], 0
        for t, s in scores:
            streak = streak + 1 if s >= a.conf else 0
            if t < FAIL_FROM:
                best_streak_neg = max(best_streak_neg, streak)
            elif streak >= a.streak:
                alerts.append(t)
        first = alerts[0] if alerts else None
        delay = None
        if first:
            delay = (int(first[:2]) * 3600 + int(first[2:4]) * 60 + int(first[4:])) - (20 * 3600 + 45 * 60 + 48)
        print(f"{m}\n  normal ({len(neg)}): >= {a.conf} in {sum(s >= a.conf for s in neg)}, max {max(neg):.2f}, "
              f"longest streak {best_streak_neg}{'  FALSE ALERT' if best_streak_neg >= a.streak else ''}\n"
              f"  failing ({len(pos)}): >= {a.conf} in {sum(s >= a.conf for s in pos)}, max {max(pos):.2f}, "
              f"median {sorted(pos)[len(pos) // 2]:.2f}; first alert "
              f"{f'{first} ({delay} s after the failure started)' if first else 'NEVER'}, alerting checks {len(alerts)}")


if __name__ == "__main__":
    main()
