# ============================================================
# compare_deck_task.ps1 - 多策略对比盘计划任务包装
# 对齐 track_etf_momentum.ps1 形态。ASCII-only (PowerShell 5.1
# reads BOM-less UTF-8 as ANSI on zh-CN systems).
# Scheduled tasks:
#   schtasks /Create /TN "DSA_Deck_Intraday" /TR "powershell -NoProfile -ExecutionPolicy Bypass -File D:\daily_stock_analysis\daily_stock_analysis\scripts\compare_deck_task.ps1 -Mode intraday" /SC MINUTE /MO 20 /F
# ============================================================
param(
    [Parameter(Mandatory=$true)][ValidateSet("intraday","close")][string]$Mode
)
$ErrorActionPreference = "Stop"
$ProjectRoot = "D:\daily_stock_analysis\daily_stock_analysis"
$LogFile     = Join-Path $ProjectRoot "logs\compare_deck_task.log"
$RunnerPy    = Join-Path $ProjectRoot "scripts\compare_deck_runner.py"

# log rotation: > 5MB -> .old
if ((Test-Path $LogFile) -and ((Get-Item $LogFile).Length -gt 5MB)) {
    Move-Item -Force $LogFile ($LogFile + ".old")
}

$stamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"
New-Item -ItemType Directory -Force -Path (Split-Path $LogFile) | Out-Null
Add-Content -Path $LogFile -Value "$stamp $Mode start"

$ErrorActionPreference = "Continue"
$env:PYTHONIOENCODING = "utf-8"
$py = "C:\WINDOWS\py.exe"
if (-not (Test-Path $py)) { $py = "py" }

# WakeToRun only starts the hidden child (run_hidden.vbs returns immediately),
# so hold an execution-state request for the whole run to keep the machine awake.
Add-Type -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint esFlags);' `
    -Name DeckPower -Namespace Win32 | Out-Null
$ES_CONTINUOUS = [uint32]2147483648
$ES_SYSTEM_REQUIRED = [uint32]1
[void][Win32.DeckPower]::SetThreadExecutionState([uint32]($ES_CONTINUOUS -bor $ES_SYSTEM_REQUIRED))
$code = 1
try {
    & $py $RunnerPy --mode $Mode 2>&1 |
        ForEach-Object { Add-Content -Path $LogFile -Value "$_" -Encoding UTF8 }
    $code = $LASTEXITCODE
}
finally {
    [void][Win32.DeckPower]::SetThreadExecutionState($ES_CONTINUOUS)
}
$ErrorActionPreference = "Stop"

Add-Content -Path $LogFile -Value "$stamp $Mode exit=$code"
exit $code
