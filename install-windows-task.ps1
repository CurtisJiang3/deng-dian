param(
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'
$taskName = 'TiboFeishuNotifier'
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path

if ($Remove) {
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existing) {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Output "Removed scheduled task $taskName"
    }
    return
}

$envPath = Join-Path $projectDir '.env'
if (-not (Test-Path -LiteralPath $envPath)) {
    throw 'Create .env from .env.example and fill FEISHU_WEBHOOK_URL first.'
}
$webhookLine = Get-Content -LiteralPath $envPath | Where-Object { $_ -match '^FEISHU_WEBHOOK_URL=' } | Select-Object -First 1
if ($webhookLine -notmatch '^FEISHU_WEBHOOK_URL=\S+') {
    throw 'FEISHU_WEBHOOK_URL is empty in .env.'
}
$intervalLine = Get-Content -LiteralPath $envPath | Where-Object { $_ -match '^POLL_MINUTES=' } | Select-Object -First 1
$intervalMinutes = 5
if ($intervalLine) {
    $intervalMinutes = [int]($intervalLine -replace '^POLL_MINUTES=', '')
}
if ($intervalMinutes -lt 1 -or $intervalMinutes -gt 1440) {
    throw 'POLL_MINUTES must be between 1 and 1440.'
}

$python = (Get-Command python -ErrorAction Stop).Source
$script = Join-Path $projectDir 'tibo_feishu.py'
$action = New-ScheduledTaskAction -Execute $python -Argument ('"{0}" once' -f $script) -WorkingDirectory $projectDir
$currentUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $intervalMinutes) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 2)
$principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'Poll AIHOT Tibo reset updates and notify Feishu' -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Write-Output "Installed scheduled task $taskName for $currentUser; repeats every $intervalMinutes minutes while logged in"
