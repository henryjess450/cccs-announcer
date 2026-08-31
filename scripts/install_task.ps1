<#
    Registers the CCCS Announcer to start automatically when the PA machine
    logs in.

    Run this ONCE, in PowerShell, as an administrator, from the announcer
    folder:

        powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1

    Why "at logon" and not a Windows service: a Windows service runs in
    Session 0, which has no access to the audio device. A service would start
    perfectly and never make a sound. The machine must auto-log-in to a
    dedicated account, and the announcer runs inside that session.
    DEPLOYMENT.md covers setting up the auto-logon and locking the machine
    down.
#>

$ErrorActionPreference = "Stop"

$taskName = "CCCS Announcer"
$folder   = Split-Path -Parent $PSScriptRoot
$script   = Join-Path $folder "ANNOUNCER.bat"

if (-not (Test-Path $script)) {
    Write-Error "Could not find $script. Run this from the announcer folder."
}

$action = New-ScheduledTaskAction -Execute $script -WorkingDirectory $folder

# At logon of the account that is currently signed in -- the dedicated PA
# account that Windows auto-logs-in.
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0)   # never time out

$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Starts the school PA announcement system when this computer logs in." `
    -Force | Out-Null

Write-Host ""
Write-Host "Registered the scheduled task '$taskName'." -ForegroundColor Green
Write-Host "It will start the announcer every time $env:USERNAME logs in,"
Write-Host "and restart it every minute if it ever stops."
Write-Host ""
Write-Host "Test it now with:  Start-ScheduledTask -TaskName '$taskName'"
Write-Host ""
