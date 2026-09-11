@echo off
rem ============================================================
rem  Manual ad test across ALL whitelisted robots (single run).
rem  Watch-ad only; default 1 ad per robot for a quick full-loop
rem  sanity check (~20 min for 10 robots).
rem  Usage: ad_test_all.bat [COUNT]   e.g. ad_test_all.bat 3
rem
rem  NOTE: per-robot loop STOPS automatically when the day quota
rem  (X/10) is full -- robots with leftover quota only get topped
rem  up, robots already at 10/10 exit immediately. So running with
rem  a big COUNT never overshoots the quota; it only caps how many
rem  ads this invocation may add.
rem ============================================================
if "%1"=="" (set COUNT=1) else (set COUNT=%1)
cd /d "%~dp0"
py -3.13 main.py --ad-only --ad-times %COUNT%
