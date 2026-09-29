@echo off
cd /d "%~dp0"
python make_share.py
if errorlevel 1 (echo. & echo Share zip NOT built -- see above.)
pause
