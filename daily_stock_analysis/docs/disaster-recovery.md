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
| 对比盘台账 | `.scratch/dsa-strategy-validation/compare_strategies.json` | cohort 真源（prereg/marks/`_control`/18 槽持仓）；`archive/cohort-manifest.jsonl`（如存在）一并备份。`ledger-seal-manifest.json` 属一次性封存证据，不在每日备份内 |
| 收盘全量备份 | `data/backups_close/<日期>/` | 17:25 `DSA_Ledger_CloseBackup` 独立全量（DB/.env/台账等），保留 14 份；与 08:30 的 `data/backups/` 分离，不覆盖早上备份 |

布局：`data/backups/<YYYY-MM-DD>/…`，每份含 `MANIFEST.txt`（备份时间/大小/源库路径；对比盘台账存在时另含 `deck_ledger_bytes` 与 `deck_ledger_sha256`）。
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

**server 自愈（`DSA_Server_Keepalive`）**：每 10 分钟探测 8000；若端口未通但已有 `main.py --serve` 进程（启动中），只等待、不再启动第二个 server（2026-10-29 02:01 手动与计划任务并发实测会留下重复进程）；无进程时才隐藏启动 `py main.py --serve`。端口等待上限 **180s**（原 60s 在 2026-10-28 23:51 实测不足，server 进程 23:51:06 已启动但端口监听被 LiteLLM import 时远程 cost map 拉取拖过 60s）。脚本显式设置 `LITELLM_LOCAL_MODEL_COST_MAP=True`，与 `.env` 默认一致；失败告警的 Python 子进程需在 `$ErrorActionPreference=Continue` 下调用，避免 stderr 被 PowerShell 当作终止错误、失败日志被 LiteLLM 警告污染；健康/失败日志使用实际发生时间而不是启动时刻。

包装脚本 `scripts/backup_ledger.ps1`（2026-09-26 U4 修复引入）调用 py 脚本并把输出落到
`logs/backup_task.log`、透传退出码给调度器。**该脚本必须保持纯 ASCII**：PowerShell 5.1
对无 BOM 的 UTF-8 按系统 ANSI 代码页误读，行尾中文可能吞掉换行符把下一行并进注释
（实测发生过：两条赋值语句消失，`$LogFile` 为空）。

手动执行（在 `daily_stock_analysis/` 下）：

```bash
py scripts/backup_ledger.py --verify              # 备份 + 校验 db 与对比盘台账
py scripts/backup_ledger.py --retention 30        # 改保留份数（0=不删）
schtasks /Run /TN "DSA_Ledger_Backup"             # 计划任务上下文验证（改包装后必做）
```

`--verify` 会打开备份出的 db 确认 `portfolio_accounts/trades/cash_ledger/positions/event_audit`
五张核心表齐全；若对比盘台账存在，还会确认其可解析且保留 `strategies` / `marks` / `_control`
三块容器。任一校验失败退出码非 0（计划任务可据此告警）。干净检出/未启用对比盘时台账缺失只告警，不阻断账户备份。

> 建议：`data/backups/` 只在本机等于"同盘副本"，防不了盘坏。真要抗硬件故障，
> 再把 `data/backups/` 定期同步到第二块盘或网盘（本脚本不做外部上传，避免把
> 账本/凭据推到三方——需要时由你显式配置同步工具）。

### 2.1 离机同步（第二存储）

`scripts/backup_offsite.py` 把**最新一份**每日备份复制到用户指定的第二存储
（外置盘/网盘同步目录），逐文件 sha256 校验并写 `OFFSITE_MANIFEST.txt`：

```bash
# 先预览，再执行；--date 可选，默认最新日期
py scripts/backup_offsite.py --destination E:\DSA-Backup --dry-run
py scripts/backup_offsite.py --destination E:\DSA-Backup
py scripts/backup_offsite.py --destination E:\DSA-Backup --retention 8

# 仅当目标是加密存储时，才把 .env 一并同步
py scripts/backup_offsite.py --destination E:\DSA-Backup --include-env

# 收盘备份（data/backups_close）作为源，同步最新收盘全量
py scripts/backup_offsite.py --destination E:\DSA-Backup --source-root data\backups_close
```

- 默认布局：`<destination>/daily_stock_analysis/<YYYY-MM-DD>/`；默认保留最近 14 份，
  只清理本脚本管理的日期目录，不触碰目标里的其他文件。
- **默认跳过 `.env`**（含飞书/LLM/数据源凭据）；目标未加密时不要把明文凭据复制过去。
  完整还原需要 `.env` 时，用 `--include-env` 并确保目标是加密盘/加密卷，或把 `.env`
  单独保存在密码管理器。默认模式会删除目标同日目录里上次遗留的 `.env`。
- 脚本拒绝把目标放在 `data/backups/` 内（那不是离机）；也不做任何自动上传/云 API。
- **2026-10-27 临时第二分区副本**：本机 C:/D: 是同一块物理盘，尚无外置盘/网盘目标。已注册
  `DSA_Offsite_Copy`（工作日 17:40，源 `data/backups_close`，目标 `C:\DSA-Backup`，保留 7 份，
  `WakeToRun`），只防逻辑误删/文件损坏，**不防物理盘损坏，也不是离机**。接入真正外置/网络目标后，
  应把任务目标切换到该存储并保留本副本作为本地冗余。
- **quant 信号输入副本（2026-10-28 增）**：同一任务同时调用 `backup_quant_snapshot.py`，把
  `convertible_pit.parquet`、`paper_tracking/current_plan.json`、最新 `feeds_snapshot_v*.pkl`、
  最新 H 目标缓存和 `etf_mwu_cb_monthly.yaml` 复制到 `C:\DSA-Backup\quant\<date>\`，逐文件
  sha256 并写 `QUANT_MANIFEST.txt`。恢复后可直接重放 E/H/A/B 信号，无需先重建 PIT；PIT 源 raw
  缓存仍不在副本内，若需重建历史 PIT 仍需原始缓存。
- 计划任务：真正离机目标确定后，将 `DSA_Offsite_Copy` 的 `$Destination` 指向外置盘/网盘同步目录；
  本脚本不做自动上传/云 API。
- 还原：把 `<destination>/daily_stock_analysis/<date>/` 复制回
  `data/backups/<date>/`，再按场景 A/B/C 执行；若未同步 `.env`，从密码管理器恢复。

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
   `output/decision_snapshot/`、`output/order_sheets/`，以及
   `dsa-strategy-validation/compare_strategies.json` 与
   `dsa-strategy-validation/archive/cohort-manifest.jsonl`；
3. `pip install -r requirements.txt` → 重启 server → 重注册 keepalive 与备份计划任务。

## 四、边界与已知限制

- **默认只做本机备份**，不外传；离机需显式运行 `backup_offsite.py` 到你指定的第二存储（默认排除 `.env`）。抗盘坏需你实际配置目标。
- **备份时点**：每日 08:30 的完整备份早于 15:00 收盘；17:25 `DSA_Ledger_CloseBackup` 另做独立收盘全量备份到 `data/backups_close/<日期>/`（DB/.env/台账，保留 14 份），不覆盖早上备份。17:30 `DSA_Deck_Audit` 另有台账快照（保留 30 份）。**这些都在本机同一磁盘，尚未离机**；抗盘坏仍需配置 `backup_offsite.py` 目标。
- **审计检查范围**：`DSA_Deck_Audit` 会检查 08:30 备份新鲜度、当日收盘备份、收盘台账快照（与 live 台账 sha 一致且 15:00 后）和 `DSA_Ledger_Backup` 结果；但**不检查离机目标或审计任务自身**。审计 FAIL 会 best-effort 推飞书；备份失败仍需看 `logs/backup_task.log` / `logs/backup_close.log`。
- 备份是**只增**的（滚动删旧），恢复是覆盖式，无回滚需求。
- 跨项目契约 `quant_research/output/paper_tracking/current_plan.json` 是**运行时生成**的
  （由 `run_paper_tracking.py`），未纳入备份——丢失时重跑管线即可再生，不属账户真源。
- PIT 数据（`quant_research/data/pit/*.parquet`）是可重建的数据缓存，同样不备份。
