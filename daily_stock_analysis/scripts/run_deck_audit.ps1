# ============================================================
# run_deck_audit.ps1 - DSA_Deck_Audit scheduled-task wrapper
#
# Read-only post-close audit: run compare_deck_audit.py and append
# its UTF-8 output to logs/compare_deck_audit.log. No ledger writes,
# no notifications. Exit code is propagated to Task Scheduler.
#
# Keep this file ASCII-only (PowerShell 5.1 reads BOM-less UTF-8
# as ANSI/GBK on zh-CN systems; see backup_ledger.ps1).
# ============================================================
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$LogFile     = Join-Path $ProjectRoot "logs\compare_deck_audit.log"
$AuditPy     = Join-Path $PSScriptRoot "compare_deck_audit.py"
$LogDir      = Split-Path -Parent $LogFile

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}
$stamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"
Add-Content -Path $LogFile -Value "$stamp audit start" -Encoding UTF8

# Audit prints Chinese to stdout; PowerShell 5.1 decodes native output
# with the console codepage (GBK here), so force UTF-8 before the pipe.
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

# Keep the native pipe alive on stderr and use $LASTEXITCODE as the criterion.
$ErrorActionPreference = "Continue"
& C:\WINDOWS\py.exe -X utf8 $AuditPy 2>&1 |
    ForEach-Object { Add-Content -Path $LogFile -Value "$_" -Encoding UTF8 }
$code = $LASTEXITCODE
$ErrorActionPreference = "Stop"

Add-Content -Path $LogFile -Value "$stamp audit exit=$code" -Encoding UTF8
exit $code
