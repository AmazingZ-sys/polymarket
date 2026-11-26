# 运维与恢复

## GCE 低磁盘 VM 运维剖面

低磁盘 VM 使用 `docs/DEPLOYMENT.md` 中的 `google-vm-low-disk` 配置剖面。该名称用于值班记录，不改变 Compose 的服务行为。默认只启动实时研究栈，按需启用 monitoring profile。所有时间使用 UTC，磁盘观察同时覆盖宿主机根盘、Docker 数据目录和 `archives` 卷。

15 GiB 总盘目标按系统与 Docker 4 GiB、`pgdata` 3 GiB、`archives` 3 GiB、本地备份和两个应用镜像 2 GiB、更新预演与紧急余量 3 GiB 预算。该剖面适用于短时间采集和小窗口回测。归档增长或升级预演需要更多空间时先迁移到更大的持久盘，不能删除卷来满足预算。

当前容量边界需要纳入值班检查。Compose 日志按每个文件 10 MiB、最多 4 个文件滚动。Redis 上限为 96 MiB。ops 每 10 分钟归档，原始包至少保留 24 小时热副本。更新脚本在预演前要求至少 2 GiB 可用空间。ops worker 在归档目录低于 1 GiB 或低于总量 5% 时产生低磁盘通知。低磁盘环境只安排一个回测任务，避免研究回放与实时模拟争用同一 engine 写入循环。

15 GiB 剖面采用分级处置。剩余空间低于 2 GiB 时告警并停止新的回测，低于 1 GiB 时执行 `docker compose stop collector`，低于 512 MiB 时再停止 `engine`。停写后先把已校验的备份和 Parquet 清单复制到外部存储，再扩盘或迁移归档卷。恢复前重新执行容量、账本和数据源验收。

每日备份通过 `polyquant-backup.timer` 运行。备份目录复制到机器外后才进入留存周期，至少保留两份本地已校验备份。释放空间时先查看 `df -h`、`df -i` 和 `docker system df`，再清理已确认不需要的悬空镜像、过期构建缓存和已异地复制的旧备份。不得删除 `pgdata`、`archives` 或仍被 `dataset_versions` 引用的 Parquet 文件。归档卷接近满时停止 collector，在更大卷上完成导出、恢复和哈希核验后再切换。

流量与数据源观察使用 `scripts/status.sh`、`scripts/logs.sh collector api web`、`/healthz`、`/readyz` 和 `/metrics`。另外查询 `data_source_status` 的最后成功时间和错误。监控面板只有在磁盘预算充足时启用，Prometheus 的抓取周期为 30 秒。GCE 管理端口通过 IAP 或 SSH 隧道访问，8080、3300、5432 和 6379 不对公网开放。

部署或版本切换后，运行 `docker compose config -q`、`scripts/start.sh` 和 `scripts/healthcheck.sh`。确认全部核心服务健康、账本校验通过、每个数据源状态可解释，并执行 `nginx -t` 与 HTTPS `/healthz`、`/readyz`、`/metrics` 检查。每次迁移和更换磁盘后建立一份新备份，在维护窗口恢复到隔离数据库和归档卷，核对账本、账户余额、归档清单与 SHA-256 后再将该备份标记为恢复点。

用 scripts/status.sh 查看服务，scripts/logs.sh 查看最近日志。健康检查区分 API 可响应、数据库就绪及数据供应商是否可访问。数据中心展示源最后成功时间和最近错误。过期输入时只允许观察与账务保护。

通过 backup.sh 生成数据库和归档副本，校验哈希，把副本转移到受控的独立存储。restore.sh 面向新的隔离库/卷；验证各账户流水、持仓和原始数据后再切换。更新前记录原镜像版本、做备份并检查剩余空间，部署后核查就绪、账务与行情。rollback.sh 只切换应用镜像，数据迁移兼容性需要逐版本核查。停止服务保留卷。

审计来源受限、无历史观察、无成交、无标记价格等状态。模拟净收益的解释必须交待数据覆盖、费用、实际结算和置信区间。管理员凭证、访问令牌和备份内容不可写入工单。

## 周期与通知

维护进程通过数据库行锁领取到期任务；单项任务事务失败时登记失败原因和下一次时间。日报、周报和月报按上一个完整 UTC 期间生成。风险事件重复时提出待审假设，配置修改仍需要新版本。后台的系统监控可手动触发维护任务。

通过 .env 配置 Telegram Bot 和 chat ID，或 HTTPS Webhook。发送方采用持久化 outbox、退避、限速与业务事件键。接收方应按 event_id 去重，网络在确认时中断可能导致重复通知。自动备份在宿主机运行。按实际仓库位置修改 infrastructure/systemd/polyquant-backup.service 后安装该服务及同名 timer，运行 systemctl enable --now polyquant-backup.timer。备份应放在机器外的受控存储。

## 升级检查点与恢复验证

`scripts/update.sh 0.2.0` 默认拉取配置的 GHCR 版本。已通过离线分发载入同一组 API、Web、Worker 镜像时可用 `scripts/update.sh 0.2.0 --local-images`。更新和回滚共用进程互斥锁。升级先从已校验备份创建临时库，在目标镜像执行真实迁移、校验账本，并检查运行库迁移指纹。预演关闭角色配置以隔离 PostgreSQL 集群级密码。通过后才切换版本，并写入本地回滚点。

对于新迁移，镜像可在 `database/rollback-compatibility.json` 记录已审阅的后向兼容性。顶层键为上一个应用版本，值为「新增迁移文件名 → SHA-256」对象。没有新迁移时无需该文件。破坏性迁移应在隔离恢复的数据库和卷上完成验证并安排切换。自动回滚仅在实际数据库指纹与已验证的升级前或升级后指纹一致时启动。回滚中出现健康检查失败时，命令返回非零；保留备份和原始回滚点供恢复。

备份从 pg_dump 自身的数据快照提取 Parquet 清单，然后按清单打包并逐个验证 SHA-256。恢复在新数据库和新卷中校验归档摘要、每条流水的余额连续性与账户总账。隔离恢复库不还原旧集群的 ACL，切换新库前使用部署对应的迁移服务配置运行角色和权限。主体库及归档卷的切换由操作员在维护窗口完成。
