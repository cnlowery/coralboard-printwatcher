"""Propose 'spaghetti' boxes on watch-run frames by colour: works when the failed filament is a colour
nothing else in the scene shares in thin strands (green PETG, 2026-10-31). Boxes are proposals to review.

- Green mask in HSV, minus the yellow printhead's hull and the band above it down to the nozzle (green fan
  covers, feed filament).
- Green is merged into nests with a dilation; nests with >= --min-area green pixels get a tight box.
- The filament feeding into the top of the extruder is a green line too: a box that is tall and thin, or
  reaches the top --top-margin pixels, is dropped.

  python autolabel_green.py RUN_DIR [--from HHMMSS] [--to HHMMSS] [--out DIR]
Writes YOLO labels (class 1 = spaghetti, normalised xywh) to DIR/labels/ for every frame (empty outside
--from/--to: negatives), and DIR/review/ with the boxes drawn.
"""
import argparse
from pathlib import Path

import cv2
import numpy as np

SPAGHETTI = 1


def head_zone(hsv):
    """The yellow printhead's convex hull, grown a little: the green fan covers sit inside it."""
    yellow = cv2.morphologyEx(cv2.inRange(hsv, (20, 120, 120), (35, 255, 255)), cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(yellow)
    zone = np.zeros_like(yellow)
    if n > 1:
        k = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        if stats[k, cv2.CC_STAT_AREA] > 1500:
            pts = cv2.findNonZero((lab == k).astype(np.uint8))
            cv2.fillConvexPoly(zone, cv2.convexHull(pts), 255)
            zone = cv2.dilate(zone, np.ones((11, 11), np.uint8))
            # The fan covers stick out sideways and the feed filament runs straight up from the head: also
            # exclude the head's width (plus a margin) from the top of the frame to just above the nozzle.
            x, y, w, h = cv2.boundingRect(pts)
            cv2.rectangle(zone, (x - 25, 0), (x + w + 25, y + h - 20), 255, -1)
    return zone


def boxes_for(img, min_area, top_margin):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, (50, 60, 90), (90, 255, 255))
    green = cv2.subtract(green, head_zone(hsv))
    green = cv2.morphologyEx(green, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))  # drop single-pixel noise
    nests = cv2.dilate(green, np.ones((15, 15), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(nests)
    out = []
    for k in range(1, n):
        x, y, w, h, _ = stats[k]
        px = cv2.findNonZero(cv2.bitwise_and(green, green, mask=(lab == k).astype(np.uint8)))
        if px is None or len(px) < min_area:
            continue
        x, y, w, h = cv2.boundingRect(px)  # tight box around the green itself, not the dilation
        if y < top_margin or h > 4 * w or min(w, h) < 12:  # feed filament (tall thin line); specks (screen LED)
            continue
        ev = np.linalg.eigvalsh(np.cov(px.reshape(-1, 2).T.astype(np.float64)))
        if ev[0] < (0.15 ** 2) * ev[1]:  # one straight line, e.g. the green graphic on the bed plate; nests are 2-D
            continue
        out.append((x, y, w, h))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--from", dest="t0", default="000000")
    ap.add_argument("--to", dest="t1", default="999999")
    ap.add_argument("--min-area", type=int, default=60, help="green pixels in a box (640 px frames)")
    ap.add_argument("--top-margin", type=int, default=30)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = Path(a.out)
    (out / "labels").mkdir(parents=True, exist_ok=True)
    (out / "review").mkdir(parents=True, exist_ok=True)
    n_img = n_box = 0
    for p in sorted(Path(a.run, "frames").glob("*.jpg")):
        t = p.stem.split("_")[1]
        img = cv2.imread(str(p))
        if img.shape[1] != 640:  # the thresholds are in 640 px pixels; the first run saved full 1280x720 frames
            img = cv2.resize(img, (640, round(img.shape[0] * 640 / img.shape[1])), interpolation=cv2.INTER_AREA)
        H, W = img.shape[:2]
        # Outside the failure window a frame is a negative (empty label), whatever green is in it: e.g. the
        # green part being printed before it was knocked off.
        bs = boxes_for(img, a.min_area, a.top_margin) if a.t0 <= t <= a.t1 else []
        lines = [f"{SPAGHETTI} {(x + w / 2) / W:.5f} {(y + h / 2) / H:.5f} {w / W:.5f} {h / H:.5f}" for x, y, w, h in bs]
        (out / "labels" / f"{p.stem}.txt").write_text("\n".join(lines) + ("\n" if lines else ""))
        for x, y, w, h in bs:
            cv2.rectangle(img, (x, y), (x + w, y + h), (0, 0, 255), 2)
        cv2.putText(img, f"{p.stem} {len(bs)}", (5, 350), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
        cv2.imwrite(str(out / "review" / p.name), img)
        n_img += 1
        n_box += len(bs)
    print(f"{n_img} frames, {n_box} boxes, {sum(1 for f in (out / 'labels').glob('*.txt') if f.stat().st_size)} frames with boxes")


if __name__ == "__main__":
    main()
