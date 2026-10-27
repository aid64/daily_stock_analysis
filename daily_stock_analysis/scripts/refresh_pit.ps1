# ============================================================
# refresh_pit.ps1 - PIT tail refresh wrapper (active daily / full weekly)
# ASCII-only (PowerShell 5.1 reads BOM-less UTF-8 as ANSI on zh-CN).
# ============================================================
param(
    [ValidateSet("active","full")][string]$Scope = "active"
)
$ErrorActionPreference = "Stop"
$ProjectRoot = "D:\daily_stock_analysis\daily_stock_analysis"
$QuantRoot   = "D:\daily_stock_analysis\quant_research"
$LogFile     = Join-Path $ProjectRoot "logs\pit_refresh.log"
$TailPy      = Join-Path $QuantRoot "scripts\update_pit_tail.py"
$AssemblePy  = Join-Path $QuantRoot "scripts\build_pit_snapshot.py"

if ((Test-Path $LogFile) -and ((Get-Item $LogFile).Length -gt 5MB)) {
    Move-Item -Force $LogFile ($LogFile + ".old")
}
$stamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"
New-Item -ItemType Directory -Force -Path (Split-Path $LogFile) | Out-Null
Add-Content -Path $LogFile -Value "$stamp pit-refresh start scope=$Scope"

$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$py = "C:\WINDOWS\py.exe"
if (-not (Test-Path $py)) { $py = "py" }

Add-Type -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint esFlags);' `
    -Name PitPower -Namespace Win32 | Out-Null
$ES_CONTINUOUS = [uint32]2147483648
$ES_SYSTEM_REQUIRED = [uint32]1
[void][Win32.PitPower]::SetThreadExecutionState([uint32]($ES_CONTINUOUS -bor $ES_SYSTEM_REQUIRED))

$code = 1
try {
    Set-Location $QuantRoot
    if ($Scope -eq "full") {
        & $py $TailPy --time-budget 9000 2>&1 |
            ForEach-Object { Add-Content -Path $LogFile -Value "$_" -Encoding UTF8 }
    }
    else {
        & $py $TailPy --active-only --time-budget 2700 2>&1 |
            ForEach-Object { Add-Content -Path $LogFile -Value "$_" -Encoding UTF8 }
    }
    $code = $LASTEXITCODE
    if ($code -eq 0) {
        & $py $AssemblePy --assemble-only --start 2018 2>&1 |
            ForEach-Object { Add-Content -Path $LogFile -Value "$_" -Encoding UTF8 }
        $code = $LASTEXITCODE
    }
}
finally {
    [void][Win32.PitPower]::SetThreadExecutionState($ES_CONTINUOUS)
}

$endStamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"
Add-Content -Path $LogFile -Value "$endStamp pit-refresh exit=$code"
exit $code
