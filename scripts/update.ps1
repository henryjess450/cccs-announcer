<#
    Pull the latest code before starting.

    ANNOUNCER.bat runs this on every start. Two rules shape it:

      1. It must NEVER stop the announcer starting. If the PA machine reboots
         at 3 AM with no internet, announcements still have to work at 8 AM.
         Every failure in here is a printed line and nothing more.

      2. It must never sit waiting for input. Credential prompts are turned
         off, and a stalled network connection gives up after ten seconds.
         Setup signs in to GitHub once, interactively, while someone is there.
#>

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# Fail instead of prompting. A prompt here would hang the machine at boot
# with nobody watching.
$env:GIT_TERMINAL_PROMPT = '0'
$env:GCM_INTERACTIVE = 'never'

function Skip($reason) {
    Write-Host "  Updates: $reason"
    exit 0
}

if (-not (Test-Path (Join-Path $root '.git'))) {
    Skip "this folder is not linked to the code repository, so there is nothing to pull."
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Skip "git is not installed, so updates are turned off."
}

Write-Host "  Checking for updates..."

$before = (& git rev-parse --short HEAD 2>$null)

# Give up after ten seconds of no progress rather than hanging on a network
# that accepts the connection and then says nothing.
& git -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=10 pull --ff-only --quiet 2>&1 |
    ForEach-Object { Write-Host "    $_" }

if ($LASTEXITCODE -ne 0) {
    Write-Host "  Updates: could not check (no internet, or not signed in to GitHub)."
    Write-Host "           Starting with the code already here."
    exit 0
}

$after = (& git rev-parse --short HEAD 2>$null)

if ($before -eq $after) {
    Write-Host "  Updates: already up to date."
    exit 0
}

Write-Host "  Updates: pulled new code ($before -> $after)."

# New code may need new libraries. Cheap when nothing changed.
$venvPython = Join-Path $root '.venv\Scripts\python.exe'
if (Test-Path $venvPython) {
    Write-Host "  Updating what the announcer needs..."
    & $venvPython -m pip install --quiet -r requirements.txt 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  WARNING: could not update the libraries. Starting anyway." -ForegroundColor Yellow
    }
}
exit 0
