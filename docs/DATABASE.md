# 持久化边界

按字典序运行 database/migrations/*.sql，每个文件单独事务，记录版本和校验和，互斥执行。运行中的旧进程要能读取新迁移；不兼容的数据恢复必须在隔离库演练后切换。

|实体组|权威数据与关联|
|---|---|
|市场|markets 关联规则、费用和原始元数据。market_id 与 condition_id、两个 token_id 的映射需复核|
|行情|engine_events 保留来源、时间、顺序和质量。通过市场和观察顺序检索；归档保留内容身份|
|账户|paper_accounts 的 UUID 隔离余额、订单、成交、持仓、结算、快照。NUMERIC 支持固定精度|
|控制|会话、写命令幂等键和审计记录持久化。Redis 丢失只影响短期限流|
|研究|回测绑定配置、代码与数据版本，改进假设及人工审核独立于运行中的配置|
|运维|按 UTC 期间及任务键去重报告，通过持久通知队列调度和重试|

当前实际列、索引、唯一约束与可用查询视图以迁移文件为准。备份需同时包含数据库和归档目录。

迁移按文件全名排序，前导数字用于分组。runtime-grants.sql 在指定运行角色密码时重复应用。市场时间窗可按 asset/timeframe/start_time 检索。engine_events 通过市场、种类、序号及时间 BRIN 索引定位观测。balance_ledger(account_id,id) 关联前后余额。paper_settlements(account_id,market_id) 唯一约束防止重复兑付。配置通过触发器保护。冷热转移与恢复由 BACKTEST.md 和 DATA_QUALITY.md 定义。
