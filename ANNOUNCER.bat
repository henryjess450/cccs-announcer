@echo off
REM ====================================================================
REM  THE ONLY FILE YOU NEED.
REM
REM  Double-click it. The first time it installs everything and sets the
REM  computer up. Every time after that it just starts the announcer.
REM  Safe to run whenever you like.
REM
REM  This is also what Windows runs automatically at sign-in.
REM
REM  The setup work lives in scripts\setup.ps1 -- PowerShell handles
REM  downloads and elevation far more reliably than a batch file can.
REM ====================================================================

title CCCS Announcer
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" if exist "data\setup-complete.txt" goto run

powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\setup.ps1"
if errorlevel 1 (
    echo.
    echo   Setup did not finish. The reason is above.
    echo.
    pause
    exit /b 1
)

:run
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
