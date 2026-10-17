# ============================================================
# compare_deck_shadow_task.ps1 - compare-deck shadow gate wrapper
# Mirrors compare_deck_task.ps1 shape. ASCII-only (PowerShell 5.1
# reads BOM-less UTF-8 as ANSI on zh-CN systems).
# ProjectRoot is derived from $PSScriptRoot (no hard-coded path).
#
# Scheduled task (advances the 3-trading-day gate, read-only):
#   schtasks /Create /TN "DSA_Shadow_Compare" /TR "powershell -NoProfile -ExecutionPolicy Bypass -File <repo>\daily_stock_analysis\scripts\compare_deck_shadow_task.ps1" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 15:40 /F
# (Every trading weekday, not only THU/FRI: the 10-15 run was missed because
# the machine was off at the single weekly slot; a daily slot means any
# weekday boot can advance the gate. Missed starts catch up via
# StartWhenAvailable, still recorded under the same calendar day.)
#
# The wrapper disables its own task once 3 trading days are archived with
# zero FAIL, and at that point it also enables DSA_Deck_Intraday (user
# standing instruction 2026-10-14: finish the last mile autonomously).
# To revert the enable: Disable-ScheduledTask -TaskName DSA_Deck_Intraday
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

# gate bookkeeping: on 3/3 clean, enable the deck task, then disable this one
$stat  = & $py -c "from scripts.compare_deck_shadow import read_log, progress; p = progress(read_log()); print(p['count'], p['fails'])" 2>&1 | Select-Object -Last 1
$parts = ("$stat").Trim() -split '\s+'
$n = 0
$nf = 99
if ($parts.Count -ge 2) {
    [void][int]::TryParse($parts[0], [ref]$n)
    [void][int]::TryParse($parts[1], [ref]$nf)
}
Add-Content -Path $LogFile -Value "$stamp gate: $n/$RequireDays days, fails=$nf"

if (($n -ge $RequireDays) -and ($nf -eq 0)) {
    try {
        Enable-ScheduledTask -TaskName "DSA_Deck_Intraday" -ErrorAction Stop | Out-Null
        Add-Content -Path $LogFile -Value "$stamp gate cleared ($n/$RequireDays, 0 fail) -> DSA_Deck_Intraday enabled"
    } catch {
        Add-Content -Path $LogFile -Value "$stamp enable DSA_Deck_Intraday failed: $_"
    }
    try {
        Disable-ScheduledTask -TaskName $TaskName -ErrorAction Stop | Out-Null
        Add-Content -Path $LogFile -Value "$stamp self-disable: $TaskName disabled after gate cleared"
    } catch {
        Add-Content -Path $LogFile -Value "$stamp self-disable failed: $_"
    }
}
Pop-Location

$ErrorActionPreference = "Stop"
Add-Content -Path $LogFile -Value "$stamp shadow-run exit=$code"
exit $code
