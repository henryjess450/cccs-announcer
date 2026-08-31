@echo off
REM ====================================================================
REM  THE ONLY FILE YOU RUN.
REM
REM  You can download JUST this file and double-click it. If the rest of
REM  the announcer is not here, it fetches it first.
REM
REM  Every run it will:
REM     1. download the announcer, the first time only
REM     2. install everything, the first time only
REM     3. pull the latest code
REM     4. start the announcer
REM     5. show the sign-in details and the addresses
REM
REM  Safe to run whenever you like. This is also what Windows runs
REM  automatically at sign-in.
REM ====================================================================

title CCCS Announcer
cd /d "%~dp0"

set "REPO=https://github.com/henryjess450/cccs-announcer.git"

REM ---- 0. Are we a lone .bat in someone's Downloads folder? ----------
if exist "run.py" goto setup

REM  Where it will live. C:\announcer is tidiest, but a standard (non
REM  administrator) account cannot create a folder at the root of C:, and
REM  the PA machine is supposed to run as a standard account. The
REM  bootstrap tries C:\announcer first and falls back to the user's own
REM  folder, then tells us which one it used.
set "TARGET=C:\announcer"
set "FALLBACK=%USERPROFILE%\announcer"

if exist "%TARGET%\run.py" goto handoff
if exist "%FALLBACK%\run.py" (
    set "TARGET=%FALLBACK%"
    goto handoff
)

echo.
echo  ==================================================================
echo   CCCS ANNOUNCER
echo.
echo   Only this one file is here, so the rest will be downloaded.
echo.
echo   It is a private repository, so you will be asked to sign in to
echo   GitHub once. A browser window may open.
echo  ==================================================================
echo.

REM Write a small PowerShell script to a temp file and run it. Doing this
REM rather than a long inline -Command avoids batch and PowerShell
REM fighting over quote characters, which is where this normally breaks.
set "BOOT=%TEMP%\announcer-bootstrap.ps1"
set "WHERE=%TEMP%\announcer-target.txt"
if exist "%BOOT%" del /q "%BOOT%"
if exist "%WHERE%" del /q "%WHERE%"

>>"%BOOT%" echo $ErrorActionPreference = 'Stop'
>>"%BOOT%" echo $target = '%TARGET%'
>>"%BOOT%" echo try {
>>"%BOOT%" echo   if (-not (Test-Path $target)) { New-Item -ItemType Directory -Path $target -Force -ErrorAction Stop }
>>"%BOOT%" echo } catch {
>>"%BOOT%" echo   Write-Host '  No permission to use C:\announcer, using your own folder instead.'
>>"%BOOT%" echo   $target = '%FALLBACK%'
>>"%BOOT%" echo   if (-not (Test-Path $target)) { New-Item -ItemType Directory -Path $target -Force }
>>"%BOOT%" echo }
>>"%BOOT%" echo Set-Content -Path '%WHERE%' -Value $target
>>"%BOOT%" echo if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
>>"%BOOT%" echo   Write-Host '  Installing Git first. Windows may ask for permission...'
>>"%BOOT%" echo   $rel = Invoke-RestMethod -UseBasicParsing -Uri 'https://api.github.com/repos/git-for-windows/git/releases/latest'
>>"%BOOT%" echo   $asset = $null
>>"%BOOT%" echo   foreach ($a in $rel.assets) { if ($a.name -like '*-64-bit.exe' -and $a.name -notlike '*Portable*') { $asset = $a; break } }
>>"%BOOT%" echo   if ($asset -eq $null) { Write-Host '  Could not find the Git installer.'; exit 2 }
>>"%BOOT%" echo   $exe = Join-Path $env:TEMP $asset.name
>>"%BOOT%" echo   Invoke-WebRequest -UseBasicParsing -Uri $asset.browser_download_url -OutFile $exe
>>"%BOOT%" echo   Start-Process -FilePath $exe -Wait -ArgumentList '/VERYSILENT','/NORESTART','/NOCANCEL','/SP-'
>>"%BOOT%" echo   Remove-Item $exe -ErrorAction SilentlyContinue
>>"%BOOT%" echo   $gitDir = Join-Path $env:ProgramFiles 'Git\cmd'
>>"%BOOT%" echo   if (Test-Path $gitDir) { $env:Path = $env:Path + ';' + $gitDir }
>>"%BOOT%" echo }
>>"%BOOT%" echo if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
>>"%BOOT%" echo   Write-Host '  Git is still not available. Restart the computer and try again.'
>>"%BOOT%" echo   exit 2
>>"%BOOT%" echo }
>>"%BOOT%" echo Write-Host ('  Downloading the announcer into ' + $target + ' ...')
>>"%BOOT%" echo git clone '%REPO%' $target
>>"%BOOT%" echo if ($LASTEXITCODE -ne 0) { exit 1 }
>>"%BOOT%" echo exit 0

powershell -NoProfile -ExecutionPolicy Bypass -File "%BOOT%"
set "BOOTCODE=%ERRORLEVEL%"
del /q "%BOOT%" >nul 2>nul

if exist "%WHERE%" for /f "usebackq delims=" %%T in ("%WHERE%") do set "TARGET=%%T"
del /q "%WHERE%" >nul 2>nul

if not "%BOOTCODE%"=="0" goto boot_failed
if not exist "%TARGET%\run.py" goto boot_failed

:handoff
echo.
echo  ==================================================================
echo   The announcer now lives in:
echo       %TARGET%
echo.
echo   From now on run:
echo       %TARGET%\ANNOUNCER.bat
echo.
echo   Starting it now. You can delete this downloaded copy.
echo  ==================================================================
timeout /t 4 >nul
start "" "%TARGET%\ANNOUNCER.bat"
exit /b 0

REM ---- 1. First-time setup, skipped once it has been done ------------
:setup
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
exit /b 0

:boot_failed
echo.
echo  ==================================================================
echo   COULD NOT DOWNLOAD THE ANNOUNCER
echo  ==================================================================
echo.
echo   Do it by hand instead:
echo.
echo     1. Open https://github.com/henryjess450/cccs-announcer
echo        ^(sign in to GitHub - the repository is private^)
echo     2. Click the green "Code" button, then "Download ZIP"
echo     3. Unzip it to  %TARGET%
echo     4. Double-click %TARGET%\ANNOUNCER.bat
echo.
pause
exit /b 1
