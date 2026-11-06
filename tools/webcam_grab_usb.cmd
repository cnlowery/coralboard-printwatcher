@echo off
rem Grab one frame from a USB webcam on the CoralBoard when the board is plugged into this PC (adb, no password)
rem and open it. Saved as ..\snapshots\webcam_<date>_<time>.jpg. Same capture as webcam_grab.cmd (the ssh
rem version for the board on the printer): MJPEG straight from the camera, 45 frames so exposure settles.
set ADB=%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe
set DIR=%~dp0..\snapshots
if not exist "%DIR%" mkdir "%DIR%"
for /f %%T in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set STAMP=%%T
set OUT=%DIR%\webcam_%STAMP%.jpg
"%ADB%" shell "for d in /dev/video*; do v4l2-ctl -d $d --info 2>/dev/null | grep -q uvcvideo && v4l2-ctl -d $d --list-formats 2>/dev/null | grep -q MJPG && break; done; gst-launch-1.0 -q v4l2src device=$d num-buffers=45 ! image/jpeg,width=1920,height=1080 ! multifilesink location=/dev/shm/webcam.jpg >/dev/null 2>&1"
"%ADB%" pull /dev/shm/webcam.jpg "%OUT%" >nul && "%ADB%" shell "rm -f /dev/shm/webcam.jpg /dev/shm/inno.jpg"
if exist "%OUT%" (
  for %%F in ("%OUT%") do echo Saved %%~fF ^(%%~zF bytes^)
  start "" "%OUT%"
) else (
  echo No picture. Is the board plugged in? Check with: "%ADB%" devices
)
