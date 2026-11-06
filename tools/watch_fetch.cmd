@echo off
rem Copy the newest watch run (log.csv, frames\, hits\) from the CoralBoard to runs\<run>\.
rem   watch_fetch.cmd              newest run
rem   watch_fetch.cmd 20261031_160500   a specific run
rem Asks for the board's root password once. The run stays on the board (delete old ones from
rem /home/root/printwatch/runs/ when done). Binary tar over ssh, so this is a .cmd (see deploy.cmd).
setlocal
if not defined PRINTWATCH_HOST (echo Set PRINTWATCH_HOST to your printer's address first, e.g.  setx PRINTWATCH_HOST 192.168.1.50 & exit /b 1)
set PRINTER=%PRINTWATCH_HOST%
set OUT=%~dp0..\runs
if not exist "%OUT%" mkdir "%OUT%"
set RUN=%~1
if "%RUN%"=="" (
    ssh -p 2222 root@%PRINTER% "cd /home/root/printwatch/runs && R=$(ls | sort | tail -1) && echo $R 1>&2 && tar -cf - $R" > "%OUT%\run.tar"
) else (
    ssh -p 2222 root@%PRINTER% "cd /home/root/printwatch/runs && tar -cf - %RUN%" > "%OUT%\run.tar"
)
tar -xf "%OUT%\run.tar" -C "%OUT%" && del "%OUT%\run.tar"
echo Runs in %OUT%:
dir /b "%OUT%"
