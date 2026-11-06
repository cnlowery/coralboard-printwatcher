"""Check the converted print-failure detector (convert_failures.sh) on the PC before it goes on the board.

Runs the same letterboxed test images through:
  1. the float ONNX model (best_normalized_output.onnx, onnxruntime): the reference,
  2. the full-int8 TFLite model (ai-edge-litert),
  3. the compiled .vmfb on Torq's x86 simulator (torq_hw_type="sim"),
then reports how far the int8 outputs drift from float, whether the .vmfb matches the TFLite bit for bit, and
image-level spaghetti hits against the dataset labels. Decoding matches jellectronica's detector.py
(one int8 scale for all channels; boxes are cx, cy, w, h already divided by 320).

Run in the conversion venv (WSL):
  ~/venvs/printwatch-convert/bin/python check_failures.py ~/printwatch/convert/cameteam_v8n_320 ~/printwatch/data/cameteam/test [N]
"""
import json
import random
import sys
import time
from pathlib import Path

import cv2
import numpy as np

SIZE = 320
CONF = 0.25
IOU = 0.45
SPAGHETTI = 1  # class index in the cameteam data.yaml


def letterbox(path):
    img = cv2.cvtColor(cv2.imread(str(path)), cv2.COLOR_BGR2RGB)
    h, w = img.shape[:2]
    s = min(SIZE / w, SIZE / h)
    r = cv2.resize(img, (int(w * s), int(h * s)))
    pad = np.full((SIZE, SIZE, 3), 114, np.uint8)
    t, l = (SIZE - r.shape[0]) // 2, (SIZE - r.shape[1]) // 2
    pad[t:t + r.shape[0], l:l + r.shape[1]] = r
    return pad.astype(np.float32) / 255.0  # NHWC [0,1], as in the calibration set


def nms(boxes, scores):
    x1, y1 = boxes[:, 0] - boxes[:, 2] / 2, boxes[:, 1] - boxes[:, 3] / 2
    x2, y2 = boxes[:, 0] + boxes[:, 2] / 2, boxes[:, 1] + boxes[:, 3] / 2
    area = (x2 - x1) * (y2 - y1)
    order, keep = scores.argsort()[::-1], []
    while order.size:
        i = order[0]
        keep.append(i)
        xx1, yy1 = np.maximum(x1[i], x1[order[1:]]), np.maximum(y1[i], y1[order[1:]])
        xx2, yy2 = np.minimum(x2[i], x2[order[1:]]), np.minimum(y2[i], y2[order[1:]])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        order = order[1:][inter / (area[i] + area[order[1:]] - inter + 1e-9) < IOU]
    return keep


def decode(out):
    """out: float [9, 2100] -> list of (class, score, box)."""
    boxes, cls = out[:4].T, out[4:].T
    c, s = cls.argmax(1), cls.max(1)
    m = s >= CONF
    boxes, c, s = boxes[m], c[m], s[m]
    dets = []
    for k in np.unique(c):  # class-wise NMS
        idx = np.where(c == k)[0]
        dets += [(int(k), float(s[i]), boxes[i]) for i in idx[nms(boxes[idx], s[idx])]]
    return dets


def main():
    conv, test = Path(sys.argv[1]), Path(sys.argv[2])
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 60
    q = json.loads((conv / "quant_params.json").read_text())
    in_s, in_zp, out_s, out_zp = q["input_scale"], q["input_zero_point"], q["output_scale"], q["output_zero_point"]

    import onnxruntime as ort
    from ai_edge_litert.interpreter import Interpreter
    from torq.runtime.vmfb import VMFBInferenceRunner

    sess = ort.InferenceSession(str(conv / "best_normalized_output.onnx"), providers=["CPUExecutionProvider"])
    tfl = Interpreter(model_path=str(next(conv.glob("onnx2tf_normalized_full_int8/*_full_integer_quant.tflite"))))
    tfl.allocate_tensors()
    ti, to = tfl.get_input_details()[0], tfl.get_output_details()[0]
    vm = VMFBInferenceRunner(str(conv / "printwatch_failures_320_int8.vmfb"), torq_hw_type="sim")

    imgs = sorted(p for p in (test / "images").iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    # Half the sample from images labelled spaghetti, half from the rest, so both hits and false alarms show.
    def has_spag(p):
        lab = test / "labels" / (p.stem + ".txt")
        return lab.exists() and any(l.split()[0] == str(SPAGHETTI) for l in lab.read_text().splitlines() if l.strip())
    rng = random.Random(0)
    spag = [p for p in imgs if has_spag(p)]
    other = [p for p in imgs if not has_spag(p)]
    sample = rng.sample(spag, min(n // 2, len(spag))) + rng.sample(other, min(n - n // 2, len(other)))

    stats = {k: {"tp": 0, "fn": 0, "fp": 0, "tn": 0} for k in ("onnx", "tflite", "vmfb")}
    drift_tfl, drift_vm, bit_equal, vm_ms = [], [], 0, []
    for p in sample:
        x = letterbox(p)[None]
        f = sess.run(None, {sess.get_inputs()[0].name: x.transpose(0, 3, 1, 2)})[0][0]
        xq = np.clip(np.round(x / in_s + in_zp), -128, 127).astype(np.int8)
        tfl.set_tensor(ti["index"], xq)
        tfl.invoke()
        rt = tfl.get_tensor(to["index"])
        t0 = time.time()
        rv = np.asarray(vm.infer([xq])[0])
        vm_ms.append((time.time() - t0) * 1000)
        bit_equal += int(np.array_equal(rt.reshape(-1), rv.reshape(-1)))
        outs = {"onnx": f,
                "tflite": ((rt.astype(np.float32) - out_zp) * out_s).reshape(f.shape),
                "vmfb": ((rv.astype(np.float32) - out_zp) * out_s).reshape(f.shape)}
        drift_tfl.append(np.abs(outs["tflite"] - f).max())
        drift_vm.append(np.abs(outs["vmfb"] - f).max())
        truth = has_spag(p)
        line = []
        for k, o in outs.items():
            hit = any(c == SPAGHETTI for c, _, _ in decode(o))
            stats[k]["tp" if truth and hit else "fn" if truth else "fp" if hit else "tn"] += 1
            line.append(f"{k}={'Y' if hit else '-'}")
        print(f"[{len(vm_ms)}/{len(sample)}] spaghetti label={'Y' if truth else '-'} {' '.join(line)} "
              f"vmfb==tflite={np.array_equal(rt.reshape(-1), rv.reshape(-1))} sim {vm_ms[-1] / 1000:.0f} s", flush=True)

    print(f"images: {len(sample)} ({len(spag) and min(n // 2, len(spag))} labelled spaghetti)")
    print(f"quant: in scale {in_s:.9f} zp {in_zp}; out scale {out_s:.9f} zp {out_zp}")
    print(f"max |int8 - float| over all outputs: tflite {max(drift_tfl):.4f} (median {np.median(drift_tfl):.4f}), "
          f"vmfb {max(drift_vm):.4f} (median {np.median(drift_vm):.4f})")
    print(f"vmfb == tflite bit for bit: {bit_equal}/{len(sample)} images")
    print(f"(one int8 step is {out_s:.4f}; box channels are fractions of 320 px, so 0.01 is about 3 px)")
    print(f"vmfb simulator time: median {np.median(vm_ms):.0f} ms/image (x86 simulator, not the NPU)")
    print(f"image-level spaghetti at conf {CONF}:")
    for k, s in stats.items():
        rec = s["tp"] / max(1, s["tp"] + s["fn"])
        print(f"  {k:7s} hits {s['tp']}/{s['tp'] + s['fn']} (recall {rec:.2f}), false alarms {s['fp']}/{s['fp'] + s['tn']}")


if __name__ == "__main__":
    main()
