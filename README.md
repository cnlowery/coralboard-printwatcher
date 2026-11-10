# Coral PrintWatcher: software

Print-failure detection that runs on a **Synaptics CoralBoard** (SL2610) next to a Klipper 3D printer. A USB camera on the toolhead looks at the nozzle. Every 2 seconds a YOLOv8n model on the board's NPU checks the newest frame for spaghetti. When it sees spaghetti, it sends a phone notification through [ntfy](https://ntfy.sh) with the picture and a **Pause print** button, so you decide. The detection runs on the board; nothing goes to the cloud except the ntfy message, and not even that if you host ntfy yourself.

The printed case, top mount and camera mount are on Printables: **[Coral PrintWatcher](https://www.printables.com/model/1907730-coralboard-3d-printer-frame-mount-case)**. This repo is only the software side.

> **Status: working proof of concept.** The boot service watches every print and sends ntfy alerts. It never pauses on its own: the notification's button does that. Tested on one printer (Elegoo Neptune 4 Pro); see [Results](#results-so-far) and [What's next](#whats-next).

## How it works

```
toolhead USB camera (MJPEG, 1280x720)
   -> gst-launch writes a 4-frame ring in /dev/shm (RAM; the board decodes nothing)
   -> every 2 s while Moonraker says "printing": newest frame -> letterbox 320 px -> int8 -> NPU (about 14 ms)
   -> spaghetti >= 0.25 on 3 checks in a row  =>  one ntfy alert: annotated frame + [Pause print] [Open Fluidd]
```

- **`board/printwatcher_service.py`** is the boot service (`printwatcher.service`). It polls Moonraker every 5 s. The camera is on while printing or paused and off otherwise. One alert per failure: it re-arms when you resume or start a new print, with one reminder after 15 min if the spaghetti carries on.
- **"Not watching" alert:** if the camera is missing or frames stop during a print, or the service crashes, it says so once (and again when it recovers). If the board itself is off, nothing can tell you.
- **No records:** nothing is saved per check. It keeps the last 20 alert pictures.
- **`board/watch.py`** is the same loop run by hand for testing: it logs every check to `runs/<time>/` (CSV, a 640 px copy of each frame, annotated hits) and prints `WOULD ALERT` instead of notifying.
- **Live video** on the PC: `tools\watch_video.cmd` plays the camera in an ffplay window over the same ssh link, alongside the service.

The board connects to the printer over USB networking (NCM gadget). The printer forwards port 2222 to the board's ssh, so the board needs no Wi-Fi or Ethernet of its own (see [`printer/`](printer/printer-coral-link.sh)).

## Results so far

All tests on an Elegoo Neptune 4 Pro, with the same model each time (YOLOv8n, 320 px, full int8, conf 0.25, alert after 3 hits in a row).

| Test | Camera | Result |
|---|---|---|
| Normal print, PETG cube, 16 min | toolhead (Innomaker U20CAM) | **0 false alerts** (max spaghetti score 0.11) |
| Normal print, ASA, last 100 min of 4 h 13 min, live video running alongside | toolhead | **0 false alerts** in 3,057 checks (max seen 0.13), 24 to 31 ms per check |
| Fake failures ([`ml/fake_failures.py`](ml/fake_failures.py)) | toolhead | **alert 85 s after the first nest started**; 64 of 122 checks ≥ 0.25, max 0.88 |
| Same fake failures | frame-mounted Tapo C100 | alert after about 4.5 min |
| Real failure, part knocked off, 5.5 min | frame-mounted Tapo | **missed**: the nest stayed small and hidden under the head |
| Clump dropped on the bed | frame-mounted Tapo | alert 4 s after it landed |

The frame-camera misses are why the camera moved to the toolhead. From there the nozzle is always in the same spot and close up, so an early nest fills a big part of the frame.

**Timing on the board:** NPU inference median 14 ms. A full check, including JPEG decode, resize and postprocessing, takes about 30 ms.

**Known issue:** one false alert happened on the striped warning decal on the printer's Z upright. It fired while the toolhead sat high and idle, so the decal was in view. While printing, the bed fills the frame. The service only checks while Moonraker reports `printing`, which avoids this.

## What's in here

| Folder | What |
|---|---|
| [`board/`](board/) | Runs on the CoralBoard: `printwatcher_service.py` + `printwatcher.service` (boot service with ntfy alerts), `watch.py` (manual watch loop that logs everything), `stream_frames.py` (live video source), `failure_detect.py` (detector, also a one-image CLI), `board_test.sh` (self-test) |
| [`ml/`](ml/) | Runs on a PC (WSL + GPU): train, evaluate, convert to the NPU format, check the int8 model against float, make fake failures, build your own dataset |
| [`tools/`](tools/) | Windows helpers that run over ssh: deploy, install and manage the service (`service.cmd`), live video (`watch_video.cmd`, needs ffplay), start the manual watch loop, fetch runs, snapshot view, grab a frame |
| [`printer/`](printer/) | Klipper host side: USB link, NAT and port forward to the board |
| [`docs/`](docs/) | [Board setup and gotchas](docs/board-setup.md), [model training and conversion](docs/model.md), [camera](docs/camera.md) |

## Quick start

You need:
- a CoralBoard with Synaptics' [`sl2610-examples`](https://github.com/synaptics-astra-demos/sl2610-examples) installed in `/home/root/sl2610-examples`, with its `.venv` (that provides the Torq runtime and OpenCV)
- a UVC USB camera that does MJPEG
- a converted model in `models/cameteam_v8n_320/`; see [docs/model.md](docs/model.md). The weights aren't in the repo yet.

1. On the printer (Klipper host, as root), set up the USB link:
   ```bash
   bash printer-coral-link.sh install
   ```
2. On the PC, tell the tools where the printer is:
   ```bat
   setx PRINTWATCH_HOST 192.168.1.50
   ```
   Open a new terminal afterwards so the setting takes effect.
3. Deploy and self-test:
   ```bat
   tools\deploy.cmd
   ```
   The self-test needs a few JPEGs in `board/test_images/` first.
4. Set your printer's address and ntfy topic on the two `EDIT` lines in `board/printwatcher.service`, then install the service:
   ```bat
   tools\service.cmd install
   ```
   It starts the service, checks that the board reaches Moonraker, and sends a TEST alert. The Pause button calls Moonraker directly from your phone, so it works when the phone can reach the printer (home Wi-Fi or a VPN), and Moonraker must trust that network. Later: `service.cmd log`, `follow`, `test`, `update`, `stop`, `start`, `uninstall`.
5. Watch the camera live (needs [ffplay](https://ffmpeg.org) on PATH):
   ```bat
   tools\watch_video.cmd
   ```
   Close the window or press q to stop. Add `rotate` if the picture is upside down.
6. Testing by hand instead: stop the service (`service.cmd stop`, both want the camera), then run `tools\watch.cmd` (add `--rotate 180` if needed; Ctrl-C stops it) and copy the run back with `tools\watch_fetch.cmd`.

Every ssh tool connects to `root@<printer>` on port 2222, but that port lands on the **board**, so it asks for the board's root password, not the printer's.

## What's next

- **More real prints** with the service, to see how often it raises false alarms. If it does, skip the first layers.
- **Training data from the toolhead camera:**
  - Fake-failure runs labelled with [`ml/autolabel_green.py`](ml/autolabel_green.py).
  - Decal and idle frames added as negatives.

## Credits and licences

- **Code:** AGPL-3.0 (see [LICENSE](LICENSE)). The model is trained with [Ultralytics YOLOv8](https://github.com/ultralytics/ultralytics), which is AGPL-3.0.
- **`board/failure_detect.py`:** adapted from Synaptics' `sl2610-examples` (`jellectronica/detector.py`), Apache-2.0; see [NOTICE](NOTICE).
- **Model conversion:** follows Synaptics' guide in the same repo (`jellectronica/docs/model-conversion.md`), with a fix for int8 boxes described in [docs/model.md](docs/model.md).
- **Training data:** Roboflow Universe "3D printing error" by cameteam (v2, MIT). It isn't included here; download it from Roboflow.
