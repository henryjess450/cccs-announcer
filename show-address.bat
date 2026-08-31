@echo off
title CCCS Announcer - Address
cd /d "%~dp0"
".venv\Scripts\python.exe" scripts\show_address.py %*
echo.
pause
