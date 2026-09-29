@echo off
title UW Desk Suite - setup
cd /d "%~dp0"
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY ( echo Python 3 was not found. Install it from python.org and tick "Add python.exe to PATH". & pause & exit /b 1 )
%PY% setup.py
echo.
pause
