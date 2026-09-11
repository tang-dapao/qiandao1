@echo off
rem ============================================================
rem  Watch ads only (skips sign-in and problem feedback).
rem  Runs ALL whitelisted robots in a single task-center session
rem  per robot (F10). Each robot stops automatically when its day
rem  quota (X/10) is full -- leftover quota is topped up, robots
rem  already at 10/10 exit immediately. So this tops every robot
rem  off to 10/10 without forcing 10 ads on already-done ones.
rem ============================================================
cd /d "%~dp0"
py -3.13 main.py --ad-only
