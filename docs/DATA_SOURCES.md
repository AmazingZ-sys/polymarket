# 数据来源与可用性

查阅及实测日期 2026-11-26。成功诊断体现当时本机出口的状态。各部署需记录自身的连接状态、数据年龄及质量事件。[原始连接探测](evidence/2026-11-26/connections.json)

| 来源 | 研究用途 | 实测 / 权限 |
| --- | --- | --- |
| Gamma /markets/slug/{slug} | 事件发现、系列配置、结果与费用 | BTC/ETH 四种组合 200，另存一条较早已结算市场。服务端页面可缓存，保存 updatedAt。 |
| CLOB public market WebSocket | 两枚结果 token 的 L2 快照、增量、成交 | 01:23:36 UTC 订阅返回真实 BTC L2 帧，无签名。独立的 REST /book 和 /time 返回 403。 |
| Data API v2 /trades | 可选公共成交交叉核查 | 通过 condition 精确查询，返回 403，可用性受限。接口采用 data/pagination 和 cursor。 |
| Chainlink Data Streams、Polymarket PolyBolt | 官方 BTC/USD、ETH/USD TWAP | 需授权，当前没有 provider key。历史和实时原生预言机报告均标为 UNAVAILABLE。 |
| Binance Spot bookTicker | 可选外部 BTCUSDT/ETHUSDT 参考买卖价 | BTCUSDT 451，停止该数据源。USDT 对 USD 基差须单独评估。 |
| Coinbase Exchange 产品 ticker | 可选独立 BTC-USD/ETH-USD 参考价 | BTC-USD 403，未验证完整性。 |
| Gamma 事件 eventMetadata | 已公开的结算基准和终值快照 | 在历史样本读到两个字段。它们只供事后核对，保留 received_at。 |

发现利用可预测的当前、上一期与下一期 slug 定位候选，复核结构化市场配置。尚未结算的已知市场由数据库驱动复查。slug 格式变更须发出覆盖缺口，不对标题做宽松猜测。[市场发现](https://docs.polymarket.com/market-data/discover-markets.md) · [按 slug 查询](https://docs.polymarket.com/api-reference/markets/get-market-by-slug.md)

公共 WS 订阅 `assets_ids` 后处理首次 book、price_change、last_trade_price 和 tick_size_change。每 10 秒发送文本 PING，超时后重新订阅并等待全量快照。公开期内限额示例包括 Gamma markets 300 次/10s、CLOB book 1500 次/10s、Data v2 trades 300 次/10s。采集器应限速并尊重 Retry-After；403/451 作为访问受限记录。官方 SDK 的路径定义是交叉检查资料。[实时协议](https://docs.polymarket.com/market-data/realtime-data.md) · [限流](https://docs.polymarket.com/api-reference/rate-limits.md) · [公开订单簿](https://docs.polymarket.com/api-reference/market-data/get-order-book.md) · [Data v2](https://docs.polymarket.com/api-reference/feeds/list-trades.md) · [官方开源代码](https://github.com/Polymarket/py-clob-client-v2/blob/main/py_clob_client_v2/endpoints.py)

Chainlink 直连报告需按供应商授权取数。当前文档将 RTDS 集成导向 PolyBolt，TWAP 要求平台认证和 Chainlink provider key。项目内无钱包签名。未获得的报告保持缺失，不用外部 spot 价格重构官方值。[Chainlink Data Streams](https://docs.chain.link/data-streams) · [Polymarket 实时流](https://docs.polymarket.com/market-data/realtime-data.md)

公共行情也受服务条款和授权范围约束，采集成功无法确认商用再分发权。当前数据集第三方再分发授权 UNKNOWN。部署方根据实际地区、用途和各自许可配置来源。禁止绕过地域或认证要求。Binance 和 Coinbase 均为参考行情，须保留交易对、价格源及陈旧度。[Polymarket 条款](https://polymarket.com/tos) · [地区规则](https://docs.polymarket.com/api-reference/geoblock.md) · [Binance Spot](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints) · [Coinbase ticker](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-ticker)
