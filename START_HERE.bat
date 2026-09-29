@echo off
title UW Desk Suite
cd /d "%~dp0"
if not exist "Valuation Desk\.env" if not exist "Swing Desk\.env" (
  echo First run: let's save your Unusual Whales API token.
  call SETUP.bat
)
call "UW Dashboard\START_HERE.bat"
