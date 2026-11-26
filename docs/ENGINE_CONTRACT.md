# 研究引擎接口

管理端在事务内调用 pq_account_command(action text, account_id uuid, payload jsonb, idempotency_key text, actor text)。动作使用小写 create/start/pause/resume/stop/clone/archive。返回 PostgreSQL 账户行 JSON。create 的账户 ID 为 NULL。所有金额按十进制字符串传输。clone 可提供新 name。

paper_accounts 使用 id、name、mode、status、strategy_id、strategy_version、config、config_hash、initial_balance、available_balance、frozen_balance、fees、realized_pnl、equity、peak_equity、max_drawdown、created_at、started_at、stopped_at。mode 取 PAPER、SHADOW 或内部 BACKTEST。模拟订单、成交、流水、持仓、权益、信号和风险表使用 account_id 外键及 created_at UTC 时间。账户创建默认版本 1.0.0，策略 S00。config 包括 assets、timeframes、simulation_model、tail_seconds 和假设说明。

backtest_runs 可写 config JSONB 新建任务，status 默认 QUEUED；查询 id、status、progress、config、result、created_at。取消仅在 QUEUED 或 RUNNING 时更新为 CANCELLED。config 支持 start_time、end_time、strategy_id、strategy_version、initial_balance 和账户 config。

采集事件表 engine_events 的 seq、kind、market_id、event_id、event_time、received_at、data 和 quality_flags 构成输入。BOOK 的 data 包含 side、token_id、asks、bids、tick_size、minimum_order_size、fee_rate、fee_exponent、asset、timeframe、start_time、end_time、rule_version、rule_verified、accepting_orders。RESOLUTION 需 verified、outcome、rule_version 和 source。GAP 使已知订单簿失效。输入的所有时间均为 UTC。

config.strategies 可选，至多包含四个附加策略对象，每项提供 strategy_id、strategy_version。数据库在创建时冻结附加版本的参数和实现 SHA-256。风险和虚拟现金限额由同一组合账户统一维护。费用压力 cost_multiplier 为 1 至 3，延迟压力 latency_multiplier 为 1 至 5。

策略构建标识通过 services.engine.build.engine_hash() 计算。修改任何受摘要覆盖的文件会导致旧账户拒绝启动、新回放返回 FAILED。发布不同实现时应增加不可变的策略版本和迁移记录；需要重新分析旧实验时使用原始发布构建及原始事件数据。
