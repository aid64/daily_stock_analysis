# ============================================================
# keepalive_server.ps1 — DSA server 守护（审计第 10 条修复）
# ============================================================
# 背景：账户中心系统的可用性 = server 进程的可用性，而它是已知数周的
# 单点故障（2026-09-18 整个交易日因此丢了决策包）。
# 用法：由 Windows 计划任务每 10 分钟调用一次（注册命令见
# docs/REFACTOR-PLAN-STRATEGY-CENTRIC.md S0.5）；也可手动执行。
# 行为：端口探测（2s 超时）不通才拉起，避免重复启动；
# 拉起动作用 hidden 窗口 + 日志留痕（logs/keepalive.log）。
# ============================================================

$ErrorActionPreference = "Stop"
# Avoid LiteLLM's import-time remote cost-map fetch (2026-10-28 23:51: the
# server process started immediately but did not bind port 8000 within 60s
# because the fetch stalled on a weak network). The bundled local map is
# sufficient for runtime pricing; set False explicitly if fresh costs matter.
$env:LITELLM_LOCAL_MODEL_COST_MAP = "True"
$Port = 8000
$ProjectRoot = Split-Path -Parent $PSScriptRoot   # scripts/ 的上级 = daily_stock_analysis/
$LogFile = Join-Path $ProjectRoot "logs\keepalive.log"

function Test-ServerAlive {
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $task = $client.ConnectAsync("127.0.0.1", $Port)
        if ($task.Wait(2000) -and $client.Connected) {
            $client.Close()
            return $true
        }
        $client.Close()
        return $false
    } catch {
        return $false
    }
}

if (Test-ServerAlive) {
    exit 0
}

$stamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"
try {
    New-Item -ItemType Directory -Force -Path (Join-Path $ProjectRoot "logs") | Out-Null
    Add-Content -Path $LogFile -Value "$stamp port $Port down - restarting server"
    Start-Process -FilePath "py" -ArgumentList "main.py", "--serve" `
        -WorkingDirectory $ProjectRoot -WindowStyle Hidden
    Add-Content -Path $LogFile -Value "$stamp restart issued (py main.py --serve)"
    $healthy = $false
    for ($i = 0; $i -lt 90; $i++) {
        Start-Sleep -Seconds 2
        if (Test-ServerAlive) { $healthy = $true; break }
    }
    if ($healthy) {
        Add-Content -Path $LogFile -Value "$stamp restart healthy (port $Port)"
        exit 0
    }
    Add-Content -Path $LogFile -Value "$stamp restart FAILED: port $Port not listening after 180s"
    $py = "C:\WINDOWS\py.exe"
    if (-not (Test-Path $py)) { $py = "py" }
    $dedup = "keepalive_fail_" + (Get-Date -Format "yyyyMMdd")
    $notifyCode = "import sys; sys.path.insert(0, r'" + $ProjectRoot + "'); from src.notification import NotificationService; NotificationService().send('DSA server keepalive: restart failed on port $Port', route_type='system_error', dedup_key='$dedup')"
    Set-Location $ProjectRoot
    # Native stderr must not become a terminating NativeCommandError before
    # the notification completes; the alert is best-effort and exit stays 1.
    $ErrorActionPreference = "Continue"
    & $py -c $notifyCode 2>&1 | ForEach-Object { Add-Content -Path $LogFile -Value "$_" -Encoding UTF8 }
    $notifyExit = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
    Add-Content -Path $LogFile -Value "$stamp notify exit=$notifyExit"
    exit 1
} catch {
    Add-Content -Path $LogFile -Value "$stamp restart FAILED: $($_.Exception.Message)"
    exit 1
}
