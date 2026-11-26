# 结算规则与验证范围

查阅及探测日期为 2026-11-26。时间统一为 UTC。四次独立响应均为 HTTP 200。表内结果仅适用于对应市场，下一期需重新解析规则、token 配对、费用与精度。原始响应、采集时间以及 SHA-256 可逐一核查。[证据清单](evidence/2026-11-26/manifest.json)

| 市场 | Gamma ID | 观测期 UTC | 官方结算源与规则 | 样本费率 / tick / 最小股数 | 独立响应 |
| --- | --- | --- | --- | --- | --- |
| BTC 5m | 7332543 | 01:20–01:25 | BTC/USD TWAP 60s，终值 ≥ 起值为 UP | 0.05 / 0.01 / 5 | [Gamma](https://gamma-api.polymarket.com/markets/slug/btc-updown-5m-1795656000) |
| BTC 15m | 7332529 | 01:15–01:30 | BTC/USD TWAP 60s，终值 ≥ 起值为 UP | 0.05 / 0.01 / 5 | [Gamma](https://gamma-api.polymarket.com/markets/slug/btc-updown-15m-1795655700) |
| ETH 5m | 7332544 | 01:20–01:25 | ETH/USD TWAP 60s，终值 ≥ 起值为 UP | 0.01 / 0.01 / 5 | [Gamma](https://gamma-api.polymarket.com/markets/slug/eth-updown-5m-1795656000) |
| ETH 15m | 7332533 | 01:15–01:30 | ETH/USD TWAP 60s，终值 ≥ 起值为 UP | 0.01 / 0.01 / 5 | [Gamma](https://gamma-api.polymarket.com/markets/slug/eth-updown-15m-1795655700) |

每期分别核对结构化字段 `cryptoMarketConfig` 的 asset、duration、twapEnabled、twapLookbackSeconds，以及 `resolutionSource`、`description`、时间边界、结果对、合约 ID、`negRisk=false`、`resolutionMechanism=oracle`。事件的 `eventStartTime` 与 `endDate` 给出研究区间。市场可能在开始前一日已创建，`startDate` 和 `acceptingOrdersTimestamp` 都早于观测区间。

开盘时刻取同一 Chainlink 流的 TWAP 为基准，结束时刻取该流的 TWAP 为终值，低于基准为 DOWN。60 秒为本次四个流配置的回看长度。精确边界取样、缺报补偿及生产端插值细节为 UNVERIFIED。缺少官方基准或观察值时禁止生成尾盘可交易信号。Gamma 的事件元数据可能在结算后才首次出现 `priceToBeat` 和 `finalPrice`，回测只从实际接收时刻开始使用。[结算说明](https://docs.polymarket.com/concepts/resolution.md) · [BTC 源](https://data.chain.link/streams/btc-usd-twap-60s-streams) · [ETH 源](https://data.chain.link/streams/eth-usd-twap-60s-streams)

`id` 标识 Gamma 市场，`conditionId` 标识结果条件。解析同长度的 `outcomes` 与 `clobTokenIds`，按相同索引映射 UP 和 DOWN。两枚 token 必须唯一且使用字符串，数据库不转换为浮点或 64 位整数。正常二元结算中每一股胜方兑付 1，负方为 0。[市场详情](https://docs.polymarket.com/market-data/market-details.md) · [结果代币](https://docs.polymarket.com/concepts/positions-tokens.md)

`endDate` 是预定结束时间，实际停止接单时间 UNKNOWN。达到 endDate、收到停止接单状态或发生质量异常均停止新模拟撮合。最终结算时间 UNKNOWN，分别记录平台报告的 closedTime、状态及本系统首次收到明确结果的时刻。四个 oracle 样本不套用 UMA 的争议周期。规则、来源、结果或兑付发生歧义时保留原始证据并冻结受影响市场。[结算文档](https://docs.polymarket.com/concepts/resolution.md)

另查历史市场 7329688。Gamma 返回 closed、acceptingOrders=false、umaResolutionStatus=resolved，UP/DOWN 价格为 0/1，平台报告的终值低于基准。可据此确认 DOWN。数据在本次采集之前已结算，对当时的可用性仍为 UNKNOWN。[历史原文](evidence/2026-11-26/btc-updown-5m-1795648800.json)

按股数乘以费率及 p(1−p) 计算基础费。费率取每市场当时的 feeSchedule，精度 5 位，maker 费为零。仅在已核实的 exponent=1、takerOnly=true 配置下使用该模型。费率、tick、最小量或规则变化应版本化。官方文档描述了 taker 费以及返还激励，研究模型默认不计任何返利。[费用](https://docs.polymarket.com/trading/fees.md)
