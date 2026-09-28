@echo off
title UW Desk Suite - tests
cd /d "%~dp0"
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
%PY% run_all_tests.py
echo.
pause
