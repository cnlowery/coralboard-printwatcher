"""Build the 'own camera' dataset from watch runs + autolabel_green.py labels, and a data YAML that mixes it
with the Roboflow (cameteam) set for fine-tuning. Runs in WSL (train venv).

Splits are by time block, not random, because neighbouring frames are near-duplicates:
- train: the clump run (164128), the fake-failure run (205452) except spot 5, and the first half of the
  real-failure run's normal printing (204131, before 20:43:40).
- val:   fake-failure spot 5 (21:00:30-21:01:30) and the clump run after 16:49:00.
- test:  the real failure (204131 from 20:45:48, never trained on) plus its normal printing after 20:43:40.
Frames inside a failure window that the labeller gave no box are left out (likely a nest it couldn't see),
so they never teach "nothing here". Own train images are listed --repeat times so they aren't swamped by
the ~larger Roboflow set.

  python build_own_dataset.py --runs RUNS_DIR --labels LABELS_DIR --cameteam DATA_DIR --out OUT_DIR
"""
import argparse
import shutil
from pathlib import Path

import cv2
import yaml

# run: (failure window start HHMMSS or None, function(time) -> split)
RUNS = {
    "20261031_164128": ("164733", lambda t: "val" if t >= "164900" else "train"),
    "20261031_205452": ("205704", lambda t: "val" if "210030" <= t < "210130" else "train"),
    "20261031_204131": ("204548", lambda t: "train" if t < "204340" else "test"),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", required=True)
    ap.add_argument("--labels", required=True, help="autolabel_green.py output, one folder per run (short name)")
    ap.add_argument("--cameteam", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--repeat", type=int, default=4)
    a = ap.parse_args()
    out = Path(a.out)
    shutil.rmtree(out, ignore_errors=True)
    counts = {}
    for run, (fail_from, split_of) in RUNS.items():
        lab_dir = Path(a.labels, run.split("_")[1], "labels")
        for img_path in sorted(Path(a.runs, run, "frames").glob("*.jpg")):
            t = img_path.stem.split("_")[1]
            split = split_of(t)
            failing = t >= fail_from
            label = (lab_dir / f"{img_path.stem}.txt").read_text()
            if run == "20261031_204131" and failing:
                label = ""  # test positives: scored per frame against the known failure time, no boxes needed
            elif failing and not label.strip():
                counts[(split, "skipped")] = counts.get((split, "skipped"), 0) + 1
                continue
            kind = "pos" if failing else "neg"
            counts[(split, kind)] = counts.get((split, kind), 0) + 1
            name = f"{run}_{img_path.stem}"
            (out / split / "images").mkdir(parents=True, exist_ok=True)
            (out / split / "labels").mkdir(parents=True, exist_ok=True)
            img = cv2.imread(str(img_path))
            if img.shape[1] != 640:
                img = cv2.resize(img, (640, round(img.shape[0] * 640 / img.shape[1])), interpolation=cv2.INTER_AREA)
            cv2.imwrite(str(out / split / "images" / f"{name}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
            (out / split / "labels" / f"{name}.txt").write_text(label)
    for k in sorted(counts):
        print(k, counts[k])

    cam = Path(a.cameteam)
    own_train = sorted(str(p) for p in (out / "train" / "images").glob("*.jpg"))
    cam_train = sorted(str(p) for p in (cam / "train" / "images").glob("*"))
    (out / "train_list.txt").write_text("\n".join(cam_train + own_train * a.repeat) + "\n")
    (out / "val_list.txt").write_text("\n".join(
        sorted(str(p) for p in (cam / "valid" / "images").glob("*")) +
        sorted(str(p) for p in (out / "val" / "images").glob("*.jpg"))) + "\n")
    names = yaml.safe_load((cam / "data.yaml").read_text())["names"]
    (out / "data_mix.yaml").write_text(yaml.safe_dump(
        {"path": str(out), "train": str(out / "train_list.txt"), "val": str(out / "val_list.txt"), "names": names},
        sort_keys=False))
    print(f"train list: {len(cam_train)} cameteam + {len(own_train)} own x{a.repeat}")


if __name__ == "__main__":
    main()
