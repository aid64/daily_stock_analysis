# 单机灾难恢复（DR）说明

> v4.1 计划 P0.5 交付。目的：账户真源是单机单 SQLite（`data/stock_analysis.db`，
> 9 张 `portfolio_*` 表 + append-only 审计），此前零备份——本文档给出**备份怎么建、
> 崩了怎么还原**，让单机环境有明确还原路径。

## 一、备份什么、备在哪

备份脚本 `scripts/backup_ledger.py`（SQLite 在线 backup API，运行中热备份也一致、不锁库）。

一份完整还原所需的最小集：

| 内容 | 路径 | 为什么 |
|---|---|---|
| 账户真源 | `data/stock_analysis.db` | 持仓/现金/交易/审计的唯一真源 |
| 配置与凭据 | `.env` | 价格规则、飞书、LLM、数据源 key |
| 策略注册表 | `strategies.yaml`（仓库根） | 策略参数（改参数不改代码） |
| 决策快照归档 | `output/decision_snapshot/` | `/操作单 历史` 的审计留痕 |
| 历史操作单 | `output/order_sheets/` | 回填/对比依据 |

布局：`data/backups/<YYYY-MM-DD>/…`，每份含 `MANIFEST.txt`（备份时间/大小/源库路径）。
`data/` 已在 `.gitignore`，备份不入库、不产生脏 diff。

## 二、每天怎么备（Windows 计划任务）

```powershell
# 每日 08:30 备份并校验，保留最近 14 份
# 必须走 powershell -File 包装形态（与 DSA_Server_Keepalive 同款）。
# 直接 py.exe 直调在计划任务上下文会 0x800710E0（2026-09-24~26 三连败实测）。
schtasks /Create /TN "DSA_Ledger_Backup" /TR ^
  "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File D:\daily_stock_analysis\daily_stock_analysis\scripts\backup_ledger.ps1" ^
  /SC DAILY /ST 08:30 /F
```

**根因两层（2026-09-27 补齐第二层）**：①Action 形态（py.exe 直调被任务上下文拒——powershell 包装修复）；②**任务条件**（`DisallowStartIfOnBatteries=true` + `StartWhenAvailable=false`——笔记本 08:30 常在睡眠/电池，触发即被拒 0x800710E0，09-27 自然触发实证）。②的修复：
```powershell
$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew
Set-ScheduledTask -TaskName "DSA_Ledger_Backup" -Settings $s
```
（45MB 本地拷贝允许电池态运行；错过 08:30 后唤醒即补跑。）验收=连续 3 日自然触发成功（09-27 条件修复后重新起算）。

包装脚本 `scripts/backup_ledger.ps1`（2026-09-26 U4 修复引入）调用 py 脚本并把输出落到
`logs/backup_task.log`、透传退出码给调度器。**该脚本必须保持纯 ASCII**：PowerShell 5.1
对无 BOM 的 UTF-8 按系统 ANSI 代码页误读，行尾中文可能吞掉换行符把下一行并进注释
（实测发生过：两条赋值语句消失，`$LogFile` 为空）。

手动执行（在 `daily_stock_analysis/` 下）：

```bash
py scripts/backup_ledger.py --verify              # 备份 + 校验 db 可打开且核心表齐全
py scripts/backup_ledger.py --retention 30        # 改保留份数（0=不删）
schtasks /Run /TN "DSA_Ledger_Backup"             # 计划任务上下文验证（改包装后必做）
```

`--verify` 会打开备份出的 db 确认 `portfolio_accounts/trades/cash_ledger/positions/event_audit`
五张核心表齐全；校验失败退出码非 0（计划任务可据此告警）。

> 建议：`data/backups/` 只在本机等于"同盘副本"，防不了盘坏。真要抗硬件故障，
> 再把 `data/backups/` 定期同步到第二块盘或网盘（本脚本不做外部上传，避免把
> 账本/凭据推到三方——需要时由你显式配置同步工具）。

## 三、崩了怎么还原

### 场景 A：账本损坏/误删（最常见）

1. 停掉 server（避免还原时被写覆盖）：结束 `server.py` 进程，或先停 keepalive 计划任务。
2. 选一份可用备份（默认取最新，或按需选更早的一天）：
   ```
   data/backups/<日期>/stock_analysis.db
   ```
3. 覆盖回主库：
   ```bash
   cp data/backups/<日期>/stock_analysis.db data/stock_analysis.db
   # 若存在 WAL/SHM 残留，一并清掉避免旧未提交写回放
   rm -f data/stock_analysis.db-wal data/stock_analysis.db-shm
   ```
4. 校验：
   ```bash
   py -X utf8 -c "import sqlite3; c=sqlite3.connect('data/stock_analysis.db'); print(sorted(r[0] for r in c.execute(\"select name from sqlite_master where type='table' and name like 'portfolio%'\")))"
   ```
   应列出 9 张 `portfolio_*` 表。
5. 重启 server，`/持仓` 与 `/健康` 确认账户数据正确。

### 场景 B：配置/规则丢失

从备份取回 `.env` 与 `strategies.yaml` 覆盖回原位（`.env` 在 `daily_stock_analysis/`，
`strategies.yaml` 在仓库根），重启 server。

### 场景 C：整机迁移

1. clone 仓库（代码是 git 真源）；
2. 从备份恢复 `data/stock_analysis.db`、`.env`、`strategies.yaml`、
   `output/decision_snapshot/`、`output/order_sheets/`；
3. `pip install -r requirements.txt` → 重启 server → 重注册 keepalive 与备份计划任务。

## 四、边界与已知限制

- **本脚本只做本机备份**，不外传（不把账本/凭据推到三方，符合安全边界）。抗盘坏需你另配盘/网盘同步。
- 备份是**只增**的（滚动删旧），恢复是覆盖式，无回滚需求。
- 跨项目契约 `quant_research/output/paper_tracking/current_plan.json` 是**运行时生成**的
  （由 `run_paper_tracking.py`），未纳入备份——丢失时重跑管线即可再生，不属账户真源。
- PIT 数据（`quant_research/data/pit/*.parquet`）是可重建的数据缓存，同样不备份。
