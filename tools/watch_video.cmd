@echo off
rem Live video from the toolhead camera in a window on this PC: see watch_video.py. Asks for the CoralBoard password once.
rem   watch_video.cmd           as the camera sends it (right way up on our toolhead mount)
rem   watch_video.cmd rotate    turned 180 degrees
rem Close the window (or press q in it) to stop. Deploy stream_frames.py first: tools\deploy.cmd copy
if /i "%~1"=="rotate" (python "%~dp0watch_video.py" --rotate) else (python "%~dp0watch_video.py")
