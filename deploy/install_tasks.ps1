<#
.SYNOPSIS
    Recreates every live-bot Scheduled Task on a fresh machine (VPS migration).

.DESCRIPTION
    The bots themselves are portable -- no .py, .bat or .vbs file in this repo
    contains an absolute path, and the launchers all start with `cd /d "%~dp0"`.
    The Scheduled Tasks are the exception: their actions hard-code the repo's
    location, so they are the one thing that cannot simply be copied to a new
    machine. Hand-creating sixteen of them through the GUI is slow and is
    exactly where a wrong argument slips in unnoticed -- which has already
    happened once in this project, producing tasks that ran with a missing .bat
    argument.

    This derives the whole set from the run_live_orb_*.bat files actually
    present, so the tasks can never drift from the launchers.

    The live bots do NOT need data/history/*.csv (~1GB): they fetch bars from
    MT5 directly. Only the backtest and analysis scripts read those files, so a
    trading-only VPS can skip them entirely.

.PARAMETER RepoPath
    Where the repo lives on THIS machine. Defaults to the script's own parent.

.PARAMETER IntervalMinutes
    Poll cadence. 2 matches the existing deployment. Do not raise it above 4
    without re-reading SIGNAL_GRACE_MINUTES in run_live_nasdaq_orb.py: the
    grace window is what stops a breakout being missed between polls, and it is
    sized for a 2-minute cadence.

.PARAMETER PaperOnly
    Register only the *_paper tasks. Sensible for a first run on a new box --
    prove the plumbing works before anything can place a real order.

.PARAMETER WhatIf
    Print what would be registered and change nothing.

.NOTES
    A clean Windows install refuses to run .ps1 files at all (ExecutionPolicy
    Restricted), so on a fresh VPS this script cannot start until that is
    lifted for the session:

        powershell.exe -ExecutionPolicy Bypass -File .\deploy\install_tasks.ps1 -PaperOnly

    Prefer the per-invocation form above over changing the machine policy.

.EXAMPLE
    powershell.exe -ExecutionPolicy Bypass -File .\deploy\install_tasks.ps1 -PaperOnly
    powershell.exe -ExecutionPolicy Bypass -File .\deploy\install_tasks.ps1
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$RepoPath,
    [int]$IntervalMinutes = 2,
    [switch]$PaperOnly
)

$ErrorActionPreference = 'Stop'

# Resolve the repo AFTER parameter binding, not as a param default.
# $PSScriptRoot is empty while defaults are evaluated under
# `powershell -File script.ps1`, which is exactly how this gets run on a fresh
# VPS -- a clean Windows blocks .ps1 otherwise. Dot-invoking it from an open
# session happens to work, which is why the first version passed here and
# failed there.
if (-not $RepoPath) {
    $scriptDir = if ($PSScriptRoot) {
        $PSScriptRoot
    } elseif ($MyInvocation.MyCommand.Path) {
        Split-Path -Parent $MyInvocation.MyCommand.Path
    } else {
        throw "RepoPath tapilmadi -- -RepoPath C:	radebot seklinde acig verin"
    }
    $RepoPath = Split-Path -Parent $scriptDir
}

$vbs = Join-Path $RepoPath 'run_hidden.vbs'
if (-not (Test-Path $vbs)) { throw "run_hidden.vbs tapilmadi: $vbs -- RepoPath duzgundurmu?" }

$bats = Get-ChildItem -Path $RepoPath -Filter 'run_live_orb_*.bat' | Sort-Object Name
if ($PaperOnly) { $bats = $bats | Where-Object { $_.Name -like '*_paper.bat' } }
if (-not $bats) { throw "run_live_orb_*.bat tapilmadi: $RepoPath" }

function Get-TaskNameFromBat {
    # run_live_orb_breakout_xauusd_demo.bat -> OrbBreakout_XAUUSD_Demo
    param([string]$Name)
    $stem = $Name -replace '^run_live_orb_', '' -replace '\.bat$', ''
    $parts = $stem -split '_'
    if ($parts.Count -ne 3) { return $null }
    $family = $parts[0].Substring(0,1).ToUpper() + $parts[0].Substring(1)
    $mode = $parts[2].Substring(0,1).ToUpper() + $parts[2].Substring(1)
    return "Orb$family`_$($parts[1].ToUpper())`_$mode"
}

Write-Host "Repo    : $RepoPath"
Write-Host "Kadans  : her $IntervalMinutes deqiqe"
Write-Host "Tapildi : $($bats.Count) launcher`n"

$made = 0
foreach ($bat in $bats) {
    $taskName = Get-TaskNameFromBat $bat.Name
    if (-not $taskName) {
        Write-Warning "Adi tanimadim, atlanir: $($bat.Name)"
        continue
    }

    $action = New-ScheduledTaskAction -Execute 'wscript.exe' `
        -Argument ('"{0}" "{1}"' -f $vbs, $bat.FullName)

    # Start a minute out so registration itself never races the first poll.
    $trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(1)) `
        -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
        -RepetitionDuration (New-TimeSpan -Days 3650)

    # IgnoreNew is load-bearing: a poll that overruns its slot must not stack a
    # second copy on top of itself, or two processes race the same paper state
    # file and the same broker position.
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Hours 72) `
        -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -StartWhenAvailable

    if ($PSCmdlet.ShouldProcess($taskName, 'Register-ScheduledTask')) {
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
            -Settings $settings -Force | Out-Null
        Write-Host ("  {0,-28} <- {1}" -f $taskName, $bat.Name)
        $made++
    } else {
        Write-Host ("  [WhatIf] {0,-28} <- {1}" -f $taskName, $bat.Name)
    }
}

Write-Host "`n$made task qeydiyyatdan kecdi."
Write-Host @"

BUNDAN SONRA, SIRA ILE:
  0. Temiz Windows-da .ps1 bloklanir. Bu skripti bele isledin:
       powershell.exe -ExecutionPolicy Bypass -File .\deploy\install_tasks.ps1 -PaperOnly
  1. MT5 terminalini qurasdirin, hesaba girin, ve Alqoritmik Ticareti ACIN
     (Ctrl+E). Bagli qalarsa real orderler sessizce retcode 10027 ile redd
     olunur -- bu layihede artiq bir defe bas verib.
  2. .env faylini kopyalayin (git-de yoxdur: MT5_LOGIN/PASSWORD/SERVER/PATH).
  3. python -m venv .venv;  .venv\Scripts\pip install -r requirements.txt
  4. Yalniz PAPER tasklari acin, bir sessiya izleyin, sonra Demo-lari acin:
       Get-ScheduledTask -TaskName 'Orb*_Demo' | Enable-ScheduledTask
  5. Yoxlayin: logs\run_live_nasdaq_orb.log ve
       Get-ScheduledTask | ? TaskName -match '^Orb' |
         % { '{0} {1}' -f `$_.TaskName, (`$_ | Get-ScheduledTaskInfo).LastTaskResult }
     Hamisinin neticesi 0 olmalidir.

QEYD: yeni masinda risk\ qovlugundaki paper state ve kill-switch fayllarini
KOPYALAMAYIN -- kohne hesabin equity baseline-i yeni masinda yalanci kill-switch
tetikleye biler. Bos baslasin.
"@
