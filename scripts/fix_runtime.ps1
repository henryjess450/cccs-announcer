<#
    Install the Microsoft Visual C++ Runtime that Piper needs.

    ANNOUNCER.bat offers to run this when it notices the runtime is missing,
    so you should not normally have to run it yourself.

    Safe to run at any time: it does nothing if the runtime is already there.
#>

$ErrorActionPreference = 'Stop'

$system32 = Join-Path $env:SystemRoot 'System32'
$present = $false
foreach ($dll in @('msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll')) {
    if (Test-Path (Join-Path $system32 $dll)) { $present = $true; break }
}

if ($present) {
    Write-Host "  The Visual C++ Runtime is already installed. Nothing to do."
    exit 0
}

Write-Host ""
Write-Host "  Installing the Microsoft Visual C++ Runtime."
Write-Host "  The announcement voice cannot start without it."
Write-Host "  Windows will ask for permission - say Yes."
Write-Host ""

try {
    $installer = Join-Path $env:TEMP 'vc_redist.x64.exe'
    Invoke-WebRequest -UseBasicParsing `
        -Uri 'https://aka.ms/vs/17/release/vc_redist.x64.exe' -OutFile $installer
    Start-Process -FilePath $installer -Wait -Verb RunAs `
        -ArgumentList '/install', '/quiet', '/norestart'
    Remove-Item $installer -ErrorAction SilentlyContinue
} catch {
    Write-Host ""
    Write-Host "  Could not install it automatically." -ForegroundColor Red
    Write-Host "  Run these two lines in PowerShell instead. They work from any folder:"
    Write-Host ""
    Write-Host "    iwr https://aka.ms/vs/17/release/vc_redist.x64.exe -OutFile `"`$env:TEMP\vc.exe`"" -ForegroundColor Yellow
    Write-Host "    Start-Process -Wait `"`$env:TEMP\vc.exe`" -ArgumentList '/install','/quiet','/norestart'" -ForegroundColor Yellow
    Write-Host ""
    exit 1
}

$present = $false
foreach ($dll in @('msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll')) {
    if (Test-Path (Join-Path $system32 $dll)) { $present = $true; break }
}

if ($present) {
    Write-Host "  Installed." -ForegroundColor Green
    exit 0
}

Write-Host "  It still is not there. The computer may need restarting." -ForegroundColor Yellow
exit 1
