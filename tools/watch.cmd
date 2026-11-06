@echo off
rem Start the PrintWatch watch loop (MONITOR ONLY: logs detections, never pauses the printer) on the CoralBoard
rem and show its live output here. Ctrl-C stops it. Closing this window also stops it (ssh hangs up).
rem Options pass through to watch.py, e.g.:  watch.cmd --interval 2 --conf 0.25 --streak 3 --stream 2
rem Afterwards, tools\watch_fetch.cmd copies the run (log.csv, frames, hits) back to runs\.
rem Deploy watch.py first with tools\deploy.cmd copy.
setlocal
if not defined PRINTWATCH_HOST (echo Set PRINTWATCH_HOST to your printer's address first, e.g.  setx PRINTWATCH_HOST 192.168.1.50 & exit /b 1)
set PRINTER=%PRINTWATCH_HOST%
ssh -t -p 2222 root@%PRINTER% "cd /home/root/printwatch && /home/root/sl2610-examples/.venv/bin/python3 -u watch.py %*"
