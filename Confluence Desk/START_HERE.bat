@echo off
title Confluence Desk
setlocal

REM ------------------------------------------------------------------
REM  Confluence Desk -- insider buying + dark pool + 13F, scored 0-100.
REM
REM  Double-click this file. Do NOT open index.html directly: the page
REM  talks to this process, and this process is what holds the API token.
REM
REM  This file MUST stay CRLF. An LF-only batch file breaks "goto" labels,
REM  so re-apply CRLF after any edit on a Linux box or a zip round trip.
REM ------------------------------------------------------------------

cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY goto nopython

REM Find a token: this folder first, then whichever sibling desk has one.
set "TOKEN="
if exist ".env" call :readenv ".env"
if not defined TOKEN if exist "..\Swing Desk\.env" call :readenv "..\Swing Desk\.env"
if not defined TOKEN if exist "..\UW SwingDesk\.env" call :readenv "..\UW SwingDesk\.env"
if not defined TOKEN if exist "..\Institutional Desk\.env" call :readenv "..\Institutional Desk\.env"
if not defined TOKEN goto notoken

if not exist "..\Institutional Desk\institutional.db" (
  echo.
  echo   NOTE: no institutional.db next door, so there is no 13F backdrop.
  echo   The board still works - cards just cap at 75 of 100.
  echo.
)

echo.
echo   Confluence Desk starting. The browser opens on its own.
echo   Leave this window open. Close it to stop the desk.
echo.
REM The SERVER opens the browser, not this file. Both opening one gave two
REM tabs on every launch, and the server wins because it knows the real port.
%PY% server.py
goto end

:readenv
REM tokens=1,* and NOT tokens=2 -- a value with "=" padding truncates otherwise.
for /f "usebackq tokens=1,* delims==" %%a in ("%~1") do (
  if /i "%%a"=="UW_API_TOKEN" set "TOKEN=%%b"
)
goto :eof

:nopython
echo.
echo   Python 3 was not found on this PC.
echo   Install it from python.org and tick "Add python.exe to PATH".
echo.
pause
goto end

:notoken
echo.
echo   No UW_API_TOKEN found.
echo   Copy .env.example to .env and put your token in it, or leave a
echo   sibling desk's .env in place next door.
echo.
pause

:end
endlocal
