@echo off
rem PrintWatcher boot service on the CoralBoard (board\printwatcher_service.py + printwatcher.service).
rem Edit the two EDIT lines in board\printwatcher.service first. Each command asks for the CoralBoard password once.
rem   service.cmd install   copy the files, install and start the service, check the board reaches Moonraker,
rem                         and send a TEST alert to ntfy (picture + Pause button)
rem   service.cmd update    copy the files again and restart the service (after changing the scripts)
rem   service.cmd test      send a TEST alert (only while the printer isn't printing: it needs the camera)
rem   service.cmd log       the last 60 lines of the service log
rem   service.cmd follow    the service log live (Ctrl-C to stop watching; the service keeps running)
rem   service.cmd status | stop | start | uninstall
rem Stop the service before a manual tools\watch.cmd run: both want the camera.
rem Binary tar over ssh, so this is a .cmd (see deploy.cmd).
setlocal
if not defined PRINTWATCH_HOST (echo Set PRINTWATCH_HOST to your printer's address first, e.g.  setx PRINTWATCH_HOST 192.168.1.50 & exit /b 1)
set PRINTER=%PRINTWATCH_HOST%
set ROOT=%~dp0..
set DIR=/home/root/printwatch
set PY=/home/root/sl2610-examples/.venv/bin/python3
set SEND=tar --format ustar -cf - -C "%ROOT%\models\cameteam_v8n_320" printwatch_failures_320_int8.vmfb quant_params.json -C "%ROOT%\board" failure_detect.py watch.py stream_frames.py printwatcher_service.py printwatcher.service
set UNPACK=mkdir -p %DIR% ^&^& tar -xf - -C %DIR%
set ACT=%~1
if "%ACT%"=="" set ACT=status

if /i "%ACT%"=="install" (
    %SEND% | ssh -p 2222 root@%PRINTER% "%UNPACK% && cp %DIR%/printwatcher.service /etc/systemd/system/ && systemctl daemon-reload && systemctl enable --now printwatcher && echo Service installed and started. && echo Moonraker from the board: && (wget -q -O - -T 5 'http://192.168.137.1/printer/objects/query?print_stats' | cut -c1-300; echo) && sleep 8 && journalctl -u printwatcher -n 15 --no-pager && echo Sending a TEST alert... && cd %DIR% && export $(systemctl show printwatcher -p Environment --value) && %PY% -u printwatcher_service.py --test-alert"
    goto :eof
)
if /i "%ACT%"=="update" (
    %SEND% | ssh -p 2222 root@%PRINTER% "%UNPACK% && cp %DIR%/printwatcher.service /etc/systemd/system/ && systemctl daemon-reload && systemctl restart printwatcher && echo Moonraker from the board: && (wget -q -O - -T 5 'http://192.168.137.1/printer/objects/query?print_stats' | cut -c1-300; echo) && sleep 12 && journalctl -u printwatcher -n 15 --no-pager"
    goto :eof
)
if /i "%ACT%"=="test" (
    ssh -p 2222 root@%PRINTER% "cd %DIR% && export $(systemctl show printwatcher -p Environment --value) && %PY% -u printwatcher_service.py --test-alert"
    goto :eof
)
if /i "%ACT%"=="log" (
    ssh -p 2222 root@%PRINTER% "journalctl -u printwatcher -n 60 --no-pager"
    goto :eof
)
if /i "%ACT%"=="follow" (
    ssh -t -p 2222 root@%PRINTER% "journalctl -u printwatcher -f"
    goto :eof
)
if /i "%ACT%"=="uninstall" (
    ssh -p 2222 root@%PRINTER% "systemctl disable --now printwatcher; rm -f /etc/systemd/system/printwatcher.service; systemctl daemon-reload; echo Service removed."
    goto :eof
)
ssh -p 2222 root@%PRINTER% "systemctl %ACT% printwatcher --no-pager; systemctl is-active printwatcher"
