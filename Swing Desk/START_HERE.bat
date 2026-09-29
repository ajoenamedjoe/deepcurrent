@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title Swing Desk

echo.
echo   ===========================================
echo      SWING DESK
echo   ===========================================
echo.

REM ---------------------------------------------------------------- Python
set PY=
for %%C in ("py -3" "python" "python3") do (
    if not defined PY (
        %%~C --version >nul 2>&1
        if !errorlevel! equ 0 set PY=%%~C
    )
)

if not defined PY (
    echo   [X] Python isn't installed, or Windows can't find it.
    echo.
    echo       Install it from:  https://www.python.org/downloads/
    echo.
    echo       IMPORTANT: on the first install screen, tick the box
    echo       "Add python.exe to PATH" before clicking Install.
    echo.
    echo       Then close this window and double-click START_HERE.bat again.
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%V in ('%PY% --version 2^>^&1') do set PYVER=%%V
echo   Python found: !PYVER!
echo.

REM ---------------------------------------------------------------- Token
if exist ".env" (
    findstr /b /c:"UW_API_TOKEN=" ".env" >nul 2>&1
    if !errorlevel! equ 0 (
        REM tokens=1,* keeps everything after the FIRST "=", so a token
        REM containing "=" padding isn't truncated.
        for /f "tokens=1,* delims==" %%A in ('findstr /b /c:"UW_API_TOKEN=" ".env"') do set SAVED=%%B
    )
)

if defined SAVED if not "!SAVED!"=="" (
    echo   Using the saved API token from your .env file.
    echo   ^(To change it, delete .env and run this again.^)
    echo.
    goto :launch
)

echo   ------------------------------------------------------------
echo    Paste your Unusual Whales API token below.
echo.
echo    Where to find it:
echo      1. Go to unusualwhales.com and sign in
echo      2. Open Settings, then the API tab
echo      3. Copy the token
echo.
echo    To paste here: right-click, or press Ctrl+V
echo   ------------------------------------------------------------
echo.
set /p TOKEN=  Token:

if "!TOKEN!"=="" (
    echo.
    echo   [X] No token entered. Nothing to run without one.
    echo.
    pause
    exit /b 1
)

>".env" echo UW_API_TOKEN=!TOKEN!
echo.
echo   Saved. You won't be asked again on this PC.
echo.

:launch
echo   Starting the dashboard...
echo   Your browser will open automatically in a few seconds.
echo.
echo   ------------------------------------------------------------
echo    LEAVE THIS WINDOW OPEN while you use the dashboard.
echo    Closing it stops the data.
echo    To stop: close this window, or press Ctrl+C.
echo   ------------------------------------------------------------
echo.

REM Single level of quoting - nested quotes inside cmd /c are unreliable.
start "" /b cmd /c "timeout /t 4 /nobreak >nul && start http://127.0.0.1:8787"

%PY% server.py

echo.
echo   The dashboard has stopped.
echo.
pause
