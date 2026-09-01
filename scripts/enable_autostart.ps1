<#
    Make the announcer start by itself when the computer is switched on.

        powershell -ExecutionPolicy Bypass -File scripts\enable_autostart.ps1
        powershell -ExecutionPolicy Bypass -File scripts\enable_autostart.ps1 -Check

    Three things have to be true. This script does the first two and checks
    all three:

      1. A scheduled task starts the announcer when this account signs in.
      2. Windows signs in to this account by itself after a restart.
      3. The BIOS switches the computer on when power comes back.

    Number 3 is a firmware setting. No program is allowed to change it, so
    this script can only tell you to go and do it.

    WHY NOT A WINDOWS SERVICE: a service runs in what Windows calls Session 0,
    which has no access to the sound card. Installed as a service the
    announcer would start perfectly and never make a sound. It has to run
    inside a signed-in desktop session, which is why step 2 exists.
#>

param(
    [switch]$Check
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$taskName = 'CCCS Announcer'
$winlogon = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'

function Heading($text) {
    Write-Host ""
    Write-Host "  $text"
    Write-Host "  ------------------------------------------------------------"
}
function Yes($text) { Write-Host "   [ok]   $text" -ForegroundColor Green }
function No($text)  { Write-Host "   [--]   $text" -ForegroundColor Yellow }

# ------------------------------------------------------------------ status

function Get-TaskState {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if (-not $task) { return $null }
    return $task
}

function Get-AutoLogonUser {
    try {
        $settings = Get-ItemProperty -Path $winlogon -ErrorAction Stop
        if ($settings.AutoAdminLogon -eq '1' -and $settings.DefaultUserName) {
            return $settings.DefaultUserName
        }
    } catch { }
    return $null
}

function Show-Status {
    Heading "Will the announcer come back on its own?"

    $task = Get-TaskState
    if ($task) {
        Yes "1. A task starts the announcer when $env:USERNAME signs in."
    } else {
        No  "1. NOTHING starts the announcer at sign-in."
    }

    $autoUser = Get-AutoLogonUser
    if ($autoUser) {
        Yes "2. Windows signs in as '$autoUser' by itself."
    } else {
        No  "2. Windows stops at the sign-in screen, so nothing starts."
    }

    Write-Host "   [??]   3. The BIOS must switch the computer on when power" -ForegroundColor Cyan
    Write-Host "             comes back. No program can read or change this."
    Write-Host "             Restart, press DEL or F2, and set"
    Write-Host "             'Restore on AC Power Loss' to 'Power On'."
    Write-Host ""

    if ($task -and $autoUser) {
        Write-Host "   Steps 1 and 2 are done. Test it properly: pull the power" -ForegroundColor Green
        Write-Host "   cable out, plug it back in, wait two minutes, and open the"
        Write-Host "   announcer from another computer."
    }
    Write-Host ""
}

if ($Check) {
    Show-Status
    exit 0
}

# ------------------------------------------------- 1. Start at sign-in

Heading "1. Starting the announcer when $env:USERNAME signs in"

try {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'install_task.ps1') | Out-Null
    Yes "Done."
} catch {
    No "Could not register the task: $_"
}

# ------------------------------------------------- 2. Sign in by itself

Heading "2. Signing Windows in by itself after a restart"

$existing = Get-AutoLogonUser
if ($existing) {
    Yes "Already set up, as '$existing'."
} else {
    Write-Host ""
    Write-Host "   The announcer needs a signed-in desktop to reach the sound"
    Write-Host "   card, so Windows has to sign in to this account by itself."
    Write-Host ""
    Write-Host "   This uses Autologon, a Microsoft tool, which stores the"
    Write-Host "   password as an encrypted LSA secret rather than as plain"
    Write-Host "   text in the registry."
    Write-Host ""
    Write-Host "   You will type the password for '$env:USERNAME' into that"
    Write-Host "   Microsoft tool. It is not sent anywhere and is not saved by"
    Write-Host "   this script."
    Write-Host ""
    Write-Host "   Because the computer signs itself in, ANYONE WHO CAN REACH" -ForegroundColor Yellow
    Write-Host "   THE KEYBOARD IS INSIDE THAT ACCOUNT. Keep the machine in a" -ForegroundColor Yellow
    Write-Host "   locked cupboard, keep the account a standard user, and lock" -ForegroundColor Yellow
    Write-Host "   the screen with Windows+L. Locking the screen does NOT stop" -ForegroundColor Yellow
    Write-Host "   announcements playing." -ForegroundColor Yellow
    Write-Host ""

    $answer = Read-Host "   Set it up now? (Y/n)"
    if ($answer -eq '' -or $answer -match '^[Yy]') {
        try {
            $zip = Join-Path $env:TEMP 'AutoLogon.zip'
            $dir = Join-Path $env:TEMP 'AutoLogon'
            Write-Host "   Downloading the Microsoft tool..."
            Invoke-WebRequest -UseBasicParsing `
                -Uri 'https://download.sysinternals.com/files/AutoLogon.zip' -OutFile $zip
            Expand-Archive -Force -Path $zip -DestinationPath $dir
            Remove-Item $zip -ErrorAction SilentlyContinue

            $exe = Get-ChildItem -Path $dir -Filter 'Autologon*.exe' -Recurse |
                   Where-Object { $_.Name -notlike '*arm*' } |
                   Select-Object -First 1
            if (-not $exe) { throw "could not find the tool after unpacking" }

            Write-Host ""
            Write-Host "   The Microsoft tool will open. Enter the password for"
            Write-Host "   '$env:USERNAME' and click Enable."
            Write-Host ""
            Start-Process -FilePath $exe.FullName -Wait -Verb RunAs -ArgumentList '/accepteula'

            if (Get-AutoLogonUser) {
                Yes "Done."
            } else {
                No "It does not look enabled. Run this again, or use netplwiz."
            }
        } catch {
            No "Could not set it up automatically."
            Write-Host ""
            Write-Host "   Do it by hand instead: press the Windows key, type"
            Write-Host "   netplwiz, press Enter, and untick 'Users must enter a"
            Write-Host "   user name and password to use this computer'."
            Write-Host ""
            Write-Host "   If that tick box is missing, set this registry value"
            Write-Host "   to 0 and try again:"
            Write-Host "     HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\PasswordLess\Device"
            Write-Host "     DevicePasswordLessBuildVersion"
            Write-Host ""
        }
    } else {
        No "Skipped. The announcer will not come back on its own after a restart."
    }
}

Show-Status
exit 0
