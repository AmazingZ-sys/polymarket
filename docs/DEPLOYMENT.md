# 单机部署

需要 Docker Engine、Compose v2+、磁盘持久卷和可用的 HTTPS 域名。运行 scripts/install.sh 初始化本地环境，再运行 scripts/start.sh 和 scripts/healthcheck.sh。端口默认只监听 127.0.0.1:8080。安装不会停止其他 Compose 项目。

将 infrastructure/nginx/host.conf 中的域名及证书路径改为实际值，配置域名解析和受信证书，测试 nginx -t 后 reload。HTTPS 部署设置 PUBLIC_ORIGIN 并启用 COOKIE_SECURE。可使用 monitoring profile 启动 Prometheus 与 Grafana。

Vercel 使用 apps/web 的构建配置，将 /api 同源反代到自己的 HTTPS Hono 服务。数据库、任务、SSE 长连接和采集器运行在服务器。发布镜像以明确版本部署，见 OPERATIONS.md。

## GCE 低磁盘 VM 剖面

单台 Google Compute Engine VM 可以承载默认实时研究栈。低磁盘剖面将总持久盘目标限制在 15 GiB，适合短时间采集、控制台和按需小窗口回测。它不适合无限期保存高频原始事件。将项目放在持久磁盘上的 `/opt/polyquant`，GCE 防火墙放行 HTTPS，若启用 HTTP 到 HTTPS 的重定向再放行 80，8080、3300、5432 和 6379 保持本机监听。管理员通过 IAP 或 SSH 隧道访问本机端口。

`google-vm-low-disk` 是运维记录中的配置剖面名称，不是应用环境变量。低磁盘剖面默认只启动 PostgreSQL、Redis、collector、engine、ops、api 和 web，不启动 `monitoring` profile。15 GiB 预算按下表核对，实际安装后以 `df -h` 和 `docker system df` 为准。

| 预算项 | 目标 |
| --- | ---: |
| 系统、Docker 引擎和镜像层 | 4 GiB |
| PostgreSQL `pgdata` | 3 GiB |
| Parquet `archives` | 3 GiB |
| 本地备份、当前镜像和上一份回滚镜像 | 2 GiB |
| 更新预演与紧急余量 | 3 GiB |
| 合计 | 15 GiB |

15 GiB 的目标依赖外部备份和短期数据窗口。归档增长或升级预演需要更多空间时，先迁移到更大的持久盘。不要通过删除卷来满足预算。

需要在现场记录以下 `.env` 值，密码和令牌继续保留在文件中，不放入 shell 历史或工单。15 GiB 机器把采集器设为 `free`，让每日事件估算预算为 30,000,000 bytes，在归档目录所在文件系统已用空间达到 15 GiB 或剩余空间低于 2 GiB 时停止非关键事件写入。

| 配置 | 低磁盘剖面建议 | 作用 |
| --- | --- | --- |
| `IMAGE_PREFIX` | `ghcr.io/AmazingZ-sys/polymarket` | 使用已发布镜像，避免在 VM 构建 |
| `APP_VERSION` | 明确的语义版本，例如 `0.1.0` | 固定可回滚版本 |
| `WEB_PORT` | `8080` | 仅向本机 Nginx 提供服务 |
| `PUBLIC_ORIGIN` | `https://research.example.org` | 会话来源校验 |
| `COOKIE_SECURE` | `true` | HTTPS 会话 cookie |
| `COMPOSE_PROFILES` | 留空 | 默认不启动 monitoring |
| `GRAFANA_PASSWORD` | 仅在启用 monitoring 时设置 | 监控面板凭证 |
| `POLYBOLT_API_KEY`、`POLYBOLT_API_SECRET`、`POLYBOLT_PASSPHRASE`、`CHAINLINK_API_KEY` | 没有授权时留空 | 未授权的数据源保持缺失 |

采集器从这些环境变量读取市场范围、采样周期、事件估算预算和剩余空间阈值。归档热保留、低磁盘通知和单队列回测分别由 ops worker、Compose 和 engine worker 的既有行为提供，现场配额通过本节的操作步骤执行。

在 VM 上初始化并检查配置。

```sh
cd /opt/polyquant
cp .env.example .env
scripts/install.sh
chmod 600 .env
${EDITOR:-vi} .env
docker compose config -q
```

低磁盘 VM 的最小采集配置如下。

```dotenv
COLLECTOR_PROFILE=free
COLLECTOR_DAILY_EVENT_BUDGET_BYTES=30000000
COLLECTOR_MIN_FREE_BYTES=2147483648
COLLECTOR_MAX_DISK_USAGE_BYTES=16106127360
COLLECTOR_ENABLE_REFERENCE_TRADES=false
```

达到每日事件预算后，采集器继续保留 GAP、METADATA、RESOLUTION 和成交证据，并停止大部分 BOOK、RAW、REFERENCE 写入。实时 PAPER 账户可能因此进入过期或风险暂停状态。15 GiB 机器把低资源档用于 SHADOW 观察和短时采集，回测使用已校验的数据集。

低磁盘剖面的容量边界来自当前部署实现。Compose 将每个服务的 JSON 日志限制为每个文件 10 MiB、最多 4 个文件。Redis 通过 `maxmemory 96mb` 和 `allkeys-lru` 限制可重建缓存。ops 每 10 分钟归档一次，原始包至少保留 24 小时热副本。更新脚本在切换前要求当前目录至少有 2 GiB 可用空间，因为升级预演需要备份和隔离数据库。ops worker 在归档目录剩余空间低于 1 GiB 或低于总量的 5% 时写入低磁盘通知。

低磁盘 VM 采用以下操作配额。

- 同时只保留一个 `QUEUED` 或 `RUNNING` 回测。engine worker 按创建时间逐项处理回测，多个大任务会占用同一个写入循环。
- 默认不启用 Prometheus 与 Grafana。启用前先确认监控卷有独立余量，并在确认面板可用后设定本地保留周期。
- 更新期间保留当前版本和上一份已验证回滚镜像。候选版本健康检查通过后，再清理更早的未使用镜像。
- 每日备份完成后将目录复制到机器外的受控存储，并保留至少两份已校验的本地备份。组织有更长留存要求时以外部存储为准。

### 15 GiB 告警与停止策略

每次部署、回测提交和归档任务前检查根盘与 Docker 数据目录。采集器的绝对写入上限为已用 15 GiB，运维处置仍使用以下剩余空间阈值。

- 剩余空间低于 2 GiB 时发出告警，停止提交新的回测，停用 monitoring，并完成外部备份或迁移准备。
- 剩余空间低于 1 GiB 时进入严重状态，执行 `docker compose stop collector`，暂停新的行情写入。该阈值与 ops worker 的低磁盘通知一致。
- 剩余空间低于 512 MiB 时停止 `collector` 和 `engine`，保留 PostgreSQL、API 和 ops 供检查，避免继续生成事件、回测和日志。清理或扩盘完成后再恢复服务。

### 低磁盘清理顺序

先观察再清理，避免删除账务和归档引用。

```sh
df -h / /var/lib/docker
df -i / /var/lib/docker
docker system df
docker compose ps -a
```

本地备份目录可以在已完成异地复制并校验 `SHA256SUMS` 后按留存策略删除。只删除明确的备份目录，保留最近两份已验证备份。

```sh
find backups -mindepth 1 -maxdepth 1 -type d -printf '%TY-%Tm-%Td %TH:%TM %p\n' | sort
```

候选发布已经通过健康检查后，可以清除悬空镜像和一周以前的构建缓存。不要使用会删除卷的全局清理命令。

```sh
docker image prune -f
docker builder prune -f --filter until=168h
docker system df
```

`pgdata` 和 `archives` 卷中的文件由数据库索引、SHA-256 清单和原始事件引用。不得用 `find ... -delete` 直接删除 Parquet 或 PostgreSQL 数据。归档卷接近满时，先停止 collector，在更大的持久磁盘上按 BACKTEST.md 的导出与恢复流程建立已校验副本，再切换卷。删除旧归档前必须保留对应的 `dataset_versions` 和 `archived_raw_events` 证据，当前版本没有无损的在线归档删除命令。

### GCE 流量与容量观察

从本机或 IAP SSH 隧道观察服务，不把管理端口暴露到公网。基础状态和业务来源可以这样检查。

```sh
scripts/status.sh
scripts/logs.sh collector api web
curl -fsS http://127.0.0.1:8080/healthz
curl -fsS http://127.0.0.1:8080/readyz
curl -fsS http://127.0.0.1:8080/metrics
docker stats --no-stream
docker compose exec -T postgres psql -U pq -d polyquant \\
  -c 'SELECT source,status,last_success_at,last_error FROM data_source_status ORDER BY source'
```

需要查看本机 Grafana 时，先显式启用 monitoring，再通过 IAP 或 SSH 转发 3300。该端口仍只绑定 `127.0.0.1`。

```sh
docker compose --profile monitoring up -d prometheus grafana
gcloud compute ssh <vm-name> --zone <zone> -- -L 3300:127.0.0.1:3300
```

Prometheus 每 30 秒抓取 API 的 `polyquant_last_event_sequence`、`polyquant_running_accounts` 和 `polyquant_sources_non_ok`。低磁盘剖面要同时观察 `df -h`、`docker stats`、collector 最近日志和数据源最后成功时间。API、web 与 host Nginx 的访问日志只用于流量核对，不得替代行情质量和账务检查。

### 部署验收

每次新机部署、版本更新或更换持久磁盘都执行一次完整验收。

```sh
cd /opt/polyquant
docker compose config -q
scripts/start.sh
scripts/healthcheck.sh
curl -fsS https://research.example.org/healthz
curl -fsS https://research.example.org/readyz
curl -fsS https://research.example.org/metrics
nginx -t
```

确认 `scripts/status.sh` 中 postgres、redis、collector、engine、ops、api 和 web 均为 running 或 healthy。`scripts/healthcheck.sh` 必须通过就绪检查、账本连续性检查，并输出每个数据源的状态。首次部署和每次迁移后执行一次备份，核对生成目录中的 `SHA256SUMS`。在维护窗口将一份备份恢复到隔离数据库和归档卷，确认 ledger-check、归档摘要和账户余额一致后，再把该备份标记为可恢复点。部署验收记录中填写版本、VM 名称、磁盘剩余量、数据源状态、备份目录和 UTC 时间。

## 已发布版本

从项目的 GitHub Releases 获取标签对应的源码。运行 install.sh 后，将 .env 的 IMAGE_PREFIX 设为 ghcr.io/amazingz-sys/polymarket，APP_VERSION 设为发布号（例如 0.1.0）；部署脚本按 -api、-web、-worker 三个镜像名拉取。保留初始 IMAGE_PREFIX=polyquant 时从当前检出的代码构建。版本升级执行 scripts/update.sh <版本>。更新会在完整备份恢复的临时库上预演迁移，然后检查服务和流水。回滚前请核查对应标签迁移兼容性，破坏性变更请使用隔离恢复后的库与同批次归档。

需启用观测面板时，在完成默认部署后运行 docker compose --profile monitoring up -d prometheus grafana，Grafana 可经 127.0.0.1:3300 访问。宿主机 Nginx 只在修改其配置后需要 reload。Vercel 在 apps/web 配置 PQ_API_ORIGIN 并将服务端 PUBLIC_ORIGIN 设置为前端域名，见 CONSOLE.md。服务进程及本机 HTTPS 终结分别管理。

### 发布流水线

Pull Request 验证源码、迁移、依赖、真实数据库和 Chromium 管理流程，并对本地构建镜像进行扫描。main 提交通过验证后，按可达语义标签及 Conventional Commit 推导版本。首版取根 package.json 的 version。feat 提升次版本，fix、perf、revert 提升补丁，破坏性提交提升主版本。

构建任务分别发布 API、前端和 Python worker 的提交 SHA 镜像，校验 linux/amd64 和 linux/arm64 两个 manifest，并在扫描对应架构的实际镜像后晋升语义版本。Release notes 列出三个镜像的内容摘要。失败后重试会复用同一提交的暂存镜像。已有版本标签与另一份 manifest 冲突时，发布任务报错。首次发布需让仓库的 Actions 有权写入对应 GHCR 包和仓库 Release；工作流使用 GitHub 颁发的短期任务令牌。
