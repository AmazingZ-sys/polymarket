# 虚拟账户与账务

用户创建 PAPER 或 SHADOW 账户，资金默认 1000 USD。每个账户有独立的订单、成交、持仓、流水、权益和风险记录。创建时保存策略版本、筛选条件、撮合参数的 JSON 快照和摘要。克隆从相同配置和初始资金开始。

| 操作 | 允许来源 | 结果 |
|---|---|---|
| start | CREATED | STARTING，经账务检查后 RUNNING |
| pause | RUNNING、STARTING | PAUSED，撤销在途订单并解冻 |
| resume | PAUSED、RISK_HALTED、ERROR | STARTING，再次核对状态 |
| stop | CREATED、STARTING、RUNNING、PAUSED、RISK_HALTED、ERROR | STOPPING，撤单后 STOPPED |
| archive | STOPPED 且无未结持仓 | ARCHIVED，历史可查 |

冻结与解冻通过同时变动可用余额和冻结余额实现。成交从冻结额扣除本金和费用，释放余款。每个市场、账户、方向只保留一个汇总持仓。确认结算后在同一事务内兑付、记录成本及已实现盈亏，结算主键由账户和市场组成。暂停和停止仍接收官方结算。

每个命令通过幂等键及请求内容绑定。订单、成交、游标和账务同事务提交。恢复后检查流水与余额相等，取消跨越服务中断的在途订单，并要求重新观察盘口。SHADOW 仅写信号。相同盘口下的多个账户属于独立反事实实验。
