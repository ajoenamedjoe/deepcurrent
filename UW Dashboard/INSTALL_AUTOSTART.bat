@echo off
title UW Dashboard - start with Windows
setlocal
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY (
  echo   Python 3 was not found on this PC.
  pause
  exit /b 1
)

set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
(
  echo @echo off
  echo REM Written by UW Dashboard INSTALL_AUTOSTART.bat - delete this file to undo.
  echo cd /d "%~dp0"
  echo start "UW Dashboard" /min %PY% server.py --boot
) > "%STARTUP%\UW Dashboard.bat"

echo.
echo   Done. The dashboard now starts (minimised) when you sign in to Windows,
echo   and starts any desk set to "start with the dashboard" in Settings.
echo   To undo: run REMOVE_AUTOSTART.bat
echo.
pause
endlocal
