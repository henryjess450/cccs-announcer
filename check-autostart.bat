@echo off
REM ===================================================================
REM  Will the announcer come back on its own after a power cut?
REM  Answers the question, and offers to fix what it can.
REM ===================================================================
title CCCS Announcer - Autostart
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\enable_autostart.ps1" -Check
echo.
choice /c YN /t 20 /d N /m "  Set up anything that is missing now (Y/N)"
if errorlevel 2 goto done
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\enable_autostart.ps1"
:done
echo.
pause
