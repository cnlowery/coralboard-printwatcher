#!/usr/bin/env bash
# Convert the trained print-failure detector (train_failures.sh) to a Torq .vmfb for the CoralBoard NPU (SL2610),
# following Synaptics' validated path: sl2610-examples/jellectronica/docs/model-conversion.md
#   best.pt -> ONNX (320) -> boxes /320 -> full-int8 TFLite (onnx2tf 2.6.8, tf_converter, 300 calibration images)
#   -> TOSA MLIR -> torq-compile --torq-hw=SL2610 -> .vmfb
# Our model has 5 classes, so the output is [1, 9, 2100] (4 box + 5 class scores), not the guide's [1, 5, 2100].
# The ONNX export and calibration set use the training venv (it already has ultralytics, torch and OpenCV);
# onnx2tf and the Torq tools get their own venv so they don't pull a second copy of torch.
#
# Usage (from Windows): wsl -d Ubuntu -- bash "<path to this script>" setup | export | convert
set -euo pipefail

TRAIN_VENV=~/venvs/printwatch-train
VENV=~/venvs/printwatch-convert
WORK=~/printwatch
DATA=$WORK/data/cameteam
RUN_NAME=${RUN_NAME:-cameteam_v8n_320}
OUT=$WORK/convert/$RUN_NAME
HERE=$(cd "$(dirname "$0")" && pwd)
SIZE=320
N_CAL=300

setup() {
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip
  # Versions: onnx2tf pinned by the guide; Torq 2.3.0 is the PyPI release (cp312, x86-64).
  # The guide's --tflite_backend tf_converter needs TensorFlow, which onnx2tf 2.6.8 only installs with its
  # [tensorflow] extra (tensorflow 2.21.0, tf-keras, keras; PyPI metadata, 2026-10-30).
  "$VENV/bin/pip" install "onnx2tf[tensorflow]==2.6.8" onnxsim "torq-compiler[tflite]==2.3.0" torq-runtime==2.3.0
  "$VENV/bin/onnx2tf" -V || true
  for t in onnxsim tosa-converter-for-tflite torq-compile torq-run-module; do
    printf '%s: ' "$t"; command -v "$VENV/bin/$t" || echo MISSING
  done
}

export_onnx() {
  mkdir -p "$OUT"
  cp "$WORK/runs/$RUN_NAME/weights/best.pt" "$OUT/best.pt"
  cd "$OUT"
  # Plain export (best.onnx, boxes in pixels): kept as the float reference for check_failures.py.
  "$TRAIN_VENV/bin/yolo" export model=best.pt format=onnx imgsz=$SIZE
  # Step 4, normalized boxes (best_normalized_output.onnx). The guide divides the output's box channels by 320
  # after the fact; in full int8 that wrecks the boxes (found 2026-10-30, class scores were fine):
  #  - YOLOv8's final Concat joins pixel boxes (0-320) with 0-1 class scores, and an int8 CONCATENATION shares one
  #    scale, sized for the scores (0-1.82): boxes clipped to 1.82 px.
  #  - Even dividing before that Concat left the decode itself (anchor -/+ distances, then x stride) in grid and
  #    pixel units with badly calibrated int8 ranges (up to 582): box errors up to 0.42 of the image.
  # So scale the DFL distances and the anchor points by stride/320 *before* decoding (the trick older Ultralytics
  # TFLite exports used). dist2bbox is linear, so the result equals boxes/320, and every box tensor stays near 0-1.6.
  "$TRAIN_VENV/bin/python" - "$OUT" $SIZE <<'EOF'
import sys, shutil
from pathlib import Path
from ultralytics import YOLO
from ultralytics.nn.modules import head
out_dir, size = Path(sys.argv[1]), float(sys.argv[2])

def _get_decode_boxes(self, x):
    shape = x["feats"][0].shape
    if self.dynamic or self.shape != shape:
        self.anchors, self.strides = (a.transpose(0, 1) for a in head.make_anchors(x["feats"], self.stride, 0.5))
        self.shape = shape
    norm = self.strides / size  # (1, 2100): stride/320 per anchor
    return self.decode_bboxes(self.dfl(x["boxes"]) * norm, self.anchors.unsqueeze(0) * norm, x.get("angle"))

shutil.copy(out_dir / "best.onnx", out_dir / "best_pixels.onnx")
head.Detect._get_decode_boxes = _get_decode_boxes
YOLO(str(out_dir / "best.pt")).export(format="onnx", imgsz=int(size))
(out_dir / "best.onnx").replace(out_dir / "best_normalized_output.onnx")
(out_dir / "best_pixels.onnx").replace(out_dir / "best.onnx")

import numpy as np, onnxruntime as ort  # check: normalized == pixels / 320 on a random input
x = np.random.default_rng(0).random((1, 3, int(size), int(size)), dtype=np.float32)
a = ort.InferenceSession(str(out_dir / "best.onnx")).run(None, {"images": x})[0]
b = ort.InferenceSession(str(out_dir / "best_normalized_output.onnx")).run(None, {"images": x})[0]
print("ONNX output", b.shape, "| box max diff vs pixels/320:", float(np.abs(b[:, :4] - a[:, :4] / size).max()),
      "| class max diff:", float(np.abs(b[:, 4:] - a[:, 4:]).max()))
print("wrote best_normalized_output.onnx")
EOF
  # Step 5 calibration set: 300 random training images, RGB, letterboxed (pad 114), NHWC float32 in [0,1].
  "$TRAIN_VENV/bin/python" - "$DATA/train/images" "$OUT/calibration_nhwc_float32.npy" $SIZE $N_CAL <<'EOF'
import sys, random, cv2, numpy as np
from pathlib import Path
src, dst, S, n = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
paths = sorted(p for p in src.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
random.Random(0).shuffle(paths)
samples = []
for p in paths[:n]:
    img = cv2.cvtColor(cv2.imread(str(p)), cv2.COLOR_BGR2RGB)
    h, w = img.shape[:2]
    s = min(S / w, S / h)
    r = cv2.resize(img, (int(w * s), int(h * s)))
    pad = np.full((S, S, 3), 114, np.uint8)
    t, l = (S - r.shape[0]) // 2, (S - r.shape[1]) // 2
    pad[t:t + r.shape[0], l:l + r.shape[1]] = r
    samples.append(pad.astype(np.float32) / 255.0)
cal = np.stack(samples).astype(np.float32)
np.save(dst, cal)
print("calibration", cal.shape, cal.dtype)
EOF
}

convert() {
  cd "$OUT"
  export PATH="$VENV/bin:$PATH"
  rm -rf "${OUT:?}/onnx2tf_normalized_full_int8"
  onnx2tf -i best_normalized_output.onnx -o onnx2tf_normalized_full_int8 -oiqt -iqd int8 -oqd int8 \
    --tflite_backend tf_converter \
    -cind images calibration_nhwc_float32.npy "[[[[0.0,0.0,0.0]]]]" "[[[[1.0,1.0,1.0]]]]"
  local tfl=onnx2tf_normalized_full_int8/best_normalized_output_full_integer_quant.tflite
  tosa-converter-for-tflite "$tfl" --text -o normalized_full_integer.tosa.mlir
  set +e
  torq-compile normalized_full_integer.tosa.mlir -o printwatch_failures_320_int8.vmfb --torq-hw=SL2610
  echo "torq-compile EXIT=$?"
  set -e
  ls -l printwatch_failures_320_int8.vmfb
  # Quantization parameters the board-side detector needs (detector.py's IN_SCALE/IN_ZP and QUANT_PARAMS).
  python3 - "$tfl" <<'EOF'
import sys, json
try:
    from ai_edge_litert.interpreter import Interpreter
except ImportError:
    from tensorflow.lite.python.interpreter import Interpreter
it = Interpreter(model_path=sys.argv[1])
i, o = it.get_input_details()[0], it.get_output_details()[0]
q = {"input_shape": i["shape"].tolist(), "input_scale": float(i["quantization"][0]), "input_zero_point": int(i["quantization"][1]),
     "output_shape": o["shape"].tolist(), "output_scale": float(o["quantization"][0]), "output_zero_point": int(o["quantization"][1])}
print(json.dumps(q, indent=1))
json.dump(q, open("quant_params.json", "w"), indent=1)
EOF
  local dest=$HERE/../models/$RUN_NAME
  mkdir -p "$dest"
  cp printwatch_failures_320_int8.vmfb quant_params.json "$dest/"
  cp "$tfl" "$dest/printwatch_failures_320_int8.tflite"
  echo "Copied model to $dest"
}

case "${1:-}" in
  setup) setup ;;
  export) export_onnx ;;
  convert) convert ;;
  *) echo "Usage: $0 setup | export | convert"; exit 1 ;;
esac
