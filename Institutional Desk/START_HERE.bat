@echo off
setlocal enabledelayedexpansion
title Institutional Desk

cd /d "%~dp0"

echo.
echo   Institutional Desk
echo   ------------------
echo.

rem ---- find python -----------------------------------------------------
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY (
  where python >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo   Python was not found on this PC.
  echo   Install it from https://www.python.org/downloads/ and tick
  echo   "Add python.exe to PATH" during setup, then run this again.
  echo.
  pause
  exit /b 1
)

rem ---- find a token ----------------------------------------------------
set "HAVETOKEN="
if exist ".env" (
  for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
    if /i "%%a"=="UW_API_TOKEN" set "HAVETOKEN=1"
  )
)
if not defined HAVETOKEN (
  if exist "..\Swing Desk\.env" (
    for /f "usebackq tokens=1,* delims==" %%a in ("..\Swing Desk\.env") do (
      if /i "%%a"=="UW_API_TOKEN" set "HAVETOKEN=1"
    )
  )
)
if not defined HAVETOKEN (
  echo   No UW_API_TOKEN found.
  echo   Copy .env.example to .env and paste your Unusual Whales API token in,
  echo   or leave your Swing Desk folder next door and it will be picked up.
  echo.
  pause
  exit /b 1
)

echo   Starting the local server on http://127.0.0.1:8777
echo   Your browser should open in a moment. Close this window to stop.
echo.

%PY% server.py

echo.
echo   Server stopped.
pause
