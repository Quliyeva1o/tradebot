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

    This derives the whole set from the run_live_orb_*.bat and run_live_fvg_*.bat
    files actually present, so the tasks can never drift from the launchers.

    The live bots do NOT need data/history/*.csv (~1GB): they fetch bars from
    MT5 directly. Only the backtest and analysis scripts read those files, so a
    trading-only VPS can skip them entirely.

    Since 2026-09-22 one machine can carry two checkouts, one per broker
    (C:\tradebot on CFI, C:\tradebot_fp on FundingPips), whose launchers share
    every name. So each checkout's tasks go in a Task Scheduler folder named
    after the broker its .env resolves to -- \tradebot\cfi\, \tradebot\fundingpips\
    -- and this script only ever enables, disables or replaces tasks in its own.
    Tasks registered before that lived in the root folder; the ones that run
    THIS checkout's launchers are moved into the folder as they are replaced.

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

$RepoPath = (Resolve-Path $RepoPath).Path
$vbs = Join-Path $RepoPath 'run_hidden.vbs'
if (-not (Test-Path $vbs)) { throw "run_hidden.vbs tapilmadi: $vbs -- RepoPath duzgundurmu?" }

$bats = @(Get-ChildItem -Path $RepoPath -Filter 'run_live_orb_*.bat') +
        @(Get-ChildItem -Path $RepoPath -Filter 'run_live_fvg_*.bat') | Sort-Object Name
if ($PaperOnly) { $bats = $bats | Where-Object { $_.Name -like '*_paper.bat' } }
if (-not $bats) { throw "run_live_orb_*.bat / run_live_fvg_*.bat tapilmadi: $RepoPath" }

function Get-TaskNameFromBat {
    # run_live_orb_breakout_xauusd_demo.bat -> OrbBreakout_XAUUSD_Demo
    # run_live_fvg_window_ndx100_paper.bat  -> FvgWindow_NDX100_Paper
    param([string]$Name)
    $stem = $Name -replace '^run_live_', '' -replace '\.bat$', ''
    $parts = $stem -split '_'
    if ($parts.Count -ne 4) { return $null }
    $cap = { param($s) $s.Substring(0,1).ToUpper() + $s.Substring(1) }
    return "$(& $cap $parts[0])$(& $cap $parts[1])`_$($parts[2].ToUpper())`_$(& $cap $parts[3])"
}

# --- which broker this checkout trades ---------------------------------------
# Resolved before anything is registered: it names the folder the tasks go in.
# It comes from this checkout's own .env via the same module the bots use --
# never guessed, and never duplicated in PowerShell. With no broker there is no
# folder, and every launcher here would refuse to start anyway, so stop.
$python = Join-Path $RepoPath '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    throw ".venv tapilmadi: $python -- evvelce venv qurun, .env-i doldurun, preflight.py isledin"
}
Push-Location $RepoPath
$prevPref = $ErrorActionPreference
$ErrorActionPreference = 'Continue'   # python's stderr is the message, not a reason to throw
try {
    $out = @(& $python -c "import config.brokers as b; print(b.local().name)" 2>&1)
    $code = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $prevPref
    Pop-Location
}
if ($code -ne 0 -or $out.Count -eq 0) {
    throw ("Brokeri teyin etmek olmadi (.env MT5_SERVER?) -- hec bir task qurulmadi. config.brokers: " +
           ($out -join ' '))
}
$broker = "$($out[-1])".Trim()
$taskPath = "\tradebot\$broker\"

# Tasks registered before 2026-09-22 sit in the root folder. Only the ones whose
# action runs THIS checkout's launcher are ours to move; the quotes make
# C:\tradebot never match C:\tradebot_fp.
$vbsArg = '"{0}"' -f $vbs
$legacy = @{}
Get-ScheduledTask -TaskPath '\' -ErrorAction SilentlyContinue |
    Where-Object {
        $_.TaskName -match '^(Orb|Fvg)' -and
        (($_.Actions | ForEach-Object { $_.Arguments }) -join ' ').IndexOf(
            $vbsArg, [StringComparison]::OrdinalIgnoreCase) -ge 0
    } |
    ForEach-Object { $legacy[$_.TaskName] = $_ }

Write-Host "Repo    : $RepoPath"
Write-Host "Broker  : $broker (.env MT5_SERVER) -> Task Scheduler qovlugu $taskPath"
Write-Host "Kadans  : her $IntervalMinutes deqiqe"
Write-Host "Tapildi : $($bats.Count) launcher`n"

$made = 0
foreach ($bat in $bats) {
    $taskName = Get-TaskNameFromBat $bat.Name
    if (-not $taskName) {
        Write-Warning "Adi tanimadim, atlanir: $($bat.Name)"
        continue
    }

    # Old root task first, new one after: the new trigger starts a minute out,
    # so the two never poll the same launcher's state file side by side.
    if ($legacy.ContainsKey($taskName)) {
        if ($PSCmdlet.ShouldProcess("\$taskName", 'Unregister-ScheduledTask (koke qeydiyyat, qovluga kocur)')) {
            Unregister-ScheduledTask -TaskName $taskName -TaskPath '\' -Confirm:$false
            Write-Host ("  kocdu   \{0} -> {1}" -f $taskName, $taskPath)
        }
        $legacy.Remove($taskName)
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

    if ($PSCmdlet.ShouldProcess("$taskPath$taskName", 'Register-ScheduledTask')) {
        Register-ScheduledTask -TaskName $taskName -TaskPath $taskPath -Action $action -Trigger $trigger `
            -Settings $settings -Force | Out-Null
        Write-Host ("  {0,-28} <- {1}" -f $taskName, $bat.Name)
        $made++
    } else {
        Write-Host ("  [WhatIf] {0,-28} <- {1}" -f $taskName, $bat.Name)
    }
}

Write-Host "`n$made task qeydiyyatdan kecdi."

# --- enforce one Demo bot per symbol, on the right account -------------------
# A .bat exists for every strategy/symbol pair, so the loop above registers a
# Demo task for all of them -- which puts TWO on GER40, JP225 and XAUUSD. Two
# bots on one symbol do not share: _partition_positions() reads the other's
# position as foreign and refuses to enter, so one wins and the other logs
# foreign_position_blocks_entry indefinitely. deploy/demo_roster.txt records
# which one owns each symbol and why; anything absent from it is disabled here.
#
# Since 2026-09-21 the roster also names the ACCOUNT each permission belongs to.
# A Demo bot listed for the other broker is disabled here, because the sizing and
# stop rule behind it were measured on that broker's spread, swap and lot
# minimum. Everything below acts on THIS checkout's folder only ($taskPath):
# since 2026-09-22 the other broker's checkout may share the machine, and its
# tasks carry the same names. Before that, this loop ran over every task on the
# machine and would have switched the other broker's live bot off.
if ($legacy.Count -gt 0) {
    Write-Warning ("Kokde bu checkout-un {0} kohne taski qaldi (-PaperOnly ve ya .bat-i silinib), toxunulmadi: {1}" -f
        $legacy.Count, (($legacy.Keys | Sort-Object) -join ', '))
}

$rosterFile = Join-Path $PSScriptRoot 'demo_roster.txt'
if (-not (Test-Path $rosterFile)) { $rosterFile = Join-Path $RepoPath 'deploy\demo_roster.txt' }

if (Test-Path $rosterFile) {
    $roster = @(Get-Content $rosterFile |
        ForEach-Object { ($_ -split '#')[0].Trim() } |
        Where-Object { $_ } |
        ForEach-Object {
            $parts = $_ -split '\s+'
            if ($parts.Count -lt 2) {
                throw "demo_roster.txt: '$($parts[0])' brokeri gostermir -- '$($parts[0]) cfi' seklinde yazin"
            }
            if ($parts[1] -eq $broker) { $parts[0] }
        })
    Write-Host "`nDemo roster ($broker, $taskPath -- $($roster.Count) simvol sahibi):"

    $demoTasks = @(Get-ScheduledTask -TaskPath $taskPath -ErrorAction SilentlyContinue |
        Where-Object { $_.TaskName -like '*_Demo' })
    foreach ($t in $demoTasks) {
        $wanted = $roster -contains $t.TaskName
        if ($PSCmdlet.ShouldProcess("$taskPath$($t.TaskName)", $(if ($wanted) { 'Enable' } else { 'Disable' }))) {
            if ($wanted) {
                Enable-ScheduledTask -TaskName $t.TaskName -TaskPath $taskPath | Out-Null
                Write-Host ("  ACIQ    {0}" -f $t.TaskName)
            } else {
                Disable-ScheduledTask -TaskName $t.TaskName -TaskPath $taskPath | Out-Null
                Write-Host ("  sondu   {0}  (bu hesabin rosterinde yoxdur -- basqa bot ve ya basqa broker)" -f $t.TaskName)
            }
        }
    }

    # Same symbol on the OTHER broker's folder is a different account, not a collision.
    $dupes = Get-ScheduledTask -TaskPath $taskPath -ErrorAction SilentlyContinue |
        Where-Object { $_.TaskName -like '*_Demo' -and $_.State -ne 'Disabled' } |
        Group-Object { ($_.TaskName -split '_')[1] } |
        Where-Object { $_.Count -gt 1 }
    if ($dupes) {
        Write-Warning ("BIR SIMVOLDA IKI DEMO BOT: " +
            (($dupes | ForEach-Object { "$($_.Name) x$($_.Count)" }) -join ', '))
    } else {
        Write-Host "  -> her simvolda tam bir Demo bot, toqqusma yoxdur"
    }
} else {
    Write-Warning "demo_roster.txt tapilmadi -- Demo tasklar el ile yoxlanmalidir"
}
Write-Host @"

BUNDAN SONRA, SIRA ILE:
  0. Temiz Windows-da .ps1 bloklanir. Bu skripti bele isledin:
       powershell.exe -ExecutionPolicy Bypass -File .\deploy\install_tasks.ps1 -PaperOnly
  1. MT5 terminalini qurasdirin, hesaba girin, ve Alqoritmik Ticareti ACIN
     (Ctrl+E). Bagli qalarsa real orderler sessizce retcode 10027 ile redd
     olunur -- bu layihede artiq bir defe bas verib.
  2. .env faylini kopyalayin (git-de yoxdur: MT5_LOGIN/PASSWORD/SERVER/PATH).
     Bir masinda iki broker varsa, MT5_PATH MUTLEQDIR ve bu checkout-un OZ
     terminalini gostermelidir (C:\tradebot -> CFI, C:\tradebot_fp -> FundingPips).
  3. python -m venv .venv
     .venv\Scripts\pip install -r deploy\requirements-live.txt
       (tam requirements.txt YOX -- pytest/matplotlib serverde islenmir)
  4. Hazir olub-olmadigini yoxlayin -- hec ne deyismir, yalniz oxuyur:
       .venv\Scripts\python.exe deploy\preflight.py
  5. Demo tasklar deploy\demo_roster.txt-e gore acilir/sondurulur -- ancaq
     bu checkout-un brokerine aid olanlar (.env MT5_SERVER), oz qovlugunda
     ($taskPath). FundingPips-de Demo tasklarin hamisi bagli qalir, paper
     botlar isleyir. Simvolun sahibini deyismek ucun HEMIN FAYLI
     redakte edin, Task Scheduler-i el ile deyil -- yoxsa novbeti qurulusda
     geri qayidir.
  6. Yoxlayin: logs\run_live_nasdaq_orb.log, logs\run_live_first_fvg_window.log ve
       Get-ScheduledTask -TaskPath '$taskPath' |
         % { '{0} {1}' -f `$_.TaskName, (`$_ | Get-ScheduledTaskInfo).LastTaskResult }
     Hamisinin neticesi 0 olmalidir.

QEYD: yeni masinda risk\ qovlugundaki paper state ve kill-switch fayllarini
KOPYALAMAYIN -- kohne hesabin equity baseline-i yeni masinda yalanci kill-switch
tetikleye biler. Bos baslasin.
"@
