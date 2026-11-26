# 交接指南

先读 docs/TASKS.md（验收进度）、docs/KNOWN_ISSUES.md（现存阻塞）和 docs/CHANGELOG.md。需求原文在 docs/PRD.md，市场证据的适用范围见 docs/MARKET_RULES.md。

项目只处理 BTC/ETH、5/15 分钟。所有数据记录为 UTC；资金用 Decimal / NUMERIC，传输用十进制字符串。单独存放测试数据。任何新行情源先留存原始规则和授权条件，再决定是否用于研究。成交、资金、结算必须提交于同一数据库事务或按业务事件独立原子入账。禁止真实资金、签名、实盘订单和编造绩效。

前端和 API 由 pnpm workspace 管理。Python 包按业务域组织，在同一 Compose 项目中运行。跨域契约变化需同时更新迁移、API、UI 和测试。新迁移按文件名顺序追加，已应用文件受 SHA-256 校验。使用 Conventional Commits。每次结束更新任务、变更、已知问题，记录实际执行的验证。

## v0.1.0 工程进度

Vue、Hono、Python 采集/模拟、回测和维护进程已联通。数据库采用 migrations + runtime-grants.sql，四个运行角色与迁移账户分开。用户启动 PAPER 后仍需等候合格实时盘口。本机软件验收与首份真实结果见 research/reports/2026-11-26-smoke.md，研究判定 INSUFFICIENT_DATA。归档目录、数据库备份和 .env 只在本机，测试使用独立库或临时 schema。

发行流程位于 .github/workflows/ci.yml。后续修改经济模型时同步处理 engine/build.py 的内容摘要和新策略版本迁移。事件导入只写新历史研究库，打开回测前核验归档哈希。

公开发布 v0.1.0 的源提交是 cca13fc，三个 GHCR 镜像均有 linux/amd64 及 linux/arm64 manifest。版本摘要和 CI 链接见 CHANGELOG.md。部署验收与真实交易研究相互独立，长期样本量仍是当前阻塞项。
