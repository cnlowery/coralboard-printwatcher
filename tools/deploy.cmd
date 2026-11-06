@echo off
rem Copy the print-failure detector to the CoralBoard (through the printer's 2222 forward) and test it on the NPU.
rem Sends the .vmfb, quant_params.json, board\failure_detect.py, watch.py, board_test.sh and test_images\ to
rem /home/root/printwatch on the board, runs board_test.sh there, and unpacks the annotated images and log
rem into board\results\. Asks for the board's root password once.
rem   deploy.cmd        copy and test
rem   deploy.cmd copy   copy only (e.g. after changing watch.py)
rem Files go over as a tar on ssh's stdin and come back as a tar on its stdout. cmd pipes and redirects are
rem binary-safe; PowerShell 5.1's are not, which is why this is a .cmd.
rem ustar format because the board's busybox tar doesn't understand the pax headers Windows tar writes by default.
setlocal
if not defined PRINTWATCH_HOST (echo Set PRINTWATCH_HOST to your printer's address first, e.g.  setx PRINTWATCH_HOST 192.168.1.50 & exit /b 1)
set PRINTER=%PRINTWATCH_HOST%
set ROOT=%~dp0..
set OUT=%~dp0..\board\results
set SEND=tar --format ustar -cf - -C "%ROOT%\models\cameteam_v8n_320" printwatch_failures_320_int8.vmfb quant_params.json -C "%ROOT%\board" failure_detect.py watch.py board_test.sh test_images
set UNPACK=mkdir -p /home/root/printwatch/runs ^&^& tar -xf - -C /home/root/printwatch
if /i "%~1"=="copy" (
    %SEND% | ssh -p 2222 root@%PRINTER% "%UNPACK% && echo Copied to /home/root/printwatch"
    goto :eof
)
if exist "%OUT%" rmdir /s /q "%OUT%"
mkdir "%OUT%"
%SEND% | ssh -p 2222 root@%PRINTER% "%UNPACK% && sh /home/root/printwatch/board_test.sh" > "%OUT%\results.tar"
tar -xf "%OUT%\results.tar" -C "%OUT%" && del "%OUT%\results.tar"
echo.
echo Results in %OUT%:
dir /b "%OUT%"
