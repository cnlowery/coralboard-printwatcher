@echo off
rem Grab one frame from a USB webcam on the CoralBoard (through the printer's 2222 forward) and save it
rem as ..\snapshots\webcam_view.jpg. Asks for the board's root password.
rem The webcam sends MJPEG itself, so no encoding on the board; 45 frames (about 1.5 s) let exposure settle.
rem The device is found by driver (uvcvideo), so it doesn't matter which /dev/video number it got.
if not defined PRINTWATCH_HOST (echo Set PRINTWATCH_HOST to your printer's address first, e.g.  setx PRINTWATCH_HOST 192.168.1.50 & exit /b 1)
set PRINTER=%PRINTWATCH_HOST%
if not exist "%~dp0..\snapshots" mkdir "%~dp0..\snapshots"
set OUT=%~dp0..\snapshots\webcam_view.jpg
ssh -p 2222 root@%PRINTER% "for d in /dev/video*; do v4l2-ctl -d $d --info 2>/dev/null | grep -q uvcvideo && v4l2-ctl -d $d --list-formats 2>/dev/null | grep -q MJPG && break; done; echo using $d 1>&2; gst-launch-1.0 -q v4l2src device=$d num-buffers=45 ! image/jpeg,width=1920,height=1080 ! multifilesink location=/dev/shm/webcam.jpg 1>&2 && cat /dev/shm/webcam.jpg; rm -f /dev/shm/webcam.jpg" > "%OUT%"
for %%F in ("%OUT%") do echo Saved %%~fF (%%~zF bytes)
