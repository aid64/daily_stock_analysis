# ============================================================
# compare_deck_shadow_task.ps1 - compare-deck shadow gate wrapper
# Mirrors compare_deck_task.ps1 shape. ASCII-only (PowerShell 5.1
# reads BOM-less UTF-8 as ANSI on zh-CN systems).
# ProjectRoot is derived from $PSScriptRoot (no hard-coded path).
#
# Scheduled task (advances the 3-trading-day gate, read-only):
#   schtasks /Create /TN "DSA_Shadow_Compare" /TR "powershell -NoProfile -ExecutionPolicy Bypass -File <repo>\daily_stock_analysis\scripts\compare_deck_shadow_task.ps1" /SC WEEKLY /D THU,FRI /ST 15:40 /F
#
# The wrapper disables its own task once 3 trading days are archived,
# so it cannot keep running after the gate is satisfied. It never
# enables DSA_Deck_Intraday (that stays user-gated).
# ============================================================
$ErrorActionPreference = "Stop"
$TaskName    = "DSA_Shadow_Compare"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$LogFile     = Join-Path $ProjectRoot "logs\compare_deck_shadow_task.log"
$ShadowPy    = Join-Path $ProjectRoot "scripts\compare_deck_shadow.py"
$RequireDays = 3

# log rotation: > 5MB -> .old
if ((Test-Path $LogFile) -and ((Get-Item $LogFile).Length -gt 5MB)) {
    Move-Item -Force $LogFile ($LogFile + ".old")
}

$stamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"
New-Item -ItemType Directory -Force -Path (Split-Path $LogFile) | Out-Null
Add-Content -Path $LogFile -Value "$stamp shadow-run start"

$ErrorActionPreference = "Continue"
$env:PYTHONIOENCODING = "utf-8"
$py = "C:\WINDOWS\py.exe"
if (-not (Test-Path $py)) { $py = "py" }

Push-Location $ProjectRoot
& $py $ShadowPy 2>&1 | ForEach-Object { Add-Content -Path $LogFile -Value "$_" -Encoding UTF8 }
$code = $LASTEXITCODE

# self-limit: disable the task once the shadow gate is satisfied
$count = & $py -c "from scripts.compare_deck_shadow import read_log, progress; print(progress(read_log())['count'])" 2>&1 | Select-Object -Last 1
$n = 0
if ([int]::TryParse(("$count").Trim(), [ref]$n) -and ($n -ge $RequireDays)) {
    schtasks /Change /TN $TaskName /DISABLE 2>&1 |
        ForEach-Object { Add-Content -Path $LogFile -Value "$stamp self-disable: $_" -Encoding UTF8 }
    Add-Content -Path $LogFile -Value "$stamp gate reached $n/$RequireDays -> task disabled"
}
Pop-Location

$ErrorActionPreference = "Stop"
Add-Content -Path $LogFile -Value "$stamp shadow-run exit=$code"
exit $code
