# Kubernetes 部署契约（最小形态）

## 0. 这份文档是什么，不是什么

`deploy/k8s/` 是本仓库的**第二套部署形态**，与 Docker Compose 并列，不是它的替代品。

| 声明 | 证据等级 |
|---|---|
| manifest 能被解析，selector / probe / secret / resources / 镜像 tag 满足下述契约 | `REPO_VERIFIED`（**仅静态检查**） |
| 任何集群 apply、admit、rollout 或承载过这份 manifest | **`PENDING`** |
| 承载真实流量、完成滚动更新、达到任何 SLO | **`PENDING`** |

**本仓库没有 Kubernetes 集群，也没有 `kubectl` 上下文。** 下面每一条命令都是给人看的执行指引，不是本仓库跑过的记录。任何「已在 Kubernetes 生产环境稳定运行」的说法都不成立。

静态检查指`tests/deploy/test_k8s_manifests.py`（24 项，全部离线、无网络、无集群）。它证明的是 YAML 结构与契约，不是运行时行为。

## 1. 范围：只有一个 workload

只包含 API 网关：Deployment + Service + ConfigMap + Secret 引用。

**不在范围内**：vLLM 推理、Qdrant、Elasticsearch、Redis、MinIO。网关假设它们作为集群内Service 存在，与 compose 拓扑对齐。这些依赖的实际部署方式取决于你的集群，**本仓库不声称验证过任何一种**。

**刻意不引入**：Helm、Istio、ArgoCD、KServe、Service Mesh、Operator、GPU Scheduler、Kafka、HPA。`replicas: 1` 的理由见§5。

## 2. 应用事实（实测，非推断）

以下三条决定了探针与配置形态，全部对运行中的应用验证过：

```text
GET /api/health   -> 200   （公开，无需认证）
GET /api/metrics  -> 401   （需认证）
```

**`/api/health` 在 Redis、Qdrant、Elasticsearch 全部不可达时仍返回 HTTP 200**，body 为：

```json
{"status":"degraded","version":"2.3.0","dependencies":{"redis":false,"qdrant":false,"elasticsearch":false}}
```

推论（见 §3）：把 `httpGet /api/health` 当 liveness 探针，**这个探针永远不会失败**。

## 3. 探针契约

| 探针 | 形态 | 端点 | 理由 |
|---|---|---|---|
| `startupProbe` | `httpGet` | `/api/health` | 给 ES/Qdrant 冷启动留时间（30 × 5s） |
| `livenessProbe` | `tcpSocket` | `:8000` | `/api/health` 恒返回 200，无liveness 信号；依赖抖动不应触发网关重启 |
| `readinessProbe` | `httpGet` | `/api/health` | **只是存活级别的闸门**，不反映依赖状态 |

**readinessProbe 的诚实边界**：依赖健康由 `status` 字段、`/api/metrics`（需认证）与 Prometheus 告警承担，**不由探针退出码承担**。当前 `readinessProbe` 在 Qdrant 宕机时仍会判定 Pod ready —— 这是 `/api/health` 的语义决定的，不是配置疏漏。

若你需要真正的依赖级就绪判定，正确做法是让应用在依赖不可用时返回非200，或用 `exec` 探针解析 `status` 字段。**两者都超出这份最小契约的范围**，此处仅记录为已知边界。

`/api/metrics` **不作为探针端点**：无凭据时返回 401，探针会持续失败。

`test_liveness_probe_does_not_depend_on_backing_services` 与 `test_liveness_probe_would_reject_an_http_get_on_health` 把上述理由钉在测试里：日后有人把 liveness 改回 `httpGet`，测试会带原因失败。

## 4. 部署步骤（需要你自己的集群）

### 4.1 前置

- 一个可用集群与 `kubectl` 上下文
- 已构建并推送的镜像。**本仓库不发布镜像**，镜像引用需替换为你自己的registry
- 依赖服务已在集群内可解析：`qdrant`、`redis`、`elasticsearch`，以及推理侧 `rewrite-service:8101`、`generation-service:8100`

镜像 tag 必须固定，且与 `config.json` → `system.version`（当前 `2.3.0`）一致 —— 这条由 `test_image_tag_matches_the_canonical_runtime_version` 强制。

### 4.2 Secret（先于其余步骤）

`deploy/k8s/secret.example.yaml` 是**模板**，只含 `REPLACE_ME` 占位符。真实值在集群外创建：

```bash
kubectl apply -f deploy/k8s/namespace.yaml

kubectl -n beauty-rag create secret generic rag-api-secrets \
  --from-literal=SERVICE_AUTH_TOKEN="$SERVICE_AUTH_TOKEN" \
  --from-literal=REDIS_PASSWORD="$REDIS_PASSWORD" \
  --from-literal=REDIS_CACHE_PASSWORD="$REDIS_CACHE_PASSWORD" \
  --from-literal=ELASTICSEARCH_PASSWORD="$ELASTICSEARCH_PASSWORD"
```

**不要提交填好值的副本。** `test_no_secret_carries_a_real_value` 会在任何人这么做时失败。

### 4.3 RS256 密钥

`config.json` 的 `auth.dev_mode=false`，因此 `/api/auth/login` 只在 RS256 路径下可用：`auth/jwt_auth.get_jwt_config()` 要求 `JWT_ALGORITHM` 已设且两个 key path 存在。

```bash
python3 -c "from auth.jwt_auth import generate_keypair; generate_keypair('./keys')"

kubectl -n beauty-rag create secret generic rag-jwt-keys \
  --from-file=jwt-private.pem=./keys/jwt-private.pem \
  --from-file=jwt-public.pem=./keys/jwt-public.pem
```

文件名必须与 ConfigMap 里的 `JWT_PRIVATE_KEY_PATH` / `JWT_PUBLIC_KEY_PATH` 一致（`defaultMode: 0400`）。

**未挂载密钥时，系统表现为 401，而不是放开鉴权** —— 这是有意的 fail-closed。

`JWT_SECRET`（HS256）保留在 Secret 模板中仅供一次性开发命名空间使用，它**不会**启用登录。

### 4.4 其余

```bash
kubectl apply -f deploy/k8s/configmap.yaml
# 将 api-deployment.yaml 中的 image 替换为你自己的镜像
kubectl apply -f deploy/k8s/api-deployment.yaml
kubectl -n beauty-rag get deploy,pods,svc
```

## 5. 为什么 `replicas: 1`

用户库是 SQLite：`auth/user_store.py` 用 `DATABASE_URL`，默认 `sqlite:///./data/users.db`。

本契约**没有**声明共享存储，也**没有**外部数据库后端。副本数 >1 时各Pod 持有互不一致的用户行：一个 Pod 创建的用户可能无法从另一个 Pod 登录，role/dept mask 变更会陈旧，Pod 替换后其上所有用户消失。

**提高副本数需要先落地共享用户存储。** 那是这份最小契约**不做**的真实工作，因此 `replicas: 1` 并非疏漏，而是当前诚实的选择。`test_multiple_replicas_require_a_shared_user_store` 会在未配置 `DATABASE_URL` 而副本数被提高时失败。

同理，`./data/users.db` 没有 volume：单副本下它随Pod 生灭。这是**已声明的限制**，不是生产就绪。

`maxUnavailable: 0` + `maxSurge: 1` 保证单副本滚动更新期间不丢流量。

## 6. 配置项的来源

ConfigMap 里每个键都对着一个 `os.environ.get` 调用点验证过，没有臆测项。几个不显然的：

| 键 | 为什么需要 |
|---|---|
| `REWRITE_SERVICE_URL` / `GENERATION_SERVICE_URL` | 不设则默认 `localhost`，**API pod 会回调自身**，生成与改写全部降级 |
| `ELASTICSEARCH_USERNAME` | `retrieval/bm25_retriever.py:49-50` 仅在 username **与** password 均非空时启用 `basic_auth`；config.json 的 username 是空串，缺失会导致匿名 `info()` 收 401，**BM25 对该进程永久禁用** |
| `JWT_ALGORITHM` + 两个 key path | 见 §4.3 |

`ELASTICSEARCH_HOST` 不在此处：`config.json` 的 `elasticsearch.host` 已指向集群内 Service 名，应用也没有对应的 env 覆盖。

刻意**不**设置的键：`CONFIG_PATH`（镜像已含 canonical config.json）、`CORS_ORIGINS`（通配默认值只是开发态 posture）、`TRUSTED_PROXIES`（仅在有 LB 设置 `X-Forwarded-For` 时有意义）。

## 7. 静态检查覆盖的契约

`python3 -m pytest tests/deploy/ -q` → 24 项。分组：

YAML 可解析、apiVersion/kind 完整、Namespace 一致 · Deployment selector ⊆ pod labels · Service selector命中 pod labels、targetPort 匹配 · 探针指向真实免认证端点、liveness 不依赖后端 · secret 仅被引用不硬编码、Secret 无真实值、ConfigMap 无 secret 形态键 · resources requests/limits 齐备且不倒挂 · 镜像 tag 固定且匹配 runtime version、RollingUpdate 策略 · RS256 配置与挂载、推理 URL 非 localhost、ES username 与密码配对、副本数与用户存储。

检查本身经mutation 验证：硬编码 secret、selector 漂移、探针改向、删除 resources、tag 改 `latest`、secret 键混入 ConfigMap、删除推理 URL、删除 ES username、提高副本数、移除密钥挂载、清空 `JWT_ALGORITHM` —— 每一项都会让对应测试失败。

## 8. 仍然 PENDING

| 项 | 缺什么 |
|---|---|
| manifest 被真实集群 apply | 集群 |
| 滚动更新承载真实流量 | 集群 + 依赖服务 |
| RS256 密钥实际挂载并登录成功 | 密钥材料 + 集群 |
| `./data/users.db` 跨副本持久化 | 共享用户存储（未实现） |
| 真实模型权重挂载 | 权重，见 [#8](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/8) / [#12](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/12) |
| HPA / 扩缩容 | 本契约不含 HPA；无负载证据支撑副本数目标（见 [#32](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/32)） |

**这份契约没有提升任何 Evidence Level，也没有关闭任何 validation issue。** Docker Compose 仍是本仓库的 canonical 部署形态。