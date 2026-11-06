#!/usr/bin/env bash
# Evaluate the trained print-failure detector (train_failures.sh): score it on the dataset's held-out test split,
# then run it on your own printer frames (snapshots/*.jpg) and copy annotated images back to ml/runs/<run>/.
# Usage (from Windows): wsl -d Ubuntu -- bash "<path to this script>"
set -euo pipefail

VENV=~/venvs/printwatch-train
WORK=~/printwatch
DATA=$WORK/data/cameteam
RUN_NAME=${RUN_NAME:-cameteam_v8n_320}
HERE=$(cd "$(dirname "$0")" && pwd)
MODEL=$WORK/runs/$RUN_NAME/weights/best.pt
DEST=$HERE/runs/$RUN_NAME

cd "$WORK"
echo "== Test split =="
"$VENV/bin/yolo" detect val model="$MODEL" data="$DATA/data_abs.yaml" split=test imgsz=320 batch=64 device=0 \
  project="$WORK/runs" name="${RUN_NAME}_test" exist_ok=True 2>&1 | tr '\r' '\n' | grep -E "^ +(Class|all|error|spaghetti|stringing|warping|zits) "

echo "== Printer frames (conf 0.10) =="
rm -rf "$WORK/frames"; mkdir -p "$WORK/frames"
cp "$HERE"/../snapshots/*.jpg "$WORK/frames/" 2>/dev/null || true
"$VENV/bin/yolo" detect predict model="$MODEL" source="$WORK/frames" imgsz=320 conf=0.1 device=0 \
  project="$WORK/runs" name="${RUN_NAME}_frames" exist_ok=True 2>&1 | tr '\r' '\n' | grep -E "^image "

echo "== Test-image samples (conf 0.25) =="
rm -rf "$WORK/samples"; mkdir -p "$WORK/samples"
ls "$DATA/test/images" | sort | awk 'NR % 50 == 1' | head -8 | while read -r f; do cp "$DATA/test/images/$f" "$WORK/samples/"; done
"$VENV/bin/yolo" detect predict model="$MODEL" source="$WORK/samples" imgsz=320 conf=0.25 device=0 \
  project="$WORK/runs" name="${RUN_NAME}_samples" exist_ok=True 2>&1 | tr '\r' '\n' | grep -E "^image "

rm -rf "${DEST:?}/printer_frames" "${DEST:?}/test_samples"
mkdir -p "$DEST/printer_frames" "$DEST/test_samples"
cp "$WORK/runs/${RUN_NAME}_frames"/*.jpg "$DEST/printer_frames/"
cp "$WORK/runs/${RUN_NAME}_samples"/*.jpg "$DEST/test_samples/"
echo "Copied annotated images to $DEST"
