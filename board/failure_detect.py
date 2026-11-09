"""PrintWatcher print-failure detector, run on the CoralBoard NPU (Torq .vmfb from ml/convert_failures.sh).

Adapted from Synaptics' sl2610-examples/jellectronica/detector.py (Apache-2.0, see NOTICE) (letterbox -> int8 -> Torq -> dequantize -> NMS). Changes:
- 9 output channels: cx, cy, w, h (fractions of 320, with the letterbox padding) then 5 class scores;
  argmax picks the class, and NMS runs per class.
- Quant params come from quant_params.json next to the model, not hard-coded.

Run with the examples' venv (it has torq.runtime and OpenCV):
  /home/root/sl2610-examples/.venv/bin/python3 failure_detect.py --image frame.jpg [--conf 0.25] [--bench 20] [--save out.jpg]
"""
import argparse
import json
import os
import sys
import time

import cv2
import numpy as np

EXAMPLES = "/home/root/sl2610-examples"
HERE = os.path.dirname(os.path.abspath(__file__))
SIZE = 320
CLASSES = ["error extrusion", "spaghetti", "stringing", "warping", "zits"]  # cameteam data.yaml order
COLOURS = [(0, 165, 255), (0, 0, 255), (255, 0, 255), (255, 255, 0), (0, 255, 0)]  # BGR
IOU = 0.45
MAX_DET = 30


def load(model):
    # Imported here so preprocess/postprocess can be tested on the PC (ml/check_failures.py style) without the board.
    sys.path.insert(0, EXAMPLES)
    from app_utils.torq_examples.utils.inference import SimpleVMFBInferenceRunner
    q = json.load(open(os.path.join(os.path.dirname(model), "quant_params.json")))
    runner = SimpleVMFBInferenceRunner(model, device_uri="torq", function="main",
                                       load_model_to_mem=True, device_io=True)
    return runner, q


def preprocess(frame, q):
    h, w = frame.shape[:2]
    r = min(SIZE / w, SIZE / h)
    nw, nh = int(w * r), int(h * r)  # int(), as in the calibration set
    pad = np.full((SIZE, SIZE, 3), 114, np.uint8)
    top, left = (SIZE - nh) // 2, (SIZE - nw) // 2
    pad[top:top + nh, left:left + nw] = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), (nw, nh))
    blob = np.clip(np.round(pad.astype(np.float32) / 255.0 / q["input_scale"] + q["input_zero_point"]), -128, 127)
    return blob.astype(np.int8)[None], (top / SIZE, left / SIZE)


def nms(b, s):
    order, keep = s.argsort()[::-1], []
    area = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    while order.size and len(keep) < MAX_DET:
        i = order[0]
        keep.append(i)
        xx1, yy1 = np.maximum(b[i, 0], b[order[1:], 0]), np.maximum(b[i, 1], b[order[1:], 1])
        xx2, yy2 = np.minimum(b[i, 2], b[order[1:], 2]), np.minimum(b[i, 3], b[order[1:], 3])
        inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
        order = order[1:][inter / (area[i] + area[order[1:]] - inter + 1e-9) <= IOU]
    return keep


def postprocess(raw, q, shape, pad, conf):
    out = (np.asarray(raw).astype(np.float32).reshape(9, -1) - q["output_zero_point"]) * q["output_scale"]
    out = out.T  # (2100, 9)
    cls, score = out[:, 4:].argmax(1), out[:, 4:].max(1)
    m = score >= conf
    out, cls, score = out[m], cls[m], score[m]
    oh, ow = shape
    b = out[:, :4].copy()
    b[:, 0] -= pad[1]
    b[:, 1] -= pad[0]
    b *= max(oh, ow)  # back to original pixels
    xyxy = np.stack([b[:, 0] - b[:, 2] / 2, b[:, 1] - b[:, 3] / 2, b[:, 0] + b[:, 2] / 2, b[:, 1] + b[:, 3] / 2], 1)
    xyxy = np.clip(xyxy, 0, [ow, oh, ow, oh])
    dets = []
    for c in np.unique(cls):
        idx = np.where(cls == c)[0]
        dets += [(int(c), float(score[i]), xyxy[i]) for i in idx[nms(xyxy[idx], score[idx])]]
    return sorted(dets, key=lambda d: -d[1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--model", default=os.path.join(HERE, "printwatch_failures_320_int8.vmfb"))
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--bench", type=int, default=0, help="extra timed inferences on the same frame")
    ap.add_argument("--save", help="write an annotated copy here (always, even with no detections)")
    a = ap.parse_args()

    t0 = time.perf_counter()
    runner, q = load(a.model)
    print(f"model loaded in {(time.perf_counter() - t0) * 1000:.0f} ms: {a.model}")
    frame = cv2.imread(a.image)
    if frame is None:
        sys.exit(f"can't read {a.image}")
    t0 = time.perf_counter()
    blob, pad = preprocess(frame, q)
    t1 = time.perf_counter()
    raw = runner.infer(blob)
    t2 = time.perf_counter()
    dets = postprocess(raw, q, frame.shape[:2], pad, a.conf)
    t3 = time.perf_counter()
    print(f"preprocess {(t1 - t0) * 1000:.1f} ms, first inference {(t2 - t1) * 1000:.1f} ms, "
          f"postprocess {(t3 - t2) * 1000:.1f} ms")
    if a.bench:
        times = []
        for _ in range(a.bench):
            s = time.perf_counter()
            runner.infer(blob)
            times.append((time.perf_counter() - s) * 1000)
        print(f"NPU inference over {a.bench} runs: median {np.median(times):.1f} ms, min {min(times):.1f} ms")
    print(f"{len(dets)} detection(s) at conf >= {a.conf}")
    for c, s, b in dets:
        print(f"  {CLASSES[c]:16s} {s:.2f}  box {b.round().astype(int).tolist()}")
    if a.save:
        for c, s, b in dets:
            x1, y1, x2, y2 = b.astype(int)
            cv2.rectangle(frame, (x1, y1), (x2, y2), COLOURS[c], 3)
            cv2.putText(frame, f"{CLASSES[c]} {s:.2f}", (x1, max(20, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.8, COLOURS[c], 2)
        cv2.imwrite(a.save, frame)


if __name__ == "__main__":
    main()
