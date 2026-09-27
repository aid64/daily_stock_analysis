# ============================================================
# track_etf_momentum.ps1 - DSA_ETF_Signal scheduled-task wrapper
# Form aligned with backup_ledger.ps1 / keepalive_server.ps1.
# Runs the ETF momentum shadow signal tracker (paper-only) after
# market close on weekdays; appends to logs/etf_signal_task.log.
#
# NOTE: keep this file ASCII-only (PowerShell 5.1 reads BOM-less
# UTF-8 as ANSI on zh-CN systems; trailing non-ASCII can swallow
# newlines and merge the next line into a comment - observed).
# ============================================================
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot      # parent of scripts/
$LogFile     = Join-Path $ProjectRoot "logs\etf_signal_task.log"
$TrackerPy   = Join-Path $PSScriptRoot "track_etf_momentum_signals.py"

$stamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"
New-Item -ItemType Directory -Force -Path (Split-Path $LogFile) | Out-Null
Add-Content -Path $LogFile -Value "$stamp signal start"

# The py script logs to stderr; relax EAP around the native call
# (see backup_ledger.ps1 for the NativeCommandError pitfall).
$ErrorActionPreference = "Continue"
& C:\WINDOWS\py.exe $TrackerPy 2>&1 |
    ForEach-Object { Add-Content -Path $LogFile -Value "$_" }
$code = $LASTEXITCODE
$ErrorActionPreference = "Stop"

Add-Content -Path $LogFile -Value "$stamp signal exit=$code"
exit $code
