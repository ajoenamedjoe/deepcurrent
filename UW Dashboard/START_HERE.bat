@echo off
title UW Dashboard
setlocal

REM ------------------------------------------------------------------
REM  UW Dashboard -- every desk, today's events, portfolio and P&L in one page.
REM  Double-click this file. Do NOT open index.html directly.
REM  This file MUST stay CRLF.
REM ------------------------------------------------------------------

cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY goto nopython

echo.
echo   UW Dashboard starting on http://127.0.0.1:8700/
echo   The browser opens on its own. Leave this window open; close it to stop
echo   the dashboard. Desks it started keep running - stop them from their page.
echo.
%PY% server.py
goto end

:nopython
echo.
echo   Python 3 was not found on this PC.
echo   Install it from python.org and tick "Add python.exe to PATH".
echo.
pause

:end
endlocal
