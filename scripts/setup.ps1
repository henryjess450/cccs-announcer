<#
    First-time setup for the CCCS Announcer.

    ANNOUNCER.bat runs this automatically the first time. You should not need
    to run it by hand, but it is safe to: everything it does is skipped if it
    has already been done.

    It is PowerShell rather than batch because it downloads files and asks for
    administrator rights, and batch files are miserable at both.
#>

$ErrorActionPreference = 'Stop'

# Always work from the announcer folder, whatever directory we were called from.
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Step($number, $text) { Write-Host ("  [{0}/8] {1}" -f $number, $text) }
function Ok()                  { Write-Host "        OK." -ForegroundColor Green }
function Note($text)           { Write-Host ("        " + $text) -ForegroundColor Yellow }

Write-Host ""
Write-Host "  =================================================================="
Write-Host "   CCCS ANNOUNCER - FIRST-TIME SETUP"
Write-Host ""
Write-Host "   This takes a few minutes and needs internet access."
Write-Host "   You only have to do it once."
Write-Host "  =================================================================="
Write-Host ""

# ---------------------------------------------------------------- 1. Python
Step 1 "Checking for Python..."

$pythonCommand = $null
$pythonArgs = @()

if (Get-Command python -ErrorAction SilentlyContinue) {
    $pythonCommand = 'python'
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $pythonCommand = 'py'
    $pythonArgs = @('-3')
}

if (-not $pythonCommand) {
    Note "Not installed. Downloading Python, please wait..."
    $installer = Join-Path $env:TEMP 'python-announcer-setup.exe'
    try {
        Invoke-WebRequest -UseBasicParsing `
            -Uri 'https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe' `
            -OutFile $installer
    } catch {
        Write-Host ""
        Write-Host "   Could not download Python." -ForegroundColor Red
        Write-Host "   Install it by hand from https://www.python.org/downloads/windows/"
        Write-Host "   IMPORTANT: tick 'Add python.exe to PATH' on the first screen."
        Write-Host "   Then run ANNOUNCER.bat again."
        exit 1
    }

    Note "Installing Python, please wait..."
    # Per-user install needs no administrator rights.
    Start-Process -FilePath $installer -Wait -ArgumentList `
        '/passive', 'InstallAllUsers=0', 'PrependPath=1', 'Include_test=0'
    Remove-Item $installer -ErrorAction SilentlyContinue

    # PATH has not refreshed in this process, so look where it lands.
    foreach ($version in @('312', '313', '311')) {
        $candidate = Join-Path $env:LOCALAPPDATA "Programs\Python\Python$version\python.exe"
        if (Test-Path $candidate) { $pythonCommand = $candidate; break }
    }
    if (-not $pythonCommand -and (Get-Command python -ErrorAction SilentlyContinue)) {
        $pythonCommand = 'python'
    }
    if (-not $pythonCommand) {
        Write-Host ""
        Write-Host "   Python installed but could not be found." -ForegroundColor Red
        Write-Host "   Restart the computer and run ANNOUNCER.bat again."
        exit 1
    }
}
Ok

# ------------------------------------------------------------ 2. The program
Step 2 "Setting up the announcer..."

$venvPython = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    & $pythonCommand @pythonArgs -m venv .venv
    if ($LASTEXITCODE -ne 0) { Write-Host "   Could not create the environment." -ForegroundColor Red; exit 1 }
}

& $venvPython -m pip install --quiet --upgrade pip
& $venvPython -m pip install --quiet -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "   Could not install what the announcer needs." -ForegroundColor Red
    exit 1
}
Ok

# ------------------------------------------------------- 3. Speech and voice
Step 3 "Getting the speech engine and voice..."

if (Test-Path (Join-Path $root 'piper\piper.exe')) {
    Note "Speech engine already here."
} else {
    Note "Downloading the speech engine, about 20 MB..."
    $zip = Join-Path $env:TEMP 'piper.zip'
    try {
        Invoke-WebRequest -UseBasicParsing `
            -Uri 'https://github.com/rhasspy/piper/releases/latest/download/piper_windows_amd64.zip' `
            -OutFile $zip
        # The archive contains a top-level "piper" folder.
        Expand-Archive -Force -Path $zip -DestinationPath $root
        Remove-Item $zip -ErrorAction SilentlyContinue
    } catch {
        Write-Host ""
        Write-Host "   Could not download the speech engine." -ForegroundColor Red
        Write-Host "   This computer needs internet access for setup, once only."
        Write-Host "   If it has none, DEPLOYMENT.md explains how to copy the"
        Write-Host "   files across on a USB stick."
        exit 1
    }
}

$voices = Join-Path $root 'voices'
if (-not (Test-Path $voices)) { New-Item -ItemType Directory -Path $voices | Out-Null }

$voiceFile = Join-Path $voices 'en_US-lessac-medium.onnx'
if (Test-Path $voiceFile) {
    Note "Voice already here."
} else {
    Note "Downloading the voice, about 65 MB..."
    $base = 'https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx'
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $base -OutFile $voiceFile
        Invoke-WebRequest -UseBasicParsing -Uri ($base + '.json') -OutFile ($voiceFile + '.json')
    } catch {
        Write-Host ""
        Write-Host "   Could not download the voice." -ForegroundColor Red
        Write-Host "   This computer needs internet access for setup, once only."
        exit 1
    }
}
Ok

# ------------------------------------------------- 4. Visual C++ runtime
# Piper is built with Microsoft's compiler and needs its runtime. Windows 10
# does not always have it, and without it piper.exe cannot start at all --
# it raises a "MSVCP140.dll was not found" dialog and no announcement ever
# plays. Installing it here means nobody meets that message.
Step 4 "Checking the Windows components Piper needs..."

$system32 = Join-Path $env:SystemRoot 'System32'
$haveRuntime = $false
foreach ($dll in @('msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll')) {
    if (Test-Path (Join-Path $system32 $dll)) { $haveRuntime = $true; break }
}

if ($haveRuntime) {
    Note "Already installed."
    Ok
} else {
    Note "Installing the Microsoft Visual C++ Runtime..."
    Note "Windows will ask for permission - say Yes."
    try {
        $vc = Join-Path $env:TEMP 'vc_redist.x64.exe'
        Invoke-WebRequest -UseBasicParsing `
            -Uri 'https://aka.ms/vs/17/release/vc_redist.x64.exe' -OutFile $vc
        Start-Process -FilePath $vc -Wait -Verb RunAs `
            -ArgumentList '/install', '/quiet', '/norestart'
        Remove-Item $vc -ErrorAction SilentlyContinue
        Ok
    } catch {
        Note "COULD NOT install it. The voice will not work until you do."
        Note "Run this in PowerShell, then restart the announcer:"
        Write-Host ""
        Write-Host "    iwr https://aka.ms/vs/17/release/vc_redist.x64.exe -OutFile `"`$env:TEMP\vc.exe`"" -ForegroundColor Yellow
        Write-Host "    Start-Process -Wait `"`$env:TEMP\vc.exe`" -ArgumentList '/install','/quiet','/norestart'" -ForegroundColor Yellow
        Write-Host ""
    }
}

# ------------------------------------------------------- 5. Database, chimes
Step 5 "Creating the database and chimes..."
& $venvPython scripts\seed.py | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host "   Setup of the data folder failed." -ForegroundColor Red; exit 1 }
Ok

# ---------------------------------------------------------- 6. Start at logon
Step 6 "Making it come back on its own after a restart..."
try {
    & powershell -NoProfile -ExecutionPolicy Bypass `
        -File (Join-Path $PSScriptRoot 'enable_autostart.ps1')
} catch {
    Note "COULD NOT finish that. Run this afterwards:"
    Note "    powershell -ExecutionPolicy Bypass -File scripts\enable_autostart.ps1"
}

# --------------------------------------------------------------- 7. Firewall
# Read the real port rather than assuming 8080, in case .env changed it.
$port = 8080
try {
    $reported = & $venvPython -c "import sys; sys.path.insert(0, '.'); from app.config import load_config; print(load_config().port)"
    if ($reported -match '^\d+$') { $port = [int]$reported }
} catch { }

Step 7 "Letting staff computers reach it through the firewall..."
Note "Windows will ask for permission - say Yes."
$rule = "New-NetFirewallRule -DisplayName 'CCCS Announcer' -Direction Inbound " +
        "-Protocol TCP -LocalPort $port -Action Allow -Profile Domain,Private " +
        "-ErrorAction SilentlyContinue | Out-Null"
try {
    Start-Process powershell -Verb RunAs -Wait -ArgumentList '-NoProfile', '-Command', $rule
    Ok
} catch {
    Note "SKIPPED. Staff computers may not be able to connect."
    Note "Run this later in an ADMINISTRATOR PowerShell window:"
    Write-Host ""
    Write-Host "    $rule" -ForegroundColor Yellow
    Write-Host ""
}

# -------------------------------------------------------- 8. Code updates
# Linking the folder to the code repository means ANNOUNCER.bat can pull
# fixes by itself from then on. This is the one place a GitHub sign-in can
# be asked for, because somebody is standing here.
Step 8 "Setting up automatic code updates..."

$repoUrl = 'https://github.com/henryjess450/cccs-announcer.git'

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Note "Git is not installed. Downloading it..."
    try {
        $release = Invoke-RestMethod -UseBasicParsing `
            -Uri 'https://api.github.com/repos/git-for-windows/git/releases/latest'
        $asset = $release.assets |
            Where-Object { $_.name -like '*-64-bit.exe' -and $_.name -notlike '*Portable*' } |
            Select-Object -First 1
        if (-not $asset) { throw "no installer found" }

        $gitInstaller = Join-Path $env:TEMP $asset.name
        Invoke-WebRequest -UseBasicParsing -Uri $asset.browser_download_url -OutFile $gitInstaller
        Note "Installing Git. Windows may ask for permission."
        Start-Process -FilePath $gitInstaller -Wait -ArgumentList '/VERYSILENT', '/NORESTART', '/NOCANCEL', '/SP-'
        Remove-Item $gitInstaller -ErrorAction SilentlyContinue

        # PATH has not refreshed in this process.
        $gitExe = Join-Path $env:ProgramFiles 'Git\cmd\git.exe'
        if (Test-Path $gitExe) { $env:Path = "$env:Path;" + (Split-Path $gitExe) }
    } catch {
        Note "Could not install Git. Automatic updates will be off."
        Note "Everything else works; you would update by copying files over."
    }
}

if (Get-Command git -ErrorAction SilentlyContinue) {
    if (Test-Path (Join-Path $root '.git')) {
        Note "Already linked to the code repository."
        Ok
    } else {
        Write-Host ""
        Write-Host "        The announcer can keep itself up to date from GitHub."
        Write-Host "        This is a PRIVATE repository, so you have to sign in once."
        Write-Host "        A browser window may open. It only has to be done here, once."
        Write-Host ""
        $answer = Read-Host "        Set that up now? (Y/n)"
        if ($answer -eq '' -or $answer -match '^[Yy]') {
            try {
                & git init --quiet
                & git remote remove origin 2>$null | Out-Null
                & git remote add origin $repoUrl
                & git fetch origin --quiet
                if ($LASTEXITCODE -ne 0) { throw "could not reach the repository" }
                # Ignored files -- data\, .env, piper\, voices\ -- are untouched.
                & git reset --hard origin/main --quiet
                & git branch --set-upstream-to=origin/main main 2>$null | Out-Null
                Ok
            } catch {
                Note "Could not link to the repository. Automatic updates are off."
                Note "Everything else works. You can try again by running:"
                Note "    powershell -ExecutionPolicy Bypass -File scripts\setup.ps1"
            }
        } else {
            Note "Skipped. Automatic updates are off."
        }
    }
}

# ------------------------------------------------------------------- Finish
$dataDir = Join-Path $root 'data'
if (-not (Test-Path $dataDir)) { New-Item -ItemType Directory -Path $dataDir | Out-Null }
Set-Content -Path (Join-Path $dataDir 'setup-complete.txt') `
    -Value "Setup finished. Delete this file to make ANNOUNCER.bat run setup again."

Write-Host ""
Write-Host "  =================================================================="
Write-Host "   SETUP FINISHED - starting the announcer now"
Write-Host "  =================================================================="

& $venvPython scripts\show_address.py

Write-Host ""
Write-Host "   ONE THING STILL TO DO. No program is allowed to do it:" -ForegroundColor Yellow
Write-Host ""
Write-Host "     Make the computer switch itself on when power comes back."
Write-Host "     This is a BIOS setting, not a Windows one. Restart, press"
Write-Host "     DEL or F2 for the BIOS, and set 'Restore on AC Power Loss'"
Write-Host "     (or 'After Power Failure') to 'Power On'. Save and exit."
Write-Host ""
Write-Host "   To check the rest at any time:"
Write-Host "     powershell -ExecutionPolicy Bypass -File scripts\enable_autostart.ps1 -Check"
Write-Host ""
Write-Host "   DEPLOYMENT.md explains it if you get stuck."
Write-Host ""
Read-Host "   Press Enter to start the announcer"
exit 0
