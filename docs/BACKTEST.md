# 事件驱动回测

回放使用已持久化事件的接收顺序、首次可观察时间与规则版本。任务预先固定时间区间、数据边界、策略和模拟假设。外部价格、未来结算和后来修订的市场元数据不能进入历史决策。运行使用专属 BACKTEST 账户，和 PAPER 共用决策、撮合、账务及确认结算逻辑。

任务状态依次为 QUEUED、RUNNING、COMPLETED，用户可在完成前取消。缺少盘口或结算样本时报告 INSUFFICIENT_DATA；保留时间覆盖、未验证订单和缺失观测。重复运行比较经济输出与配置、事件清单摘要，数据库生成的记录 ID 不参与比较。

按时间划分训练、验证与一次性测试区间。预注册尾盘窗口与费用压力场景。具有重叠观察期或标签期的市场必须从训练边界剔除，embargo 长度按最大观察窗口确定。统计推断按时间块而非逐笔独立抽样。

## 导出与导入历史行情

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
