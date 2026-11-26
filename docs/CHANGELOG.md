# 变更日志

## 2026-11-26

- 检查 main 分支、工作树和初始提交。归档原始需求和分期验收协议，开始四类市场独立探测。

- 完成四类市场独立规则与费用快照、CLOB WebSocket、Gamma 和 Kraken 公共行情采集、缺口检测与压缩 Parquet。实测访问限制和授权状态见 DATA_SOURCES.md。
- 加入固定精度虚拟账本、幂等账户控制、S00–S04、保守 IOC 撮合、可验证结算及隔离回测。固定真实窗口两次回放结果相同；见 research/reports/2026-11-26-smoke.json。

- 加入中文管理终端、同源会话、分页 API、SSE、独立数据库角色、策略组合、压力回放、人工评审和晋级、定期研究报告和通知。
- 已在本机运行实际 Compose，核验就绪、登录、总览、市场、数据源、任务和 OpenAPI。隔离测试覆盖浏览器账户生命周期、并发幂等和数据库恢复。

- 在独立 Docker Compose 中演练了带 49,586 条事件和 10 个 Parquet 的备份、哈希核验、虚拟资金恢复、更新与手动回滚。注入无法就绪的 Web 镜像后自动恢复旧版本。见 docs/evidence/2026-11-26/operations-smoke.md。
- 补充 GCE 低磁盘 VM 部署剖面和按需回测流程。15 GiB 预算、留存清理、分级停写、流量观察与部署验收命令见 DEPLOYMENT.md、OPERATIONS.md 和 BACKTEST.md。
- 增加 `COLLECTOR_PROFILE=free` 低资源采集档和回测数据集准入状态。free 档按日事件预算、15 GiB 已用空间上限与剩余空间保护降级写入，默认 `.env.example` 用于 15 GiB 部署。

## v0.1.0 正式发布

GitHub Actions 在 cca13fc 完成验证、版本计算、三个应用镜像的双架构构建、安全扫描和 Release 发布。匿名访问三个 0.1.0 OCI 索引均返回 HTTP 200，摘要与 Release Notes 一致。[发布版本](https://github.com/AmazingZ-sys/polymarket/releases/tag/v0.1.0) · [CI/CD 运行](https://github.com/AmazingZ-sys/polymarket/actions/runs/45745176063)

本机 Docker 部署通过就绪、登录、账本、备份和隔离恢复检查。固定研究窗口内 23,154 条真实事件的两次回放一致，模拟成交为 0。研究状态为 INSUFFICIENT_DATA。现场边界见 KNOWN_ISSUES.md。
