@echo off
rem ============================================================
rem  Watch-ad rotation only (skips sign-in and problem feedback).
rem  Ad count per robot comes from config.yaml
rem  (workflow.ad_times_per_robot, default 10).
rem ============================================================
cd /d "%~dp0"
py -3.13 main.py --ad-only
