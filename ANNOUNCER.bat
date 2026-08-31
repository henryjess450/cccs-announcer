@echo off
REM ====================================================================
REM  THE ONLY FILE YOU NEED.
REM
REM  Double-click it. It will:
REM     1. install everything, the first time only
REM     2. pull the latest code
REM     3. start the announcer
REM     4. show you the sign-in details and the addresses
REM
REM  Safe to run whenever you like. This is also what Windows runs
REM  automatically at sign-in.
REM
REM  The real work is in scripts\setup.ps1 and scripts\update.ps1 --
REM  PowerShell handles downloads and permissions far more reliably
REM  than a batch file can.
REM ====================================================================

title CCCS Announcer
cd /d "%~dp0"

REM ---- 1. First-time setup, skipped once it has been done ------------
if exist ".venv\Scripts\python.exe" if exist "data\setup-complete.txt" goto update

powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\setup.ps1"
if errorlevel 1 (
    echo.
    echo   Setup did not finish. The reason is above.
    echo.
    pause
    exit /b 1
)

REM ---- 2. Pull the latest code --------------------------------------
REM  This can never stop the announcer starting. With no internet it
REM  prints a line and carries on.
:update
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\update.ps1"

REM ---- 3. Start ------------------------------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo.
    echo   Something is missing. Delete the .venv folder and run this again.
    echo.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" run.py

echo.
echo  ==================================================================
echo   THE ANNOUNCER HAS STOPPED. The reason is above.
echo  ==================================================================
echo.
echo   If it says it is already running, that is correct: another copy
echo   is going, and two would talk over each other on the speakers.
echo.
pause
