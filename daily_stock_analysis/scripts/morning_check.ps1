# ============================================================
# morning_check.ps1 - DSA weekday pre-market check wrapper
# ASCII-only: PowerShell 5.1 reads BOM-less UTF-8 as ANSI on
# zh-CN systems (see backup_ledger.ps1).
# ============================================================
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$LogFile     = Join-Path $ProjectRoot "logs\morning_check_task.log"
$CheckPy     = Join-Path $PSScriptRoot "morning_check.py"

if ((Test-Path $LogFile) -and ((Get-Item $LogFile).Length -gt 5MB)) {
    Move-Item -Force $LogFile ($LogFile + ".old")
}

$stamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"
New-Item -ItemType Directory -Force -Path (Split-Path $LogFile) | Out-Null
Add-Content -Path $LogFile -Value "$stamp morning-check start"

$ErrorActionPreference = "Continue"
Set-Location $ProjectRoot
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$py = "C:\WINDOWS\py.exe"
if (-not (Test-Path $py)) { $py = "py" }

& $py $CheckPy 2>&1 |
    ForEach-Object { Add-Content -Path $LogFile -Value "$_" -Encoding UTF8 }
$code = $LASTEXITCODE

$ErrorActionPreference = "Stop"
$endStamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"
Add-Content -Path $LogFile -Value "$endStamp morning-check exit=$code"
exit $code
