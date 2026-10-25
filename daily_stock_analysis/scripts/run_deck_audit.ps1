# ============================================================
# run_deck_audit.ps1 - DSA_Deck_Audit scheduled-task wrapper
#
# Post-close audit: snapshot the close-state ledger, then run
# compare_deck_audit.py and append its UTF-8 output to
# logs/compare_deck_audit.log. The live ledger is never written and no
# notifications are sent. Exit code is propagated to Task Scheduler.
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

# Post-close ledger snapshot: the 08:30 full backup predates the 15:00 close.
# Keep a small, validated copy of the close-state ledger without touching the
# live file or overwriting the morning backup. Retain 30 daily snapshots.
$RepoRoot    = Split-Path -Parent $ProjectRoot
$Ledger      = Join-Path $RepoRoot ".scratch\dsa-strategy-validation\compare_strategies.json"
$SnapDir     = Join-Path $RepoRoot ".scratch\dsa-strategy-validation\postclose"
$SnapPath    = Join-Path $SnapDir ("compare_strategies." + (Get-Date -Format "yyyyMMdd") + ".json")
try {
    if (Test-Path $Ledger) {
        New-Item -ItemType Directory -Path $SnapDir -Force | Out-Null
        $SnapTmp = "$SnapPath.tmp"
        Copy-Item -LiteralPath $Ledger -Destination $SnapTmp -Force
        & C:\WINDOWS\py.exe -c "import json,sys; json.load(open(sys.argv[1],encoding='utf-8'))" $SnapTmp 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Move-Item -LiteralPath $SnapTmp -Destination $SnapPath -Force
            $SnapHash = (Get-FileHash -Algorithm SHA256 $SnapPath).Hash.ToLower()
            Add-Content -Path $LogFile -Value "$stamp postclose snapshot=$SnapPath sha256=$SnapHash" -Encoding UTF8
            Get-ChildItem -Path $SnapDir -Filter "compare_strategies.*.json" |
                Sort-Object LastWriteTime |
                Select-Object -SkipLast 30 |
                Remove-Item -Force
        } else {
            Remove-Item -LiteralPath $SnapTmp -Force -ErrorAction SilentlyContinue
            Add-Content -Path $LogFile -Value "$stamp postclose snapshot=INVALID_LEDGER" -Encoding UTF8
        }
    } else {
        Add-Content -Path $LogFile -Value "$stamp postclose snapshot=NO_LEDGER" -Encoding UTF8
    }
} catch {
    Add-Content -Path $LogFile -Value "$stamp postclose snapshot=ERROR $($_.Exception.Message)" -Encoding UTF8
}

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

# Best-effort Feishu alert on FAIL; never change the audit exit code.
if ($code -ne 0) {
    & C:\WINDOWS\py.exe -X utf8 (Join-Path $PSScriptRoot "notify_deck_audit_fail.py") 2>&1 |
        ForEach-Object { Add-Content -Path $LogFile -Value "$stamp $($_)" -Encoding UTF8 }
}
exit $code
