@echo off
title Valuation Desk
setlocal

REM ------------------------------------------------------------------
REM  Valuation Desk -- DCF + scenarios -> report, script, share card.
REM  Double-click this file. Do NOT open index.html directly.
REM  This file MUST stay CRLF.
REM ------------------------------------------------------------------

cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY goto nopython

echo.
echo   Valuation Desk starting. The browser opens on its own.
echo   Leave this window open. Close it to stop the desk.
echo.
REM The SERVER opens the browser (it knows the real port), not this file.
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
