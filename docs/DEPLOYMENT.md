# 单机部署

## 下载免克隆部署包

主机需要 Docker Engine、Docker Compose、Python 3、curl、OpenSSL 和 shasum。GitHub Release 的 `polyquant-deploy.tar.gz` 包含 Compose、主机脚本及服务配置。数据库迁移和应用程序随同版本的 GHCR 镜像提供。

在新机上，先将当前用户可写的目录建为 `/opt/polyquant`。已有部署使用原项目目录。以下命令下载最新正式版。固定版本时可把地址中的 `latest/download` 换为 `download/vX.Y.Z`。如果曾定制包内的 Nginx、Grafana 或 systemd 文件，先解压到临时目录，将本地配置合入候选版本。

```sh
cd /opt/polyquant
bundle=$(mktemp)
curl -fLsS https://github.com/AmazingZ-sys/polymarket/releases/latest/download/polyquant-deploy.tar.gz -o "$bundle" && tar -xzf "$bundle"
rm -f "$bundle"
```

**新安装。** 安装脚本从包内 `.env.example` 创建权限为 0600 的 `.env`，生成数据库和管理员密码。模板中的 `APP_VERSION` 和小写 `IMAGE_PREFIX` 已对应本次发布。

```sh
scripts/install.sh
${EDITOR:-vi} .env
docker compose config -q
scripts/start.sh
scripts/healthcheck.sh
```

本机入口为 `http://127.0.0.1:8080`。使用域名时，将 `PUBLIC_ORIGIN` 设为浏览器访问的 HTTPS 源站，设置 `COOKIE_SECURE=true`，配置宿主机 Nginx 的域名与证书，运行 `nginx -t` 后 reload。示例配置位于 `infrastructure/nginx/host.conf`。数据库与 Redis 使用内部 Compose 网络。Vercel 前端的同源反代设置见 [管理终端](CONSOLE.md)。

**已有部署。** 解包只会更新 `.env.example` 和发行文件。编辑原 `.env`，按需补入新的采集配置，保留项目名、旧 `APP_VERSION` 和现有凭证。若由本地构建改用发布镜像，请先拉取或加载与旧版本对应的三种 GHCR 镜像，再将 `IMAGE_PREFIX` 改为包内的小写 GHCR 前缀，以便使用该版本回滚。升级脚本在预演通过后写入目标版本。

```sh
target=$(sed -n 's/^APP_VERSION=//p' .env.example)
scripts/update.sh "$target"
```

## 15 GiB GCE 部署

总持久盘预算为 15 GiB，包含系统、Docker 镜像、日志、数据库、WAL、归档及临时文件。一个分配示例为系统与镜像 5 GiB、数据库 3 GiB、归档 3 GiB、备份暂存与其他日志 1 GiB、升级及应急余量 3 GiB。用现场读数规划采集时长，并把较大的回放或恢复预演放在另一台机器上。

**免克隆部署包默认关闭行情采集。** 已有部署需要在原 `.env` 中显式设置 `COLLECTOR_REPLICAS=0` 才能切换到按需采集。若有运行中的 PAPER 账户，先通过管理端暂停。部署包中的低资源配置如下。

```dotenv
COLLECTOR_REPLICAS=0
COLLECTOR_PROFILE=free
COLLECTOR_DAILY_EVENT_BUDGET_BYTES=30000000
COLLECTOR_MIN_FREE_BYTES=2147483648
COLLECTOR_MAX_DISK_USAGE_BYTES=16106127360
```

数据库就绪后，执行 `scripts/collect-data.sh 300` 采集五分钟，结束后容器退出。参数为秒数，默认 300。时间窗口内磁盘保护也保持有效。需要连续采集时，将 `.env` 中的 `COLLECTOR_REPLICAS` 改为 `1`，执行 `docker compose up -d collector`。改回 `0` 后再次执行同一命令即可停止。免费档默认关闭 Kraken 逐笔参考流，降低常规盘口和 RAW 写入频率。30,000,000 字节是每天事件载荷的估算预算。达到预算后，低优先级事件暂停入库，关键事件仍可保留。按实际持久化的市场区间提交回测，操作见 [历史回测](BACKTEST.md)。

**磁盘保护。** collector 监测挂载于 `/data/parquet` 的归档文件系统。设置 15 GiB 已用上限时，采集器预留 1 GiB。文件系统已用约 14 GiB 或剩余空间降至 2 GiB 时，采集器暂停全部行情连接。系统、数据库、归档和备份仍可写入。该策略可覆盖共用这一 Docker 文件系统的卷。分盘部署时分别监测各挂载点，在 GCE 实例存储配置中限制总容量。

### 容量、流量与验收

```sh
docker_root=$(docker info --format '{{.DockerRootDir}}')
df -h / "$docker_root"
df -i / "$docker_root"
docker system df
docker compose ps -a
docker compose stats --no-stream
docker compose exec -T ops df -h /data/parquet
docker compose exec -T postgres df -h /var/lib/postgresql/data
scripts/healthcheck.sh
```

采集副本为零时，`docker compose ps --status running -q collector` 应无输出。启用采集时，检查容器 NET I/O 增量、数据源最后成功时间与 GAP 记录。容器计数也包含与 PostgreSQL 的内部通信。对照 VM 默认网卡的收发字节数，两次观测的差值可用于估算整机流量。

```sh
iface=$(ip route show default | awk 'NR==1 {print $5}')
date -u
cat "/sys/class/net/$iface/statistics/rx_bytes" "/sys/class/net/$iface/statistics/tx_bytes"
```

部署验收包括 `/healthz`、`/readyz`、账本对账、HTTPS 管理登录和数据源状态。内网指标可从 API 容器读取。

```sh
docker compose exec -T api node -e "fetch('http://localhost:3000/metrics').then(r=>r.text()).then(console.log)"
```

常驻服务日志按每文件 10 MiB、最多四个文件轮转。Redis 缓存上限为 96 MiB。ops 每十分钟归档，校验 SHA-256 后可转移超过 24 小时的合格 RAW 热数据。标准化事件、索引、账本和 Parquet 仍需空间。保留机器外已校验的备份，容量告警时先停采集，再检查已复制的本地备份、构建缓存及闲置镜像。保留清单引用的 Parquet，备份和原始事件检索都依赖这些文件。回滚镜像、备份验证与隔离恢复见 [运维与恢复](OPERATIONS.md)。

需要额外面板时运行 `docker compose --profile monitoring up -d prometheus grafana`。Grafana 绑定本机 `127.0.0.1:3300`，可以通过 SSH 隧道访问。

### 发布流水线

Pull Request 验证源码、迁移、依赖、真实数据库和 Chromium 管理流程，并对本地构建镜像进行扫描。main 提交通过验证后，按可达语义标签及 Conventional Commit 推导版本。首版取根 package.json 的 version。feat 提升次版本，fix、perf、revert 提升补丁，破坏性提交提升主版本。

构建任务分别发布 API、前端和 Python worker 的提交 SHA 镜像，校验 linux/amd64 和 linux/arm64 两个 manifest，并在扫描对应架构的实际镜像后晋升语义版本。Release notes 列出三个镜像的内容摘要。失败后重试会复用同一提交的暂存镜像。已有版本标签与另一份 manifest 冲突时，发布任务报错。首次发布需让仓库的 Actions 有权写入对应 GHCR 包和仓库 Release；工作流使用 GitHub 颁发的短期任务令牌。
