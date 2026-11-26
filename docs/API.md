# 管理接口

前缀 /api/v1。GET /healthz 和 /readyz 位于根。会话认证使用同源 HttpOnly cookie；管理员写入需 Idempotency-Key，登录、退出除外。金额字段使用十进制字符串。列表为 {data: [], meta: {limit, offset, total}}，单个对象为 {data: ...}，错误为 {error: {code, message}}。GET /api/v1/openapi.json 获取可机器读取的路径。

|方法|路径（省略前缀）|用途|
|---|---|---|
|POST、GET、POST|/auth/login、/auth/me、/auth/logout|username、password 换取定时会话，查询身份、注销|
|GET|/dashboard、/events|汇总与 refresh 事件流|
|GET|/markets、/market-events、/data-sources、/opportunities|实际采集和信号，按 market_id 筛选事件|
|GET、POST|/paper-accounts|列表和创建。name、initial_balance、mode、strategy_id、strategy_version、config|
|GET、POST|/paper-accounts/{id}、/paper-accounts/{id}/{action}|详情及 start、pause、resume、stop、archive、clone|
|GET|/paper-orders、/paper-fills、/paper-positions、/settlements、/ledger、/equity|account_id 过滤|
|GET|/strategies、/strategy-versions、/experiments、/attributions、/analytics|策略、实验及结果|
|GET、POST|/backtest-datasets、/backtest-datasets/{id}/refresh|登记历史数据覆盖请求，复查当前库的可用性|
|GET、POST|/backtests、/backtests/{id}/cancel|回放与取消。传入 `dataset_id` 时必须绑定 `BACKTEST_READY` 数据集|
|GET|/risk、/reports、/improvements、/logs、/system、/jobs、/settings|研究与运维视图|

默认 limit=50，最多 200。offset 从 0 开始。列表支持白名单排序/筛选。重复的同键写请求返回原结果，键被用于另一请求时拒绝。查询分页导出时保留顺序和过滤条件。

GET /evaluations 和 /promotions 提供评审与基线变更历史。POST /evaluations 传入对照实验、候选账户、基础及加费压力回放、期间运行开支和人工证据；服务端记录当次比较的指标与状态。POST /strategy-versions/{strategy}/{version}/promote 要求相同版本的通过记录及人工理由。策略晋级不修改已有账户。

历史数据请求只登记资产、周期、UTC 时间段和本地磁盘配额。系统不会从没有授权的上游补抓历史数据。刷新请求返回 `DATA_UNAVAILABLE`、`DATA_INCOMPLETE` 或 `BACKTEST_READY`，并附事件数、盘口数、缺口数、估算字节数、过期时间及归档分片。只有 `BACKTEST_READY` 可以绑定到新的回测任务。

来源查看可用 /reference-prices、/data-quality、/orderbooks、/trades、/datasets。GET /analytics 以账户为单位统计，NULL 为样本或分母不足。POST /risk/halt 全局暂停模拟账户，POST /risk/release 需要 reason，随后按账户恢复。完整有效字段以 OpenAPI 端点为准。
