# 验证策略

独立测试库先应用全量迁移。验证创建、部分成交、订单过期、冻结解冻、幂等结算、并发命令及重启恢复。回放同样的已知事件两次，对比结果哈希。时间泄漏测试注入在决策后才到达的盘口；所有 fixture 标记为测试来源。

管理面验证未授权访问、只读角色写入、同键重试、输入边界、排序字段、CSV 转义和前端关键流程。部署检查按 Compose 配置、镜像构建、服务就绪、备份和隔离恢复执行。网络实测与确定性测试独立记录。

具体命令和本轮运行结果在 CHANGELOG.md 中。失败保留测试及原因，环境阻塞单列。

## 本地复验

使用 Python 3.12、uv、Node 22、pnpm 10，运行 uv sync --frozen 和 pnpm install --frozen-lockfile。使用独立 PostgreSQL 17 库提供 TEST_DATABASE_URL 和 DATABASE_URL，独立 Redis 提供 REDIS_URL。运行 uv run python -m services.migrate、uv run pytest -q、uv run ruff check .、uv run mypy pqcollector services、pnpm typecheck、pnpm lint、pnpm build、pnpm --filter @polyquant/api test。API 测试要求测试库名含 test。

pnpm --filter @polyquant/web exec playwright test console.spec.ts 使用隔离接口样本。真实端到端另以一次性完整 Compose 栈执行 stack.spec.ts，配置见 CI。scripts/healthcheck.sh 核查全体账本及上游状态。部署验收和本地依赖审计记录见 docs/evidence/2026-11-26。
