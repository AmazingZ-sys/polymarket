# PolyQuant Pro

BTC / ETH 五分钟、十五分钟预测市场的研究工作台。记录真实公开行情，在独立虚拟账户中观察策略与成本。默认进程仅采集；管理员显式启动账户后才运行实时模拟。研究状态为 INSUFFICIENT_DATA。

## 起步

先读 [市场实测](docs/MARKET_RULES.md) 和 [数据访问记录](docs/DATA_SOURCES.md)，再按 [部署手册](docs/DEPLOYMENT.md) 启动。管理后台显示空样本、数据断档、拒绝访问和未验证结算。

```sh
./scripts/install.sh
./scripts/start.sh
./scripts/healthcheck.sh
```

本地入口为 http://127.0.0.1:8080 ，首次管理员凭证保存在权限为 600 的 .env 中。打开模拟盘，选择 S00、不交易基准及初始虚拟资金，再启动实例。通过数据中心核查来源和接收时间，再解释信号或模拟收益。

## 工程与研究

- [原始需求](docs/PRD.md) · [实施路线](docs/ROADMAP.md) · [验收与进度](docs/TASKS.md)
- [架构](docs/ARCHITECTURE.md) · [数据库](docs/DATABASE.md) · [API](docs/API.md)
- [实验门槛](docs/RESEARCH.md) · [模拟撮合](docs/SIMULATION_MODEL.md) · [风险](docs/RISK_CONTROL.md)
- [测试](docs/TESTING.md) · [运维](docs/OPERATIONS.md) · [已知问题](docs/KNOWN_ISSUES.md)
