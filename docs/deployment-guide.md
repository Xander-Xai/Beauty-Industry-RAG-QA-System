# 化妆品行业 RAG 问答系统部署手册

## 0. 版本语义

本手册描述的运行配置以 `config.json` → `system.version` 为准（当前为 `2.3.0`）。仓库中部分文档与文件名使用的 “v2.5” 是 **历史 working milestone / development-phase 标签**，不是正式发布版本，也不改变本手册的运行时契约；详见 [版本策略](repository-truth-audit.md#version-policy)。

## 1. 当前推荐部署路径

当前仓库推荐先走 `app.py` 单体部署，再逐步补微服务。原因：

- React 前端默认联调的是单体 `/api/*` 接口
- `Dockerfile`、`docker-compose.yml`、`/api/health` 已对齐
- 微服务目录仍在，但不是当前验证过的前端主线

canonical 部署形态是 Docker Compose + FastAPI 单体。Kubernetes、Kafka、GraphRAG 与 Multi-Agent **不是**上线前置条件；如需引入，属于独立的架构变更，不能由本手册的上线检查默认视为已具备。

仓库另有一套最小 Kubernetes manifest（`deploy/k8s/`，单个 workload = FastAPI 单体 `app.py`），见 [Kubernetes 部署契约](deployment-guide-k8s.md)。它的证据等级只到 `REPO_VERIFIED`（静态检查），真实集群部署为 `PENDING`，不改变本手册的 canonical 推荐。

## 2. 部署前必须确认

### 2.1 环境变量

```bash
cp .env.example .env
```

`common/config.py` 负责读取应用配置。按 `.env.example` 准备环境；运行单体应用使用 `python3 app.py`。离线知识库由 `run_offline.py` 子命令构建（`create-index` / `ingest` / `incremental-build` / `full-rebuild` / `seal-epoch`），操作细节见 [数据管理手册](data-admin-guide.md)。

生产环境至少明确设置：

- `DEPLOYMENT_MODE=production`
- `AUTH_DEV_MODE=false`
- `CORS_ORIGINS=https://your-frontend.example.com`
- `JWT_PRIVATE_KEY_PATH`
- `JWT_PUBLIC_KEY_PATH`
- `JWT_ALGORITHM=RS256`
- `REDIS_PASSWORD`（Compose fail-fast：未设置时 Redis/ app 启动失败）
- `ELASTICSEARCH_USERNAME` / `ELASTICSEARCH_PASSWORD`（Compose 启用 `xpack.security.enabled=true`，缺失时 fail-fast）
- `MINIO_ACCESS_KEY`
- `MINIO_SECRET_KEY`
- `SERVICE_AUTH_TOKEN`
- `TRUSTED_PROXIES`（可选：仅当应用在可信反向代理之后、需要按真实客户端 IP 限流时设置）

说明：

- `common/config.py` 会优先读取 `.env` / 进程环境中的 `DEPLOYMENT_MODE` 和 `AUTH_DEV_MODE`。
- 如果生产环境仍保留 `AUTH_DEV_MODE=true`，任意客户端都可伪造 `X-User-*` 头部，不符合真实上线要求。
- Elasticsearch 安全边界：Compose 使用 `elastic` 用户 + `ELASTICSEARCH_PASSWORD`，健康检查也走认证请求；在线 `retrieval/bm25_retriever.py` 与离线 writer 会优先读取环境凭据，回退到 `config.json`。不要把无认证 ES 当作生产默认配置。
- `GET /api/stats` 与 `GET /api/metrics` 需要身份认证（`require_identity`）；`GET /api/health` 保持公开。Prometheus 抓取需配置 `Authorization: Bearer <token>`。
- 登录限流仅在 TCP 对端属于 `TRUSTED_PROXIES` 时才解析 `X-Forwarded-For`；未配置时使用对端地址，客户端无法通过伪造 XFF 绕过限流。
- `app.py` 以 `proxy_headers=False` 启动 uvicorn，因此代理信任完全由应用的 `TRUSTED_PROXIES` 决定。若绕过 `app.py` 直接用 `uvicorn app:app` 启动，必须同样加 `--no-proxy-headers`，否则 uvicorn 会先信任 `X-Forwarded-For`（默认 `forwarded_allow_ips=127.0.0.1`）而绕过该策略。
- 认证算法边界：浏览器登录由 `POST /api/auth/login` 签发 RS256 access/refresh token，`common/auth.parse_identity` 以 RS256 验签为主。RS256 验签不依赖 legacy `JWT_SECRET`；`JWT_SECRET`（HS256）仅为可选向后兼容回退，只有需要继续接受旧 HS256 token 时才设置。生产环境只需 `JWT_PRIVATE_KEY_PATH` / `JWT_PUBLIC_KEY_PATH` / `JWT_ALGORITHM`。

### 2.2 前端构建

```bash
cd frontend
npm ci
npm run build
cd ..
```

构建完成后，FastAPI 会从 `frontend/dist` 提供静态页面。

### 2.3 JWT 密钥

```bash
mkdir -p keys
python3 -c "from auth.jwt_auth import generate_keypair; generate_keypair('./keys')"
```

## 3. 启动方式

### 3.1 本地单体

```bash
python3 app.py
```

### 3.2 Compose

```bash
docker compose up -d
```

校验点：

- `http://localhost:8000/api/health`（公开）
- `http://localhost:8000/docs`（生产模式下禁用）
- `http://localhost:8000/api/auth/metadata`
- `http://localhost:8000/`

Compose 需要 `REDIS_PASSWORD`、`ELASTICSEARCH_PASSWORD`、`MINIO_*`、`SERVICE_AUTH_TOKEN`；缺失时 compose 会直接报错退出（fail-fast，这是有意设计，不是 bug）。

MinIO 镜像来源：canonical Compose 不再拉取公共 `minio/minio` 镜像，而是通过 `deploy/minio/Dockerfile` 从官方 upstream `github.com/minio/minio` 的**固定 commit**（`7aac2a2c5b7c882e68c1ce017d8256be2feea27f`，`master` 最后一个 commit）源码构建。原因是 MinIO 社区版已改为**仅源码分发**（upstream README「Source-Only Distribution」），官方 Docker Hub `minio/minio` 仓库已被删除，任何 `minio/minio:<tag>` 都无法拉取；旧的 `minio/minio:latest` 因此既不可复现也不可用。

- **license / provenance 边界**：MinIO 是 **external AGPLv3 依赖**，源码在构建时从官方 upstream 拉取；本仓库**不 vendor、不复制、不重新授权** MinIO 代码，也不改写其许可证。本节仅记录依赖来源，不构成任何许可证结论；具体的 AGPLv3 义务由使用者自行评估。
- 本地镜像名为 `beauty-rag-minio:7aac2a2c`（tag 即 pinned commit 前 8 位），构建产物带 OCI label 记录 upstream commit，便于审计。
- builder / runtime 基础镜像按 **digest** 固定（`golang:1.24-alpine3.22@sha256:3641e0d9…`、`alpine:3.22.6@sha256:5291449c…`）。仅固定版本 tag 不够：同一 tag 下 Go patch 与 Alpine manifest 仍可能被重建，从而在同一 Git commit 下产出不同二进制。升级基础镜像时请用 `docker buildx imagetools inspect <image>` 取新 digest 并在同一个 commit 里同时更新 tag 与 digest、写明原因。
- **apk 输入同样被固定**，否则仅固定基础镜像仍不够：`apk add` 未写版本时会从可变的 Alpine 仓库解析到最新包（Alpine 明确说明安装会选最新可用包）。构建通过 `deploy/minio/apk-pin-install.sh` 完成，做四件事：
  1. 按仓库（`main` / `community`）× 架构（`x86_64` / `aarch64`）记录并校验 `APKINDEX.tar.gz` 的 **sha256**，不一致直接构建失败；
  2. 从已校验的 index 中读出每个包所属的仓库，并按正确的 `$snapshot/$repo/$arch/` 布局下载对应 `.apk`；
  3. **覆盖 `/etc/apk/repositories`**，使镜像自带的网络仓库失效，只保留本地快照；
  4. 以 `--no-network` 安装。缺少或损坏的 payload 只会让构建失败，**不会**回退到网络。
- 注意 `apk --repository` **不能**用来做这件事：它是「追加」仓库而非替换 `/etc/apk/repositories`，且 apk 要求 index 位于 `$repo/$arch/APKINDEX.tar.gz` 并与 `.apk` 同目录。早期版本正是这样写的，结果是本地仓库不可用（`opening ...: No such file or directory`）、构建看似成功，实际依赖仍从网络解析——**校验通过却什么都没固定**。同理，闭包不做「只装直接依赖」：Dockerfile 里列出的是**完整传递闭包**（runtime 11 个包、builder 23 个包），全部写死版本，这样离线安装才可能成功；少列一个包就会构建失败，而不会悄悄联网。
- 代价是：v3.22 分支一旦发布安全更新，**构建会直接失败**而不是悄悄换掉产物。刷新时按 Dockerfile 注释里的 `curl` + `sha256sum` 取新 digest，在同一个 commit 里同时更新 4 个 `APK_INDEX_*`、包版本与基础镜像 digest。仓库固定为 `https://dl-cdn.alpinelinux.org/alpine/v3.22`，不使用 `latest`；未记录的架构会直接拒绝构建。
- MinIO 服务设置了 `pull_policy: build`。镜像 tag 只编码上游 MinIO commit，**不会**随 Dockerfile 变化而变化；如果不加这条策略，已持有该 tag 的主机在 `docker compose up -d` 时会直接复用缓存镜像，从而静默跳过 digest 固定、非 root 运行时以及后续任何一次有意的升级。
- 容器以非 root 身份（uid/gid `1000`，用户 `minio`）运行；旧的公共镜像以 root 运行。`/minio_data` 的 `mkdir` + `chown` 放在 `VOLUME` **之前**：legacy builder 会丢弃 `VOLUME` 之后的文件系统改动，顺序反了会让新建卷继承 root 属主、非 root 进程无法初始化。**新建**的 `minio-data` 卷会自动继承该属主，无需额外操作；若你的卷是旧 root 镜像创建的，需要一次性移交：
  `docker run --rm -v <project>_minio-data:/minio_data alpine:3.22.6 chown -R 1000:1000 /minio_data`
- 构建需要 Go module 下载（`proxy.golang.org`）与 git 可达性；首次构建耗时较长属正常。
- 端口、卷（`minio-data:/minio_data`）、`MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`、healthcheck 与 server command 均未改动。
- 证据等级：契约与 guard 测试为 `REPO_VERIFIED`。本仓库曾在本机单次执行 `docker compose build minio` 与一次临时容器 smoke（healthcheck 变 healthy、S3 SigV4 `ListBuckets` 返回 200、未认证请求 403、以 uid 1000 完成 CreateBucket/PUT/GET 读写），这**只是单次本地运行记录，不是 CI 保证**；apk 快照机制亦以本地镜像服务器实测：全部 payload 可用时安装成功并校验落盘版本，payload 全部 404 时构建失败且不回退网络，index digest 被篡改时按名报错。`/api/media` 预签名链路的端到端验证、生产环境与 HA 均未验证，仍为 `PENDING`。

在线会话（`SessionState`）与登录限流在配置了 Redis 时跨 worker 共享，Redis 不可用时降级为进程内内存（此时不跨 worker）。

L2 答案缓存按 role/dept 分区存储，物理 key 形如 `rag:l2:rm:<role_mask>:dm:<dept_mask>:<hash>`。旧的未分区 key 不再被读取、也没有回落或迁移路径，只按既有 TTL 过期。**部署后出现一次性缓存 miss 属预期**，不要为此编写 key 迁移脚本，也不要批量清理 `rag:l2:*`。

## 4. 离线知识库部署要求

离线管线代码位于 `offline/`，入口为 `run_offline.py`。部署时需要区分：

- **默认应用部署**：`python3 app.py` 或 `docker compose up -d`，提供在线问答；不包含 Airflow，
  也不自动运行离线构建。
- **离线 ingestion 依赖**：PDF/DOCX/XLSX 解析库已包含在 `requirements.txt`
  （PyMuPDF / python-docx / openpyxl / pandas）。
- **可选 OCR 运行时**：处理扫描件或图片时安装 `offline/requirements-ocr.txt`
  （PaddleOCR / PaddlePaddle）。默认不安装，CI 不依赖它。
- **可选 Airflow 部署**：`dags/knowledge_base_dags.py` 仅在 Airflow 已安装且离线模块可发现时
  注册 DAG。默认 `docker compose` 不启动 Airflow；DAG 代码存在不等于调度器在运行。
- **外部模型资产**：真实 BGE/CLIP 权重需由操作者按 `config.json` 准备；仓库不随附，也不在
  import 阶段下载。缺少资产时真实模型 smoke 状态为 `EXTERNAL_MODEL_ASSET_REQUIRED`。

离线构建、校验与封存命令见 [数据管理手册](data-admin-guide.md)。构建出的 epoch 需要操作者
手动切换 `config.json` 的 `knowledge_version_epoch` 才会对在线检索生效。

## 5. 当前已对齐的前后端能力

- 登录：`POST /api/auth/login`
- 刷新 Token：`POST /api/auth/refresh`
- 单轮查询：`POST /api/query`
- 多轮问答：`POST /api/chat`
- 会话历史：`GET /api/dialog_history`
- 系统统计：`GET /api/stats`（需要认证）
- Prometheus 指标：`GET /api/metrics`（需要认证）
- 健康检查：`GET /api/health`（公开）
- 证据文档打开：`GET /api/media/{doc_id}`，由前端先取预签名链接再打开
- 管理员用户管理：
  - `GET /api/auth/users`
  - `POST /api/auth/users`
  - `PUT /api/auth/users/{user_id}/roles`

## 6. 生产前额外检查

- 将 `AUTH_DEV_MODE` 设为 `false`
- 确认 `.env` 已被当前启动进程加载
- 配置 `CORS_ORIGINS`
- 配置 `ELASTICSEARCH_USERNAME` / `ELASTICSEARCH_PASSWORD`，确认 ES 已启用认证
- 如需反向代理限流，配置 `TRUSTED_PROXIES`（且代理确实追加 XFF）
- 为 Prometheus/监控配置 `GET /api/metrics` 的 Bearer token
- 构建前端并确认 `frontend/dist` 已生成
- 使用管理员账号验证用户创建、角色更新、证据文件访问
- 确认 `Dockerfile` 健康检查访问的是 `/api/health`
- 确认 Redis L2 缓存 key 带权限分区，且发布后一次性缓存 miss 已被登记为预期（不要加未分区 key 的回落或迁移）
- 确认 `docs/pre-launch-checklist.md` 中的「缓存边界与提示词边界验证」一组已逐项确认
- 审核 `docs/pre-launch-checklist.md` 中的 P0 阻塞项
