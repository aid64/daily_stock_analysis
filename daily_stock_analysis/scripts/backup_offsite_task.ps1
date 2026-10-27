# ============================================================
# backup_offsite_task.ps1 - copy latest close backup to C: second partition
# ASCII-only (PowerShell 5.1 reads BOM-less UTF-8 as ANSI on zh-CN).
# NOTE: C: and D: are partitions on the same physical disk; this is a
# logical second copy, NOT offsite and NOT disk-failure redundancy.
# ============================================================
$ErrorActionPreference = "Stop"
$ProjectRoot = "D:\daily_stock_analysis\daily_stock_analysis"
$LogFile     = Join-Path $ProjectRoot "logs\backup_offsite_task.log"
$ScriptPy    = Join-Path $ProjectRoot "scripts\backup_offsite.py"
$QuantPy     = Join-Path $ProjectRoot "scripts\backup_quant_snapshot.py"
$Destination = "C:\DSA-Backup"
$SourceRoot  = "data\backups_close"

if ((Test-Path $LogFile) -and ((Get-Item $LogFile).Length -gt 5MB)) {
    Move-Item -Force $LogFile ($LogFile + ".old")
}
$stamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"
New-Item -ItemType Directory -Force -Path (Split-Path $LogFile) | Out-Null
Add-Content -Path $LogFile -Value "$stamp offsite-copy start source=$SourceRoot dest=$Destination"

$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$py = "C:\WINDOWS\py.exe"
if (-not (Test-Path $py)) { $py = "py" }

$code = 1
$ErrorActionPreference = "Continue"
Set-Location $ProjectRoot
& $py $ScriptPy --destination $Destination --source-root $SourceRoot `
    --retention 7 2>&1 |
    ForEach-Object { Add-Content -Path $LogFile -Value "$_" -Encoding UTF8 }
$code = $LASTEXITCODE
if ($code -eq 0) {
    & $py $QuantPy --destination $Destination --retention 7 2>&1 |
        ForEach-Object { Add-Content -Path $LogFile -Value "$_" -Encoding UTF8 }
    $code = $LASTEXITCODE
}

$endStamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"
Add-Content -Path $LogFile -Value "$endStamp offsite-copy exit=$code"
exit $code
