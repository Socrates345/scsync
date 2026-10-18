#Requires -RunAsAdministrator
<#
.SYNOPSIS
Registers (or updates) the "scdl daily sync" scheduled task.

.DESCRIPTION
Runs "src\sync_sc_playlists.py --unattended" every day at 18:00 (local time),
hidden, as you, even when you are logged out (S4U: no password is stored).
If the laptop is asleep or off at 18:00, the run starts as soon as it is back.
Run it again at any time to update the task.

.EXAMPLE
# from an admin PowerShell, in the repo root:
powershell -ExecutionPolicy Bypass -File deploy\windows\register-task.ps1
#>
param(
    [string]$User = "$env:USERDOMAIN\$env:USERNAME",
    [string]$At = '18:00',
    [string]$TaskName = 'scdl daily sync'
)

$ErrorActionPreference = 'Stop'

$repo   = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$python = Join-Path $repo '.venv\Scripts\python.exe'
$script = Join-Path $repo 'src\sync_sc_playlists.py'
if (-not (Test-Path $python)) { throw "Python venv not found at $python - run 'uv sync' in $repo first." }
if (-not (Test-Path $script)) { throw "Sync script not found at $script" }

$envFile = Join-Path $repo '.env'
if (-not ((Test-Path $envFile) -and (Select-String -Path $envFile -Pattern '^\s*SC_NOTIFY_URL\s*=\s*\S' -Quiet))) {
    Write-Warning "No SC_NOTIFY_URL in $envFile - runs will work but send no alerts. See .env.example."
}

$action    = New-ScheduledTaskAction -Execute $python -Argument "`"$script`" --unattended" -WorkingDirectory $repo
$trigger   = New-ScheduledTaskTrigger -Daily -At $At
$settings  = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3) `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $User -LogonType S4U -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Force `
    -Description 'Daily SoundCloud playlist sync (sync_sc_playlists.py --unattended). Logs in the repo logs folder.' | Out-Null

Write-Host "Registered '$TaskName' for $User - daily at $At, hidden, catches up if missed."
Write-Host ""
Write-Host "  Run now : Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "  Status  : Get-ScheduledTaskInfo -TaskName '$TaskName' | Select-Object LastRunTime, LastTaskResult, NextRunTime"
Write-Host "            LastTaskResult: 0 = all good, 1 = needs attention (see alert/log), 75 = another sync was running,"
Write-Host "            267009 = still running"
Write-Host "  Logs    : $repo\logs"
Write-Host "  Remove  : Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
