@echo off
title UW Dashboard - stop starting with Windows
set "F=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\UW Dashboard.bat"
if exist "%F%" ( del "%F%" & echo   Removed. The dashboard no longer starts with Windows. ) else ( echo   It was not set to start with Windows. )
echo.
pause
