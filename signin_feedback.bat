@echo off
rem ============================================================
rem  Daily sign-in + problem feedback for all robots.
rem  Skips ad watching (equivalent to --ad-times 0).
rem ============================================================
cd /d "%~dp0"
py -3.13 main.py --no-ad
