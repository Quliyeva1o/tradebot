<#
.SYNOPSIS
    Registers the ICT signal bot: one resident watcher per MT5 feed (FundingPips and CFI).

.DESCRIPTION
    Tasks (folder \tradebot\ict\):
        ict_signals      run_ict_signals.bat      FundingPips feed - DISABLED since 2026-10-08 (kept for reference)
        ict_signals_cfi  run_ict_signals_cfi.bat  python -m ict_lab.live --loop --feed cfi   CFI terminal, talks to Telegram
    Both go through run_hidden.vbs (no console flash). Each loop scans the moment a new minute closes. The task
    trigger (every 5 minutes) is only a watchdog: while the loop runs MultipleInstances IgnoreNew drops the extra
    start, and if the loop dies the next trigger brings it back.
    The bot only READS the terminals and sends Telegram messages; it places no orders.
    Remove with:  Unregister-ScheduledTask -TaskPath '\tradebot\ict\' -TaskName <name> -Confirm:$false
#>
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
# Since 2026-10-08 only the CFI feed is live (it talks to Telegram). The FundingPips task is left DISABLED, not removed.
# 2026-10-09 (the user's call): ict_exec_cfi places the A+ setups as real orders on the CFI DEMO account, US100 only.
$bats = @{ 'ict_signals_cfi' = 'run_ict_signals_cfi.bat'; 'ict_exec_cfi' = 'run_ict_execute_demo_cfi.bat' }
foreach ($name in $bats.Keys) {
    $action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument ('"{0}\run_hidden.vbs" "{0}\{1}"' -f $repo, $bats[$name]) -WorkingDirectory $repo
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 5)
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero)
    Register-ScheduledTask -TaskPath '\tradebot\ict\' -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
}
Get-ScheduledTask -TaskPath '\tradebot\ict\' | Select-Object TaskPath, TaskName, State
