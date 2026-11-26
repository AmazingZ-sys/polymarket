# 交接指南

先读 docs/TASKS.md（验收进度）、docs/KNOWN_ISSUES.md（现存阻塞）和 docs/CHANGELOG.md。需求原文在 docs/PRD.md，市场证据的适用范围见 docs/MARKET_RULES.md。

项目只处理 BTC/ETH、5/15 分钟。所有数据记录为 UTC；资金用 Decimal / NUMERIC，传输用十进制字符串。单独存放测试数据。任何新行情源先留存原始规则和授权条件，再决定是否用于研究。成交、资金、结算必须提交于同一数据库事务或按业务事件独立原子入账。禁止真实资金、签名、实盘订单和编造绩效。

前端和 API 由 pnpm workspace 管理。Python 包按业务域组织，在同一 Compose 项目中运行。跨域契约变化需同时更新迁移、API、UI 和测试。新迁移按文件名顺序追加，已应用文件受 SHA-256 校验。使用 Conventional Commits。每次结束更新任务、变更、已知问题，记录实际执行的验证。
