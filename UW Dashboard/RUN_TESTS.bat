@echo off
title UW Dashboard - tests
cd /d "%~dp0"
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
%PY% -m unittest discover -s tests -v
echo.
pause
