# Kubernetes 部署契约（最小形态）

## 0. 这份文档是什么，不是什么

`deploy/k8s/` 是本仓库的**第二套部署形态**，与 Docker Compose 并列，不是它的替代品。

| 声明 | 证据等级 |
|---|---|
| manifest 能被解析，selector / probe / secret / resources / 镜像 tag 满足下述契约 | `REPO_VERIFIED`（**仅静态检查**） |
| 任何集群 apply、admit、rollout 或承载过这份 manifest | **`PENDING`** |
| 承载真实流量、完成滚动更新、达到任何 SLO | **`PENDING`** |

**本仓库没有 Kubernetes 集群，也没有 `kubectl` 上下文。** 下面每一条命令都是给人看的执行指引，不是本仓库跑过的记录。任何「已在 Kubernetes 生产环境稳定运行」的说法都不成立。

静态检查指`tests/deploy/test_k8s_manifests.py`（31 项，全部离线、无网络、无集群）。它证明的是 YAML 结构与契约，不是运行时行为。`/api/ready` 的判定逻辑与 HTTP 契约另由 `tests/test_readiness_contract.py` 与 `tests/test_readiness_endpoint.py` 覆盖，同样只到 `REPO_VERIFIED`。

## 1. 范围：只有一个 workload

只包含 API 网关：Deployment + Service + ConfigMap + Secret 引用。

**不在范围内**：vLLM 推理、Qdrant、Elasticsearch、Redis、MinIO。网关假设它们作为集群内Service 存在，与 compose 拓扑对齐。这些依赖的实际部署方式取决于你的集群，**本仓库不声称验证过任何一种**。

**刻意不引入**：Helm、Istio、ArgoCD、KServe、Service Mesh、Operator、GPU Scheduler、Kafka、HPA。`replicas: 1` 的理由见§5。

## 2. 应用事实（实测，非推断）

以下三条决定了探针与配置形态，全部对运行中的应用验证过：

```text
GET /api/health   -> 200   （公开，无需认证；诊断语义，依赖全挂仍返回 200）
GET /api/ready    -> 200 / 503（公开，无需认证；流量准入语义，依赖不足返回 503）
GET /api/metrics  -> 401   （需认证）
```

**`/api/health` 在 Redis、Qdrant、Elasticsearch 全部不可达时仍返回 HTTP 200**，body 为：

```json
{"status":"degraded","version":"2.3.0","dependencies":{"redis":false,"qdrant":false,"elasticsearch":false}}
```

推论（见 §3）：把 `httpGet /api/health` 当 liveness 探针，**这个探针永远不会失败**。

`GET /api/ready` 是为流量准入新增的独立端点，在依赖不满足最低服务能力时返回 **503**（判定表见 §3.2）。`/api/health` 的契约未因此改变。

## 3. 探针契约

| 探针 | 形态 | 端点 | 理由 |
|---|---|---|---|
| `startupProbe` | `httpGet` | `/api/health` | **API 进程的启动宽限期**（30 × 5s），**不等待依赖** |
| `livenessProbe` | `tcpSocket` | `:8000` | `/api/health` 恒返回 200，无liveness 信号；依赖抖动不应触发网关重启 |
| `readinessProbe` | `httpGet` | `/api/ready` | **唯一的依赖感知探针**：不满足最低服务能力时返回 503 |

### 3.1 `/api/health` 与 `/api/ready` 的语义区别

这两个端点回答的是**不同的问题**，不能互相替代：

| | `GET /api/health` | `GET /api/ready` |
|---|---|---|
| 用途 | **诊断** | **流量准入** |
| 依赖全挂时 | HTTP **200** + `status: degraded` | HTTP **503** + `status: not_ready` |
| 回答的问题 | 「我观察到了什么」 | 「现在把请求路由到这个 Pod，它能不能服务」 |
| 认证 | 不需要 | 不需要（探针无法携带凭据） |
| 泄露面 | 仅依赖名 + 布尔值 | 仅依赖名 + 布尔值 |

`/api/health` 的契约**未被修改**：依赖降级时它仍然返回 200 + `healthy|degraded`。本次改动是**新增**一个准入端点，而不是把 health 改造成 readiness。

### 3.2 Readiness 判定表

判定不是 `all(dependencies)`。下表每一行都对应代码里真实存在的降级路径：

| 依赖 | 单独故障时的真实降级行为 | 是否阻断 readiness |
|---|---|---|
| Redis | `RedisCache` 保留 L1 进程内缓存并停用 L2；登录限流回退到进程内内存计数器 | **否** → `degraded` |
| Elasticsearch（BM25） | Qdrant 稠密召回仍能返回候选 | **否** → `degraded` |
| Qdrant（稠密） | BM25 召回仍能返回候选 | **否** → `degraded` |
| Qdrant 可达但缺少配置的 text collection | `DenseRetriever.search` 吞掉异常并返回 `[]`，实际上无候选可召回 | **否** → 与 `Qdrant + ES` 同结论 |
| **Qdrant + Elasticsearch 同时不可用** | 召回为空 → Evidence Gate 拒绝 → 每个 query 都返回结构化拒答，**无服务能力** | **是** → `blockers: ["retrieval"]` |
| MinIO | 仅 `/api/media/{doc_id}` 返回 503；query/chat 不受影响 | **否** → `degraded` |
| `elasticsearch.enabled=false` | `BM25Retriever` 不发起检索，`fallback_search` 直接返回 `[]`；存活但不启用不算检索通路 | **否** → 同 `Qdrant + ES` 结论 |
| gen_4b | `simple` 与 `rewrite` 两个 tier 在任何部署下都路由到它 | **是** |
| gen_14b | `complex` tier 在**生产模式**下路由到它，且**没有运行期回退到 4B** | **是**（仅生产模式） |

**检索是 OR 语义**：Qdrant 与 Elasticsearch 任一可用即可服务；两者同时不可用才失去服务能力。

**生成端点按部署自身的路由配置判定**，而不是硬编码生产拓扑：非生产模式下 `resolve_model_endpoint` 会把 `complex` tier 改写为 `simple`，因此只有 `gen_4b` 是必需的；生产模式下 `gen_14b` 也是必需的 —— `LLMClient.generate` 在失败时是 `raise` 而非回退，所以一个连不上 `gen_14b` 的 Pod 确实无法服务它会被路由过去的请求，称之为 ready 是不诚实的。

**为什么不能因为「怕探测模型」就假装生成不关键**：一个「检索健康但模型完全可用性为零」的 Pod 会通过 `all(qdrant, elasticsearch)` 这类判定，然后接收请求并全部失败。探针使用 OpenAI 兼容的 `GET /v1/models`，不发送任何真实生成请求。

**探测自身必须有界**：每个探针都有自己的网络超时（Elasticsearch 用 `request_timeout`，vLLM 与 Qdrant 用客户端 timeout），评估器另外对整体等待设了上限并以非阻塞方式关闭线程池。原因是 `readinessProbe` 只有 5s（`timeoutSeconds: 5`）——**一个卡住的探针会让端点永不返回，Kubernetes 于是把一个本可服务的 Pod 摘出流量**，即使卡住的是 Redis 或 MinIO 这种本该只是 `degraded` 的依赖。超过预算的探针一律记为不可用。

**「可达」不等于「可用」**：Qdrant 探针会校验配置的 text collection（`embedding.text.collection`）确实存在，而不是只看 `get_collections()` 成功；`DenseRetriever.search` 把异常吞成 `[]`，因此一个空 collection 实际上召不回任何东西。同理，`elasticsearch.enabled=false` 时 BM25 根本不发起检索，存活但不启用不能算检索通路。

响应示例：

```json
// HTTP 200
{"status":"ready","dependencies":{"redis":false,"qdrant":true,"elasticsearch":true,"minio":false,"gen_4b":true,"gen_14b":true},"degraded":["redis","minio"],"blockers":[]}

// HTTP 503
{"status":"not_ready","dependencies":{"redis":true,"qdrant":false,"elasticsearch":false,"minio":true,"gen_4b":true,"gen_14b":true},"degraded":[],"blockers":["retrieval"]}
```

**startupProbe 为什么仍留在 `/api/health`**：它回答的是「进程是否启动完成」。若改成 `/api/ready`，启动就会与依赖可用性耦合 —— 发布期间一次依赖抖动可能让探针持续失败直到 `failureThreshold`，进而触发重启循环，正是 liveness 设计要避免的结果。

**startupProbe 的诚实边界**：由于 `/api/health` 在依赖不可用时同样返回 200，这个 `30 × 5s` 宽限**只覆盖 API 进程自身的启动**，**不会**把流量挡到 Elasticsearch / Qdrant 冷启动完成之后。它不是依赖就绪闸门。

`/api/metrics` **不作为探针端点**：无凭据时返回 401，探针会持续失败。

以下测试把上述理由钉在测试里：日后有人把 liveness 改回 `httpGet`，或把 readiness 改回 `/api/health`，测试会带原因失败。

| 测试 | 钉住的契约 |
|---|---|
| `test_liveness_probe_does_not_depend_on_backing_services` | liveness 保持 `tcpSocket`，依赖抖动不触发重启 |
| `test_liveness_probe_ignores_dependency_outage_while_readiness_does_not` | 依赖全挂时 `/api/ready` 返回 503，而 liveness 不受影响 |
| `test_liveness_probe_would_reject_an_http_get_on_health` | 若 `/api/health` 不再恒返回 200，tcpSocket 的理由必须重新评估 |
| `test_readiness_probe_targets_the_readiness_endpoint` | readiness 不得退回 `/api/health`（那样它永远无法失败） |
| `test_startup_probe_remains_process_liveness_only` | startup 不与依赖可用性耦合 |
| `test_probe_endpoints_are_registered_and_unauthenticated` | 探针指向的端点必须真实存在且不需要用户 JWT |

端点语义由 `tests/test_readiness_contract.py`（判定逻辑）与 `tests/test_readiness_endpoint.py`（HTTP 契约）覆盖：ready / not-ready、Qdrant 与 ES 的 OR 语义、Redis 与 MinIO 降级不阻断、生产与非生产下不同的生成端点要求、探测异常归一化为结构化响应而非 500、探测超时不得挂起端点（含可降级依赖）、Qdrant 必须校验配置的 collection、`elasticsearch.enabled=false` 不算检索通路、以及响应体不含任何 URL / 密码 / token。

## 4. 部署步骤（需要你自己的集群）

### 4.1 前置

- 一个可用集群与 `kubectl` 上下文
- 已构建并推送的镜像。**本仓库不发布镜像**，镜像引用需替换为你自己的registry
- 依赖服务已在集群内可解析：`qdrant`、`redis`、`elasticsearch`，以及推理侧 `vllm-4b:8101`、`vllm-gen-14b:8100`（对应 ConfigMap 的 `VLLM_4B_URL` / `VLLM_GEN_14B_URL`，端口取自 `config.json` 的 `gpu1.models.vllm_4b.port` / `gpu0.models.gen_14b.port`）

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
# auth.jwt_auth.generate_keypair writes keys/private.pem and keys/public.pem
python3 -c "from auth.jwt_auth import generate_keypair; generate_keypair('./keys')"

kubectl -n beauty-rag create secret generic rag-jwt-keys \
  --from-file=jwt-private.pem=./keys/private.pem \
  --from-file=jwt-public.pem=./keys/public.pem
```

`generate_keypair` 写出的是 `private.pem` / `public.pem`，上面的 `--from-file` 用目标文件名重命名，使挂载后的路径与 ConfigMap 的 `JWT_PRIVATE_KEY_PATH` / `JWT_PUBLIC_KEY_PATH` 一致。

**权限**：Secret volume 的文件由 root 拥有，而镜像以 `appuser`（uid/gid 1000，`runAsNonRoot`）运行。manifest 因此用 `defaultMode: 0440` + pod 级 `fsGroup: 1000`，让应用组可读；`0400` 会让非 root 进程读不到密钥，登录仍然失败。Dockerfile 已显式固定 `--uid 1000 --gid 1000`，`test_numeric_ids_match_the_pinned_image_user` 保证两处不漂移。

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
| `VLLM_4B_URL` / `VLLM_GEN_14B_URL` | 镜像入口是 `python app.py`，即 **app.py 单体**，它经 `router/stateless_router.py` 读这两个变量，不设则默认 `localhost` → **pod 回调自身**。`REWRITE_SERVICE_URL` / `GENERATION_SERVICE_URL` 只被独立的 `api-gateway/` 应用读取，本 Deployment 不启动它，设了也无效 |
| `ELASTICSEARCH_USERNAME` | `retrieval/bm25_retriever.py:49-50` 仅在 username **与** password 均非空时启用 `basic_auth`；config.json 的 username 是空串，缺失会导致匿名 `info()` 收 401，**BM25 对该进程永久禁用** |
| `JWT_ALGORITHM` + 两个 key path | 见 §4.3 |

`ELASTICSEARCH_HOST` 不在此处：`config.json` 的 `elasticsearch.host` 已指向集群内 Service 名，应用也没有对应的 env 覆盖。

刻意**不**设置的键：`CONFIG_PATH`（镜像已含 canonical config.json）、`CORS_ORIGINS`（通配默认值只是开发态 posture）、`TRUSTED_PROXIES`（仅在有 LB 设置 `X-Forwarded-For` 时有意义）。

## 7. 静态检查覆盖的契约

`python3 -m pytest tests/deploy/ -q` → 31 项静态检查。分组：

YAML 可解析、apiVersion/kind 完整、Namespace 一致 · Deployment selector ⊆ pod labels · Service selector 命中 pod labels、targetPort 匹配 · 探针指向真实免认证端点、liveness 不依赖后端 · secret 仅被引用不硬编码、Secret 无真实值、ConfigMap 无 secret 形态键 · resources requests/limits 齐备且不倒挂 · 镜像 tag 固定且匹配 runtime version、RollingUpdate 策略 · RS256 配置与挂载、**挂载权限对非 root 可读**、**uid 与 Dockerfile 固定值一致**、**推理 URL 用单体实际读取的变量**、ES username 与密码配对、副本数与用户存储。

检查本身经 mutation 验证：硬编码 secret、selector 漂移、探针改向、删除 resources、tag 改 `latest`、secret 键混入 ConfigMap、删除推理 URL、改用 `REWRITE_/GENERATION_SERVICE_URL`、删除 ES username、提高副本数、移除密钥挂载、清空 `JWT_ALGORITHM`、`defaultMode` 退回 `0400`、Dockerfile 去掉 uid 固定 —— 每一项都会让对应测试失败。

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