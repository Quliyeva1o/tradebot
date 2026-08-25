# One-time setup script for a fresh Windows VPS -- see docs/VPS_DEPLOYMENT_GUIDE.md.
# Run from the repo root (after git clone and placing .env) inside the VPS's own
# PowerShell/RDP session. Recreates the same venv + Task Scheduler setup used on
# the original machine (see run_live_midnight_fvg_demo.bat / _paper.bat).

$ErrorActionPreference = "Stop"
$RepoRoot = (Get-Location).Path

if (-not (Test-Path ".env")) {
    Write-Error ".env not found in $RepoRoot -- copy it here manually before running this script (see guide, step 3)."
    exit 1
}

Write-Host "Creating virtual environment..."
py -3.13 -m venv .venv

Write-Host "Installing dependencies (including tzdata, required on Windows for ZoneInfo)..."
& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt

Write-Host "Verifying MT5 connection and timezone handling..."
& ".\.venv\Scripts\python.exe" -c @"
from mt5.connector import MT5Connector
c = MT5Connector()
ok = c.connect()
print('MT5 connect:', ok)
if ok:
    info = c.fetch_account_info()
    print('account:', info.login if hasattr(info, "login") else info)
    c.disconnect()
"@

$DemoBat = Join-Path $RepoRoot "run_live_midnight_fvg_demo.bat"
$PaperBat = Join-Path $RepoRoot "run_live_midnight_fvg_paper.bat"

Write-Host "Registering scheduled tasks (every 2 minutes)..."
schtasks /create /tn "MidnightFVG_NAS100_Demo" /tr "`"$DemoBat`"" /sc MINUTE /mo 2 /st 00:00 /f
schtasks /create /tn "MidnightFVG_NAS100_Paper" /tr "`"$PaperBat`"" /sc MINUTE /mo 2 /st 00:00 /f

Write-Host ""
Write-Host "Done. Test with:"
Write-Host '  schtasks /run /tn "MidnightFVG_NAS100_Paper"'
Write-Host '  Get-Content .\logs\run_live_midnight_fvg.log -Tail 10'
Write-Host ""
Write-Host "IMPORTANT: open the MT5 terminal on this VPS at least once and confirm it is logged into"
Write-Host "the demo account (server: value of MT5_SERVER in .env) before relying on the scheduled tasks."
