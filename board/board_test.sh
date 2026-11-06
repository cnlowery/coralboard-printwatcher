#!/bin/sh
# Runs on the CoralBoard (sent by tools/deploy_failures.cmd): test the print-failure detector on the NPU
# with the bundled test images, then on a live Tapo frame if a camera is saved (tools/tapo_grab.ps1 -SetCamera).
# The log goes to stderr (the PC console); annotated images + log go back as a tar on stdout.
# Everything temporary is in /dev/shm (RAM), not the SD card.
cd /home/root/printwatch || exit 1
P=/home/root/sl2610-examples/.venv/bin/python3
R=/dev/shm/pw_results
rm -rf $R
mkdir -p $R
{
  echo "== $(date) on $(hostname)"
  B=50   # time 50 extra inferences on the first image only
  for f in test_images/*.jpg; do
    echo "== $f"
    $P failure_detect.py --image "$f" --bench $B --save "$R/det_$(basename "$f")"
    B=0
  done
  if [ -s /home/root/.printwatch/camera_url ]; then
    echo "== Tapo frame (stream1)"
    kill $(pidof gst-launch-1.0) 2>/dev/null
    gst-launch-1.0 -q rtspsrc location="$(cat /home/root/.printwatch/camera_url)/stream1" protocols=tcp ! rtph264depay ! h264parse ! avdec_h264 ! videorate drop-only=true max-rate=1 ! videoconvert ! jpegenc ! multifilesink location=/dev/shm/pw_tapo.jpg &
    G=$!
    sleep 10
    kill $G
    $P failure_detect.py --image /dev/shm/pw_tapo.jpg --save "$R/det_tapo.jpg"
    rm -f /dev/shm/pw_tapo.jpg
  else
    echo "== No Tapo camera saved on the board, skipped the live frame"
  fi
} 2>&1 | tee $R/log.txt 1>&2
cd $R && tar -cf - *
rm -rf $R
