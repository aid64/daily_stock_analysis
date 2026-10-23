# ============================================================
# backup_close.ps1 - DSA_Ledger_CloseBackup scheduled-task wrapper
#
# Post-close full backup into data\backups_close\<date>\ (separate from
# the 08:30 data\backups root). Reuses backup_ledger.py verification and
# retention. Keep this file ASCII-only (PowerShell 5.1 encoding caveat).
# ============================================================
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$LogFile     = Join-Path $ProjectRoot "logs\backup_close.log"
$BackupPy    = Join-Path $PSScriptRoot "backup_close.py"

[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

$stamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"
Add-Content -Path $LogFile -Value "$stamp close-backup start" -Encoding UTF8

$ErrorActionPreference = "Continue"
& C:\WINDOWS\py.exe $BackupPy --retention 14 2>&1 |
    ForEach-Object { Add-Content -Path $LogFile -Value "$_" -Encoding UTF8 }
$code = $LASTEXITCODE
$ErrorActionPreference = "Stop"

Add-Content -Path $LogFile -Value "$stamp close-backup exit=$code" -Encoding UTF8
exit $code
