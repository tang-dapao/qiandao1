@echo off
rem ============================================================
rem  Sign-in + feedback + ROTATING ad watch (2026-09-16).
rem  Phase 1: per robot, sign-in + feedback + first ad in the
rem           same session (existing first_ad optimization).
rem  Phase 2: robots are split into groups of 3 (config
rem           workflow.ad_rotate_group); within a group robots
rem           take turns watching 1 ad each, so switching absorbs
rem           the 60s CD (u2 measured ~48-49s/ad vs ~89s in-place).
rem           Group shrinks as robots hit quota; a lone remaining
rem           robot falls back to in-place consecutive watching.
rem ============================================================
cd /d "%~dp0"
py -3.13 main.py --rotate
