@echo off
title Growth Leaders Desk
setlocal

REM ------------------------------------------------------------------
REM  Growth Leaders (O'Neil-style) -- seven growth-stock checks, a daily
REM  scan of ~3,000 stocks after the close, and breakout alerts.
REM
REM  Double-click this file (or let the dashboard start it). Do NOT open
REM  index.html directly: the page talks to this process, which holds the
REM  API token. This file MUST stay CRLF.
REM ------------------------------------------------------------------

cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY goto nopython

echo.
echo   Growth Leaders desk starting. The browser opens on its own.
echo   Leave this window open. Close it to stop the desk.
echo   The token is read from .env here, or from a sibling desk's .env.
echo.
%PY% server.py
goto end

:nopython
echo.
echo   Python 3 was not found on this PC.
echo   Install it from python.org and tick "Add python.exe to PATH".
echo.
pause
goto end

:end
endlocal
