# 单机部署

需要 Docker Engine、Compose v2+、磁盘持久卷和可用的 HTTPS 域名。运行 scripts/install.sh 初始化本地环境，再运行 scripts/start.sh 和 scripts/healthcheck.sh。端口默认只监听 127.0.0.1:8080。安装不会停止其他 Compose 项目。

将 infrastructure/nginx/host.conf 中的域名及证书路径改为实际值，配置域名解析和受信证书，测试 nginx -t 后 reload。HTTPS 部署设置 PUBLIC_ORIGIN 并启用 COOKIE_SECURE。可使用 monitoring profile 启动 Prometheus 与 Grafana。

Vercel 使用 apps/web 的构建配置，将 /api 同源反代到自己的 HTTPS Hono 服务。数据库、任务、SSE 长连接和采集器运行在服务器。发布镜像以明确版本部署，见 OPERATIONS.md。

## 已发布版本

从项目的 GitHub Releases 获取标签对应的源码。运行 install.sh 后，将 .env 的 IMAGE_PREFIX 设为 ghcr.io/amazingz-sys/polymarket，APP_VERSION 设为发布号（例如 0.1.0）；部署脚本按 -api、-web、-worker 三个镜像名拉取。保留初始 IMAGE_PREFIX=polyquant 时从当前检出的代码构建。版本升级执行 scripts/update.sh <版本>。更新会在完整备份恢复的临时库上预演迁移，然后检查服务和流水。回滚前请核查对应标签迁移兼容性，破坏性变更请使用隔离恢复后的库与同批次归档。

需启用观测面板时，在完成默认部署后运行 docker compose --profile monitoring up -d prometheus grafana，Grafana 可经 127.0.0.1:3300 访问。宿主机 Nginx 只在修改其配置后需要 reload。Vercel 在 apps/web 配置 PQ_API_ORIGIN 并将服务端 PUBLIC_ORIGIN 设置为前端域名，见 CONSOLE.md。服务进程及本机 HTTPS 终结分别管理。

### 发布流水线

Pull Request 验证源码、迁移、依赖、真实数据库和 Chromium 管理流程，并对本地构建镜像进行扫描。main 提交通过验证后，按可达语义标签及 Conventional Commit 推导版本。首版取根 package.json 的 version。feat 提升次版本，fix、perf、revert 提升补丁，破坏性提交提升主版本。

构建任务分别发布 API、前端和 Python worker 的提交 SHA 镜像，校验 linux/amd64 和 linux/arm64 两个 manifest，并在扫描对应架构的实际镜像后晋升语义版本。Release notes 列出三个镜像的内容摘要。失败后重试会复用同一提交的暂存镜像。已有版本标签与另一份 manifest 冲突时，发布任务报错。首次发布需让仓库的 Actions 有权写入对应 GHCR 包和仓库 Release；工作流使用 GitHub 颁发的短期任务令牌。
