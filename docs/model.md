# Model: training and conversion to the CoralBoard NPU

All of this runs in WSL Ubuntu 24.04 with an NVIDIA GPU. The venvs live in `~/venvs`, and data and work files in `~/printwatch`.

## 1. Train ([`ml/train_failures.sh`](../ml/train_failures.sh))

The model is YOLOv8n at **320 px**, which matches Synaptics' validated conversion path.

**Dataset:** Roboflow Universe "3D printing error" by cameteam, v2, MIT licence.
- Export it from Roboflow in YOLOv8 format; the download needs a free Roboflow account.
- Size: 3,419 train, 428 val and 427 test images.
- Five classes: error extrusion, spaghetti, stringing, warping, zits.

```bash
wsl -d Ubuntu -- bash ml/train_failures.sh setup <roboflow-yolov8.zip>
```
```bash
wsl -d Ubuntu -- bash ml/train_failures.sh train 80
```

Training took 80 epochs, batch 64, about 8 minutes on an RTX 3060 Ti.

**Test split** (mAP50 / precision / recall):

| Class | mAP50 | Precision | Recall |
|---|---|---|---|
| All | 0.44 | 0.52 | 0.44 |
| **Spaghetti** | **0.72** | **0.73** | **0.70** |

The other classes are weaker at 320 px: zits 0.51, error extrusion 0.44, stringing 0.35, warping 0.20. Only spaghetti counts toward an alert.

To evaluate:

```bash
wsl -d Ubuntu -- bash ml/eval_failures.sh
```

## 2. Convert ([`ml/convert_failures.sh`](../ml/convert_failures.sh))

The steps follow Synaptics' `jellectronica/docs/model-conversion.md`:

`best.pt` → ONNX (320) → boxes normalised to 0–1 → full-int8 TFLite (onnx2tf 2.6.8 with its `[tensorflow]` extra, `tf_converter`, 300 calibration images) → TOSA MLIR → `torq-compile --torq-hw=SL2610` → `.vmfb`

```bash
wsl -d Ubuntu -- bash ml/convert_failures.sh setup
```
```bash
wsl -d Ubuntu -- bash ml/convert_failures.sh export
```
```bash
wsl -d Ubuntu -- bash ml/convert_failures.sh convert
```

The output goes to `models/<run>/`:
- `printwatch_failures_320_int8.vmfb`
- `quant_params.json`
- the int8 `.tflite`

**Interface:**
- **Input:** int8 `[1,320,320,3]`, scale 1/255, zero point −128.
- **Output:** int8 `[1,9,2100]`: `cx, cy, w, h` as fractions of 320, then 5 class scores.

### Gotcha: normalise YOLOv8 boxes *before* decoding

The guide divides the output's box channels by 320 after the fact. In full int8 that wrecks the boxes, while class scores still look fine. So detect/no-detect seems right, but the boxes are garbage. There are two causes:

1. **Shared scale in the final Concat.** YOLOv8's last Concat joins pixel boxes (0–320) with 0–1 scores. An int8 concatenation shares one scale, sized for the scores, so boxes clip at about 1.8 px.
2. **Badly calibrated decode.** Even dividing before the Concat leaves the decode itself (anchor ± distance, × stride) in pixel units, with int8 ranges up to about 580.

**Fix:** at export, patch `Detect._get_decode_boxes` so it scales the DFL distances and anchor points by `stride/320` **before** decoding. The decode is linear, so the output still equals `boxes/320`: max difference 2e-7 on the float ONNX. Every box tensor now stays in about 0–1.6. Results against float:
- Int8 box error: median 1.3 % of the frame (about 4 px).
- Matched detections: median IoU 0.94.

### Check before deploying ([`ml/check_failures.py`](../ml/check_failures.py))

The check runs the same test images through:
- the float ONNX model
- the int8 TFLite model
- the `.vmfb` on Torq's x86 simulator

It reports int8 drift, whether the `.vmfb` matches the TFLite bit for bit, and spaghetti hits and false alarms against the labels.

## 3. Your own training data (work in progress)

- **Fake failures:** [`ml/fake_failures.py`](../ml/fake_failures.py) writes G-code that extrudes into the air in 15 mm squares at 6 spots, then holds still. The result is reproducible "spaghetti" in known places, with `M118` markers in the Klipper console for timing. **The printer must already be homed and the bed must be clear.** Elegoo's print start homes Z over the bed centre with a probe, and a part left there blocks it.
- **Labelling:** [`ml/autolabel_green.py`](../ml/autolabel_green.py) proposes spaghetti boxes by colour, which works when the filament colour (green, for us) appears nowhere else in strands. Review them before training.
- **Dataset:** [`ml/build_own_dataset.py`](../ml/build_own_dataset.py) splits the watch runs by time block, not randomly, because neighbouring frames are near-duplicates. It then mixes them with the Roboflow set.
- **Fine-tuning:** `ml/train_failures.sh finetune`.
- **Scoring:** [`ml/test_own.py`](../ml/test_own.py) scores a model on a held-out real failure the way `watch.py` would.

**What we learned:** fine-tuning with frames from a frame-mounted camera didn't help. An early failure seen from there is a tiny nest under the head. Moving the camera to the toolhead fixed it without retraining (see the results in the [README](../README.md)). The scripts above reference our own run names, so treat them as a template.

## Weights

The trained weights (`best.pt`) and the converted `.vmfb` aren't in the repo yet. Train and convert them with the scripts above.
