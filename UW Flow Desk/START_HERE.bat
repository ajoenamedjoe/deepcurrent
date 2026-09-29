@echo off
title UW Unusual Flow Desk
cd /d "%~dp0"

echo.
echo   UW Unusual Flow Desk
echo   --------------------
echo.

rem First run: make a .env from the example so the desk starts and tells you
rem what is missing, rather than failing on a file that is not there.
if not exist ".env" (
  if exist ".env.example" (
    copy /y ".env.example" ".env" >nul
    echo   Created .env from .env.example.
    echo   Open .env and paste your Unusual Whales token after UW_TOKEN=
    echo   ^(or leave it blank to borrow a sibling desk's token^).
    echo.
  )
)

rem Find a Python. The launcher is the reliable one on Windows.
set PYEXE=
py -3 --version >nul 2>&1 && set PYEXE=py -3
if not defined PYEXE python --version >nul 2>&1 && set PYEXE=python
if not defined PYEXE python3 --version >nul 2>&1 && set PYEXE=python3

if not defined PYEXE (
  echo   Python was not found on this PC.
  echo   Install it from https://www.python.org/downloads/ and tick
  echo   "Add python.exe to PATH" on the first screen, then run this again.
  echo.
  pause
  exit /b 1
)

echo   Starting the desk. The board opens in your browser.
echo   Leave this window open. Close it to stop the desk.
echo.

%PYEXE% server.py

echo.
echo   The desk stopped.
pause
