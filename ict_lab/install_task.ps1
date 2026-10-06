<#
.SYNOPSIS
    Registers the ICT signal bot as one Scheduled Task, polling every 5 minutes.

.DESCRIPTION
    Task: \tradebot\ict\ict_signals. It runs run_ict_signals.bat through run_hidden.vbs (no console flash).
    The bot only READS the attached MT5 terminal and sends Telegram messages; it places no orders.
    Remove it with:  Unregister-ScheduledTask -TaskPath '\tradebot\ict\' -TaskName ict_signals -Confirm:$false
#>
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument ('"{0}\run_hidden.vbs" "{0}\run_ict_signals.bat"' -f $repo) -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 5)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 4)
Register-ScheduledTask -TaskPath '\tradebot\ict\' -TaskName 'ict_signals' -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Get-ScheduledTask -TaskPath '\tradebot\ict\' -TaskName 'ict_signals' | Select-Object TaskPath, TaskName, State
