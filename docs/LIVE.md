# Polymarket 国际站账户

模拟盘可以直接使用虚拟资金。要启用真实买单，操作者须具备国际站交易资格、已开通的 Deposit Wallet、可交易的 pUSD 及交易所要求的抵押品授权。先登录国际站，在个人菜单核对钱包地址，按网站为该账户显示的充值网络与币种操作。应用显示的单笔及累计预算约束买入本金，手续费另计。[官方入门](https://docs.polymarket.com/trading/quickstart) · [官方地域限制](https://docs.polymarket.com/api-reference/geoblock)

## 控制台配置

1. 在「模拟盘」创建并启动含 S01 的 PAPER 账户，选择需要跟随的策略版本。S01 在 BTC/ETH 的 5/15 分钟合约上寻找一对互补结果的买入机会。需要运行采集器、收到合格新盘口才能产生新信号。低资源部署可用 `scripts/collect-data.sh 300` 开一个五分钟采集窗口。
2. 以管理员身份打开「系统管理 → Polymarket 账户」，填写国际站 Deposit Wallet 的公开地址、对应模拟账户和策略版本。单笔上限约束每一条买腿，累计预算由同钱包的所有配置修订共享。保存配置后检查地区。谁可以交易还取决于操作者本人资格，服务出口检查只验证这台机器。
3. 在符合条件的执行主机上按下节配置会话，刷新账户状态。确认授权范围与金额后，由账户所有人勾选资格确认、点击启用。生效点之前的历史模拟信号不触发订单。修改配置需重新启用。
4. 用「停用」停止新增委托，已提交的委托继续对账。需撤回交易所授权时，由钱包所有人在官方端撤销该 Session Key。软件版本升级与回滚会停用已有授权，升级后需重新启动可选服务并在控制台确认。[官方会话撤销](https://docs.polymarket.com/trading/session-keys#revoke-a-session-key)

## 在执行主机安装会话

在钱包所有人控制的设备上创建独立 Session Key，仅授权 `CLOB`，记录到期时间。使用钱包所有人的 CLOB 凭证查询该钱包的授权列表，然后为会话签名人生成其自己的 CLOB 凭证。授权与撤销环节需要所有人对应的 Relayer API Key 或 Builder API Key。所有人签名私钥始终由所有人保管。[官方会话创建、授权及凭证派生](https://docs.polymarket.com/trading/session-keys)

在部署目录执行下列命令，安装脚本为专用数据库角色生成随机密码。将以下 JSON 的占位值替换为该钱包及会话的凭证，通过主机上的编辑器写入 `.runtime/polymarket-executor.json`。浏览器只接收公开地址和预算。

```sh
cd /opt/polyquant
scripts/install.sh --with-live-role
install -d -m 0700 .runtime
umask 077
vi .runtime/polymarket-executor.json
```

```json
{
  "wallet_address": "<Deposit Wallet 地址>",
  "owner_address": "<所有人签名地址>",
  "owner_api_key": "<所有人的 CLOB API key>",
  "owner_api_secret": "<所有人的 CLOB secret>",
  "owner_api_passphrase": "<所有人的 CLOB passphrase>",
  "session_address": "<Session Key 签名地址>",
  "session_private_key": "<仅限获授权 Session Key 的私钥>",
  "session_api_key": "<Session Key 的 CLOB API key>",
  "session_api_secret": "<Session Key 的 CLOB secret>",
  "session_api_passphrase": "<Session Key 的 CLOB passphrase>"
}
```

启动可选服务前，设置文件权限让执行器的 UID 10001 可以读取。普通服务不挂载这份文件。更换凭证文件后强制重建执行器，使单文件绑定挂载指向新文件。

```sh
chown 10001 .runtime/polymarket-executor.json
chmod 0400 .runtime/polymarket-executor.json
docker compose run --rm migrate
docker compose --profile live up -d --no-deps --force-recreate live-executor
```

## 读懂本金、费用与订单状态

执行器为双腿预留本金后才发送一批 FOK 买单。两腿可以独立成功或失败。预留本金占用累计预算，确认后的实际本金替换相应预留。手续费按实际扣费币种分别累计，缺少可归属的最终成交证据时显示「待确认」。显示的单笔和累计本金上限均不含手续费。

成交记录显示信号编号、两个委托号与各腿状态或金额。收到委托号后，执行器只读核对结果。遇到单腿敞口或不确定的提交会停用新单并保留相关预算。响应丢失且没有可验证委托号时，保留记录，使用钱包交易历史及交易所的成交记录核对。钱包所有人管理已有仓位与赎回。重新启用需要解决该钱包的未决记录。[官方订单生命周期](https://docs.polymarket.com/concepts/order-lifecycle)
