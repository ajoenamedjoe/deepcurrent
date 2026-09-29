@echo off
title Valuation Desk -- diagnostic
cd /d "%~dp0"
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
set /p TK="Ticker to probe [AAPL]: "
if "%TK%"=="" set "TK=AAPL"
%PY% diag.py %TK%
pause
