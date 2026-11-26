# 事件驱动回测

回放使用已持久化事件的接收顺序、首次可观察时间与规则版本。任务预先固定时间区间、数据边界、策略和模拟假设。外部价格、未来结算和后来修订的市场元数据不能进入历史决策。运行使用专属 BACKTEST 账户，和 PAPER 共用决策、撮合、账务及确认结算逻辑。

任务状态依次为 QUEUED、RUNNING、COMPLETED，用户可在完成前取消。缺少盘口或结算样本时报告 INSUFFICIENT_DATA；保留时间覆盖、未验证订单和缺失观测。重复运行比较经济输出与配置、事件清单摘要，数据库生成的记录 ID 不参与比较。

## 按需回测流程

回测按需提交，默认不为低资源 VM 单独常驻回测进程。当前 engine worker 与 PAPER 模拟共用一个写入循环，每次只领取一个 `QUEUED` 或 `RUNNING` 回测。低磁盘环境一次只提交一个任务，等任务进入 `COMPLETED`、`FAILED` 或 `CANCELLED` 后再提交下一项。实时采集继续运行时，回测只读取已经持久化的事件，不会补写未来行情。

提交前固定 UTC 时间区间、策略版本、初始余额和模拟配置。`end_time` 必须早于当前时间，时间区间必须来自已导入且通过哈希校验的历史事件。下面的文件可以直接交给 `scripts/run-backtest.sh`，其中策略版本应替换为数据库中已登记的版本。

```json
{
  "start_time": "2026-11-24T00:00:00Z",
  "end_time": "2026-11-25T00:00:00Z",
  "strategy_id": "S00",
  "strategy_version": "1.0.0",
  "initial_balance": "1000",
  "seed": 17,
  "config": {
    "assets": ["BTC"],
    "timeframes": [5],
    "simulation_model": "conservative-v1",
    "tail_seconds": 60,
    "risk_per_trade": "0.005",
    "daily_loss": "0.02",
    "max_drawdown": "0.05",
    "market_cap": "0.02",
    "asset_cap": "0.1",
    "portfolio_cap": "0.2"
  }
}
```

在运行服务的主机上提交一次任务。脚本使用 `.env` 中的管理员凭证创建临时会话，并为请求生成幂等键。

```sh
scripts/run-backtest.sh /tmp/backtest.json
```

响应中的 `id` 是回测运行编号。控制台的“历史回测”页面会通过 SSE 刷新 `QUEUED`、`RUNNING`、`COMPLETED`、`FAILED` 和 `CANCELLED` 状态。命令行轮询需要使用同一个会话 cookie，并只查询该编号。以下示例保留了 cookie，避免重复提交任务。

```sh
base=http://127.0.0.1:${WEB_PORT:-8080}/api/v1
jar=$(mktemp)
trap 'rm -f "$jar"' EXIT
user=$(awk -F= '$1=="ADMIN_USERNAME"{print substr($0,index($0,"=")+1); exit}' .env)
password=$(awk -F= '$1=="ADMIN_PASSWORD"{print substr($0,index($0,"=")+1); exit}' .env)
python3 - "$user" "$password" <<'PY' | curl -fsS -c "$jar" -H 'Content-Type: application/json' --data-binary @- "$base/auth/login" >/dev/null
import json
import sys
print(json.dumps({"username": sys.argv[1], "password": sys.argv[2]}))
PY
run=$(curl -fsS -b "$jar" -H 'Content-Type: application/json' \
  -H "Idempotency-Key: $(openssl rand -hex 16)" --data-binary @/tmp/backtest.json "$base/backtests")
printf '%s\n' "$run"
id=$(printf '%s' "$run" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["id"])')
while :; do
  row=$(curl -fsS -b "$jar" "$base/backtests?q=$id&limit=1")
  printf '%s\n' "$row"
  state=$(printf '%s' "$row" | python3 -c 'import json,sys; d=json.load(sys.stdin)["data"]; print(d[0]["status"] if d else "MISSING")')
  case "$state" in COMPLETED|FAILED|CANCELLED|MISSING) break;; esac
  sleep 5
done
```

在 `QUEUED` 或 `RUNNING` 状态取消时，使用 `POST /api/v1/backtests/{id}/cancel` 并携带新的 `Idempotency-Key`。取消后 engine 会释放该回测账户的未结订单并把账户置为 STOPPED。完成或失败后保留回测结果、配置摘要和数据哈希。回测账户属于 BACKTEST 模式，不使用 PAPER 账户的生命周期接口，按历史库和研究记录的留存策略处理。

低磁盘 VM 的回测配额按事件覆盖估算。先查询时间区间内的事件数量，再决定是否拆分时间折。任务完成后检查 `result.event_count`、`result.data_hash`、`result.validation`、`result.settled_markets` 和 `result.mean_pnl_daily_block_ci`。没有足够有效样本时保留 `INSUFFICIENT_DATA`，不通过缩短区间或删除缺口来制造完整结果。

回测与实时采集共享 PostgreSQL 和归档卷。需要大量或长时间窗口时，在独立历史数据库和更大归档卷上按本文件的导入流程运行，然后将研究 API 和 engine worker 指向该历史库。历史库只允许 BACKTEST 账户，采集器拒绝连接历史模式。完成后将结果和 manifest 摘要复制回研究记录，保留原始历史库供复核。

按时间划分训练、验证与一次性测试区间。预注册尾盘窗口与费用压力场景。具有重叠观察期或标签期的市场必须从训练边界剔除，embargo 长度按最大观察窗口确定。统计推断按时间块而非逐笔独立抽样。

## 导出与导入历史行情

开始回测前可以通过管理 API 创建一个数据集请求，提交资产、周期、UTC 区间和 `max_bytes` 配额。请求只检查本地已经采集或导入的事件，状态为 `DATA_UNAVAILABLE` 时没有可用观察，`DATA_INCOMPLETE` 时存在缺口、缺少盘口或超出配额，`BACKTEST_READY` 时当前覆盖满足基础回放准入。刷新请求可以在后续采集或导入后重新评估，过期请求不会自动延长。回测传入 `dataset_id` 后，服务端和 worker 都会复核状态及时间区间。

在包含归档卷的 ops 容器中运行 `docker compose exec -T ops python -m pqcollector export --directory /data/parquet`。命令在同一 PostgreSQL 可重复读快照中归档观察记录、导出 markets、market_rule_versions、fee_schedules、market_resolutions，并输出 manifest 文件名、SHA-256 与事件数。复制 manifest 及其引用的全部文件，共享卷只读挂载也可供其他主机恢复。

在目标服务器以有 CREATEDB 权限的数据库连接作为 DATABASE_URL，运行以下命令。文件名及摘要替换为导出所给值。

```sh
docker compose run --rm --no-deps migrate python -m pqcollector import \
  --manifest /data/parquet/manifest-<digest>.json \
  --sha256 <digest> --database-name pq_history_experiment01
```

导入检查 manifest、元数据、Parquet 文件以及市场迁移的 SHA-256，逐批核对列、行数、事件 ID、序号及边界。序号允许存在 PostgreSQL 去重留下的空号。系统先在临时独立数据库执行迁移，成功恢复市场、规则、费用及结算关联后，将其命名为指定的新历史库。目标名称已被占用时退出。历史库的数据库约束仅允许 BACKTEST 账户，采集器也会拒绝在该库运行。将独立的回测 worker 与研究 API 的 DATABASE_URL 指向该数据库可提交回测。公共行情导出范围不含模拟账户、管理凭证和其他账户账务。

2026-11-26 用真实采集包导入独立数据库，恢复了 23,887 条事件、24 个市场、10 个明确结果。依赖原包存储的恢复还需要归档目录。已有不同结构的任意第三方 Parquet 不符合本项目的输入契约。

### 复现与统计

回放固定 BIGINT 事件范围和 UTC 接收区间，以序号分批提交；每笔事件同时检查接收时间与来源时间。回放载入 BOOK、GAP、RESOLUTION、METADATA、TRADE、ORACLE、REFERENCE 和 DATA_QUALITY。与决策绑定的官方价格和费用来自当时冻结的盘口元数据；未经验证的输入沿用缺失状态。数据 SHA-256 覆盖每条输入的 seq、种类、ID、时间、质量标志和规范 JSON。执行构建 SHA-256 覆盖引擎源码和经济模型迁移。恢复任务或账户时，摘要不匹配将拒绝新增研究决策。

每个时间折的训练集仅含在测试开始前 900 秒已经得到结算标签的市场，最后一折留作 holdout。比较同期市场概率与仅用训练折估计的平滑历史频率。缺少可独立评估的市场时，返回空折及样本不足状态。

分日 block bootstrap 使用结算市场为净期望观测单位，UTC 日为重采样块；少于 10 个观察日时不输出区间。费用和延迟压力实验可使用 `cost_multiplier` 及 `latency_multiplier` 固定放大倍数。[本地真钱包零连接的采集与模拟验收记录](../research/reports/2026-11-26-smoke.md)
