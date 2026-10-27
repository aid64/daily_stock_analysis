# ============================================================
# precompute_deck.ps1 - precompute H engine target before close
# ASCII-only (PowerShell 5.1 reads BOM-less UTF-8 as ANSI on zh-CN).
# ============================================================
$ErrorActionPreference = "Stop"
$ProjectRoot = "D:\daily_stock_analysis\daily_stock_analysis"
$LogFile     = Join-Path $ProjectRoot "logs\deck_precompute.log"
$ScriptPy    = Join-Path $ProjectRoot "scripts\precompute_deck_signals.py"

if ((Test-Path $LogFile) -and ((Get-Item $LogFile).Length -gt 5MB)) {
    Move-Item -Force $LogFile ($LogFile + ".old")
}
$stamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"
New-Item -ItemType Directory -Force -Path (Split-Path $LogFile) | Out-Null
Add-Content -Path $LogFile -Value "$stamp precompute start"

$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$py = "C:\WINDOWS\py.exe"
if (-not (Test-Path $py)) { $py = "py" }

Add-Type -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint esFlags);' `
    -Name PrecomputePower -Namespace Win32 | Out-Null
$ES_CONTINUOUS = [uint32]2147483648
$ES_SYSTEM_REQUIRED = [uint32]1
[void][Win32.PrecomputePower]::SetThreadExecutionState([uint32]($ES_CONTINUOUS -bor $ES_SYSTEM_REQUIRED))

$code = 1
try {
    # Python logging writes INFO to stderr; Stop would turn native stderr
    # into a terminating NativeCommandError before $LASTEXITCODE is read.
    $ErrorActionPreference = "Continue"
    Set-Location $ProjectRoot
    & $py $ScriptPy --strategy etf_mwu_cb `
        --strategy-config config/strategy/etf_mwu_cb_monthly.yaml --timeout 3600 2>&1 |
        ForEach-Object { Add-Content -Path $LogFile -Value "$_" -Encoding UTF8 }
    $code = $LASTEXITCODE
}
finally {
    [void][Win32.PrecomputePower]::SetThreadExecutionState($ES_CONTINUOUS)
}

$endStamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"
Add-Content -Path $LogFile -Value "$endStamp precompute exit=$code"
exit $code
