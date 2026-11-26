# 单机部署

需要 Docker Engine、Compose v2+、磁盘持久卷和可用的 HTTPS 域名。运行 scripts/install.sh 初始化本地环境，再运行 scripts/start.sh 和 scripts/healthcheck.sh。端口默认只监听 127.0.0.1:8080。安装不会停止其他 Compose 项目。

将 infrastructure/nginx/host.conf 中的域名及证书路径改为实际值，配置域名解析和受信证书，测试 nginx -t 后 reload。HTTPS 部署设置 PUBLIC_ORIGIN 并启用 COOKIE_SECURE。可使用 monitoring profile 启动 Prometheus 与 Grafana。

Vercel 使用 apps/web 的构建配置，将 /api 同源反代到自己的 HTTPS Hono 服务。数据库、任务、SSE 长连接和采集器运行在服务器。发布镜像以明确版本部署，见 OPERATIONS.md。
