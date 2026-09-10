<#
.SYNOPSIS
    Register (or remove) the Syncguard agent's logon scheduled task.

.DESCRIPTION
    A LOGON SCHEDULED TASK, NOT A WINDOWS SERVICE. This is not a preference:

      * Session 0 isolation means a LocalSystem service cannot start an
        interactive Revit at all.
      * Revit's licence and the Autodesk sign-in both live in the user profile,
        so the agent has to run as the signed-in user.

    Runs on the CPython that pyRevit already ships, so there is no second
    runtime to install. `pythonw.exe` is used in tray mode so no console window
    appears; headless mode uses `python.exe` so its output can be captured.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File agent\install_task.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File agent\install_task.ps1 -Headless

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File agent\install_task.ps1 -Uninstall
#>
[CmdletBinding()]
param(
    [switch]$Headless,
    [switch]$Uninstall,
    [string]$BaseUrl,
    [string]$TaskName = 'EasyBIM Syncguard Agent'
)

$ErrorActionPreference = 'Stop'

if ($Uninstall) {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed scheduled task '$TaskName'."
    } else {
        Write-Host "No scheduled task named '$TaskName'."
    }
    return
}

$agentDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$entry = Join-Path $agentDir 'syncguard_agent.py'
if (-not (Test-Path $entry)) { throw "Cannot find $entry" }

# Prefer the interpreter pyRevit ships. Globbed rather than pinned to CPY3123
# so a pyRevit upgrade that bumps the engine folder does not break the task.
$exeName = if ($Headless) { 'python.exe' } else { 'pythonw.exe' }
$candidates = @()
$candidates += Get-ChildItem -Path 'C:\Program Files\pyRevit*\bin\cengines\CPY*' -Filter $exeName -Recurse -ErrorAction SilentlyContinue |
    Sort-Object FullName -Descending | Select-Object -ExpandProperty FullName

if (-not $candidates) {
    $fallback = (Get-Command $exeName -ErrorAction SilentlyContinue).Source
    if ($fallback) { $candidates = @($fallback) }
}
if (-not $candidates) {
    throw "Could not find $exeName. Install pyRevit, or install Python 3 and put it on PATH."
}
$python = $candidates[0]
Write-Host "Interpreter: $python"

if ($BaseUrl) {
    & $python $entry --base-url $BaseUrl | Out-Host
}

$arguments = "`"$entry`""
if ($Headless) { $arguments += ' --headless' }

$action = New-ScheduledTaskAction -Execute $python -Argument $arguments -WorkingDirectory $agentDir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

# ExecutionTimeLimit 0 = never kill it; the agent is long-lived and enforces its
# own phase-aware timeouts on Revit. IgnoreNew because a second agent would
# claim runs in parallel and two Revits would fight over the same model.
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5)

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings `
    -Description 'Runs the EasyBIM Syncguard agent, which syncs cloud coordination models on request from EasyBIM.' | Out-Null

Write-Host "Registered '$TaskName' to start at logon."
Write-Host ''
Write-Host 'Starting it now...'
Start-ScheduledTask -TaskName $TaskName
Write-Host ''
if ($Headless) {
    Write-Host 'Headless mode. Enroll with:'
    Write-Host "  `"$python`" `"$entry`" --enroll <CODE>"
} else {
    Write-Host 'Look for the Syncguard icon in the notification area (bottom-right).'
    Write-Host 'It will be grey until you connect it: click it and choose'
    Write-Host '"Connect to EasyBIM...", then type the code from the EasyBIM website.'
}
