# ============================================================
# backup_ledger.ps1 - DSA_Ledger_Backup scheduled-task wrapper
# Form aligned with keepalive_server.ps1 (U4 fix: py.exe direct
# invocation failed 0x800710E0 in task context 3 days straight;
# powershell -File wrapper form is proven compatible by the
# DSA_Server_Keepalive task running continuously).
#
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads
# BOM-less UTF-8 scripts as ANSI/GBK on zh-CN systems; a trailing
# non-ASCII char can swallow the newline and merge the NEXT line
# into a '#' comment (observed 2026-09-26: two assignments vanished).
#
# Job: run backup_ledger.py (paths derived from __file__, CWD-free),
#      append file logging (the py script only writes stdout), and
#      propagate the exit code to Task Scheduler
#      (0 ok / 2 main db missing / 3 verify failed / 1 unknown).
# ============================================================
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot      # parent of scripts/
$LogFile     = Join-Path $ProjectRoot "logs\backup_task.log"
$BackupPy    = Join-Path $PSScriptRoot "backup_ledger.py"

$stamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"
Add-Content -Path $LogFile -Value "$stamp backup start"

# The py script logs to stderr; with EAP=Stop the first stderr line
# becomes a NativeCommandError and kills the pipe (observed 2026-09-26).
# Relax EAP around the native call - $LASTEXITCODE is the criterion.
$ErrorActionPreference = "Continue"
& C:\WINDOWS\py.exe $BackupPy --verify --retention 14 2>&1 |
    ForEach-Object { Add-Content -Path $LogFile -Value "$_" }
$code = $LASTEXITCODE
$ErrorActionPreference = "Stop"

Add-Content -Path $LogFile -Value "$stamp backup exit=$code"
exit $code
