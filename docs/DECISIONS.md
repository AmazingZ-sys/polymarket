# 决策记录

|日期|决定|依据及影响|
|---|---|---|
|2026-11-26|复用原 Git 仓库，以模块化进程建立 Monorepo|初始提交 de69be0 仅有标题 README；无需移动历史|
|2026-11-26|以 PostgreSQL 做事务与互斥，模拟控制用 SQL 函数同步返回|撤单、暂停、审计及余额应原子提交。行锁和事务锁的作用域见官方文档|
|2026-11-26|接收时间决定可见性，盘口、研究信号、真实结算留证|同市场存在多个时间概念；按来源时间排序可能引入未来信息|
|2026-11-26|源断开时前端显示空样本和状态，外部参考价单列|使受限部署仍能验证会话、账本及恢复，同时避免生成误导性成交|

[PostgreSQL 锁](https://www.postgresql.org/docs/current/explicit-locking.html) · [Compose 启动依赖](https://docs.docker.com/compose/how-tos/startup-order/) · [Hono Node 适配](https://hono.dev/docs/getting-started/nodejs)

- API、采集、模拟、运维分别使用受限数据库角色，DDL 归迁移进程。
- 市场 RAW 冷归档保留哈希、原 ID 与序号索引。历史导入使用清单恢复到独立库，打开 BACKTEST 前重新核验。
- 所有账户冻结策略版本、配置及代码摘要。具备重新运行证据的候选策略通过研究协议后，管理员可以登记基线变更。
