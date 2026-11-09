#!/usr/bin/env bash
# PrintWatcher print-failure detector: fine-tune YOLOv8n at 320 px on a Roboflow YOLOv8 export, in WSL Ubuntu 24.04.
# Venv and data live in WSL (~/venvs, ~/printwatch); results (best.pt, plots) are copied back to ml/runs/.
#
# Usage (from Windows): wsl -d Ubuntu -- bash "<path to this script>" setup <zip>   # venv + unpack dataset
#                       wsl -d Ubuntu -- bash "<path to this script>" train [epochs]
# Needs python3.12-venv (sudo apt-get install python3.12-venv python3-pip).
set -euo pipefail

VENV=~/venvs/printwatch-train
WORK=~/printwatch
DATA=$WORK/data/cameteam          # Roboflow "3D printing error" by Cameteam, v2, MIT licence
RUN_NAME=${RUN_NAME:-cameteam_v8n_320}
HERE=$(cd "$(dirname "$0")" && pwd)

setup() {
  local zip=$1
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip
  "$VENV/bin/pip" install -q ultralytics
  "$VENV/bin/python" -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available())"

  rm -rf "${DATA:?}"; mkdir -p "$DATA"
  "$VENV/bin/python" -m zipfile -e "$zip" "$DATA"   # no unzip in stock WSL Ubuntu
  # Roboflow's data.yaml uses relative paths that Ultralytics resolves inconsistently: rewrite with an absolute root,
  # keeping its class names.
  "$VENV/bin/python" - "$DATA" <<'EOF'
import sys, yaml, pathlib
root = pathlib.Path(sys.argv[1])
src = yaml.safe_load((root / "data.yaml").read_text())
out = {"path": str(root), "train": "train/images", "val": "valid/images", "names": src["names"]}
if (root / "test" / "images").is_dir():
    out["test"] = "test/images"
(root / "data_abs.yaml").write_text(yaml.safe_dump(out, sort_keys=False))
for split in ("train", "valid", "test"):
    d = root / split / "images"
    print(split, len(list(d.glob("*"))) if d.is_dir() else 0, "images")
print("classes:", src["names"])
EOF
}

train() {
  local epochs=${1:-80}
  cd "$WORK"
  # imgsz 320 matches the NPU model (Synaptics' validated conversion path is YOLOv8 at 320).
  "$VENV/bin/yolo" detect train model=yolov8n.pt data="$DATA/data_abs.yaml" imgsz=320 epochs="$epochs" \
    patience=20 batch=64 device=0 project="$WORK/runs" name="$RUN_NAME" exist_ok=True
  local run=$WORK/runs/$RUN_NAME dest=$HERE/runs/$RUN_NAME
  mkdir -p "$dest"
  cp "$run/weights/best.pt" "$run"/*.png "$run"/*.csv "$run/args.yaml" "$dest/" 2>/dev/null || true
  echo "Copied results to $dest"
}

finetune() {
  # Fine-tune the cameteam model on cameteam + our own camera frames (data built by build_own_dataset.py).
  local epochs=${1:-40} name=own_v8n_320
  cd "$WORK"
  "$VENV/bin/yolo" detect train model="$WORK/runs/$RUN_NAME/weights/best.pt" data="$WORK/data/own/data_mix.yaml" \
    imgsz=320 epochs="$epochs" patience=15 batch=64 device=0 project="$WORK/runs" name="$name" exist_ok=True
  local run=$WORK/runs/$name dest=$HERE/runs/$name
  mkdir -p "$dest"
  cp "$run/weights/best.pt" "$run"/*.png "$run"/*.csv "$run/args.yaml" "$dest/" 2>/dev/null || true
  echo "Copied results to $dest"
}

case "${1:-}" in
  setup) setup "$2" ;;
  train) train "${2:-}" ;;
  finetune) finetune "${2:-}" ;;
  *) echo "Usage: $0 setup <roboflow-yolov8.zip> | train [epochs] | finetune [epochs]"; exit 1 ;;
esac
