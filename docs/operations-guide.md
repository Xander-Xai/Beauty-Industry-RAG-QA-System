# 化妆品行业 RAG 问答系统运维手册

## 0. 版本语义

本手册描述的运行配置以 `config.json` → `system.version` 为准（当前为 `2.3.0`）。仓库中部分文档与文件名使用的 “v2.5” 是 **历史 working milestone / development-phase 标签**，不是正式发布版本，也不改变本手册的运行时契约；详见 [版本策略](repository-truth-audit.md#version-policy)。

## 1. 先明确当前运维范围

当前仓库已经能确认的在线运维对象是单体 FastAPI 应用：

- 入口：`app.py`
- 健康检查：`GET /api/health`（公开）
- 统计：`GET /api/stats`（需要认证）
- 指标：`GET /api/metrics`（需要认证，Prometheus 抓取需 Bearer token）

微服务目录仍在仓库中，但不应默认按“已完成整套线上运维验证”处理。

会话状态与登录限流在配置 Redis 时跨 worker 共享；Redis 不可用时降级为进程内内存（此时多 worker 不共享，属于可接受的降级而非故障）。

## 1.1 告警 → 诊断 → 处置

完整流程（8 个故障场景，每项含 Alert / 用户影响 / 诊断 / 立即处置 / 降级模式 / 回滚 / 需采集证据 / 恢复验证）见 [SLO 与故障 Runbook](slo-runbook.md)。本节只给出入口对照，避免值班同学在两处文档间反复跳转。

| 告警 | 第一动作 | Runbook 章节 |
|---|---|---|
| `RagAppDown` | 先确认 scrape token 是否有效：`curl -i http://localhost:8000/api/metrics \| head -1`。401 说明是 token 问题，不是服务挂了 | [HighErrorRate](slo-runbook.md#higherrorrate) |
| `RagHighErrorRate` | 看 `rag_http_responses_5xx` 占比与 `GET /api/health` 三个依赖位，定位到具体依赖 | [HighErrorRate](slo-runbook.md#higherrorrate) |
| `RagHighLatencyP95` | 先看缓存命中率（Redis 故障会同时打掉缓存），再看 `rag_kv_pressure`。缓存命中率**没有**对应的 `rag_*` series，算法见下方「缓存命中率怎么查」 | [HighLatency](slo-runbook.md#highlatency) |
| `RagRedisDegraded` | 会话已降级到进程内、限流已退化为单进程计数。确认 Redis 存活即可，不要重启业务进程 | [RedisUnavailable](slo-runbook.md#redisunavailable) |
| `RagHighLoginRateLimit` | 多数是撞库或客户端重试风暴；若 Redis 同时不可用，限流在多 worker 下会被削弱 | [RedisUnavailable](slo-runbook.md#redisunavailable) |
| `RagRequestSaturation` | 在途请求偏高，优先降并发而不是加超时 | [HighLatency](slo-runbook.md#highlatency) |

Qdrant 与 Elasticsearch 故障**没有**告警规则：`/api/health` 以 JSON 返回依赖状态，而本仓库没有为它们输出任何 Prometheus 指标。这两个场景靠 `GET /api/health` 巡检 + Runbook 处置流程覆盖，不靠告警。

所有告警阈值均为 `DESIGN_TARGET`，不是生产历史调优结果；本仓库没有任何告警在生产触发过的证据。

### 缓存命中率怎么查（不要找 `rag_cache_hit_rate`）

**不存在**名为 `rag_cache_hit_rate` 的 Prometheus series。exporter 只暴露原始计数器：

```text
rag_cache_hit_L1     counter   L1 命中次数
rag_cache_hit_L2     counter   L2 命中次数
rag_cache_total      counter   参与缓存判定的请求总数
```

比率由两个地方计算，**都不是** exporter 直接吐出的 series：

1. `/api/stats` 的计算字段 `cache_hit_rate.{L1,L2,L2_SESSION}`（需要认证）：

   ```bash
   curl -s http://localhost:8000/api/stats -H "Authorization: Bearer $TOKEN" \
     | python3 -c 'import json,sys; print(json.load(sys.stdin)["cache_hit_rate"])'
   ```

2. 基于真实 emitted counter 的 PromQL ratio：

   ```promql
   rate(rag_cache_hit_L1[5m]) / clamp_min(rate(rag_cache_total[5m]), 0.000001)
   rate(rag_cache_hit_L2[5m]) / clamp_min(rate(rag_cache_total[5m]), 0.000001)
   ```

`clamp_min` 不是可选的：分母为 0 时该表达式返回 `NaN`，而不是 0。

### 仓库里唯一正式的告警契约

`monitoring/prometheus/alerts.yml`（6 条规则，由外部 Prometheus 加载）。
本仓库不存在第二套告警规则：`config.json` 里的 `alerting.rules` 配置块已移除，
`monitoring/otel_tracer.py` 里的进程内 `AlertingManager` 是**未接入 canonical 请求路径的遗留类**，
不要把它的规则当成 Prometheus 规则。判定依据见
[Repository Truth Audit](repository-truth-audit.md#two-alerting-mechanisms-and-which-one-is-canonical)。

## 1.3 vLLM 生成路径的有界韧性契约

`route_chat`（`router/stateless_router.py`）是 canonical vLLM 生成路径。它的重试决策全部来自
`router/vllm_resilience.py`：失败分类、次数上限、总 deadline、退避节奏都在那里，路由器只负责
传输与解析。契约代码与确定性测试状态为 `REPO_VERIFIED`；**没有任何真实 vLLM 服务、GPU 或压测
跑过这条路径**，运行时结果仍是外部验证项，本仓库不宣称已验证。

### 失败分类（taxonomy）

每一次失败的 attempt 恰好归入一类，重试与否是该类的属性，而不是每个调用点各自判断：

| failure class | 触发条件 | 会重试 | 观测 series |
|---|---|---|---|
| `timeout` | attempt 超时（含 connect / read / write / pool timeout） | 是 | `rag_vllm_generation_attempt_failure_class_timeout` |
| `connection` | 连接被拒 / 重置 / 对端未响应即断开 | 是 | `rag_vllm_generation_attempt_failure_class_connection` |
| `http_transient` | HTTP 408 / 429 / 502 / 503 / 504 | 是 | `rag_vllm_generation_attempt_failure_class_http_transient` |
| `http_permanent` | 其他所有非 2xx，**包括全部 ordinary 4xx**（400/401/403/404/422…） | 否 | `rag_vllm_generation_attempt_failure_class_http_permanent` |
| `malformed_response` | 2xx 但响应体不是可用 completion | 否 | `rag_vllm_generation_attempt_failure_class_malformed_response` |
| `configuration` | endpoint key 在本部署未配置 | 否 | 无 attempt（未发出 HTTP 请求） |
| `budget_exhausted` | 本进程的总 deadline 或次数上限用尽 | 否（已终止） | `rag_vllm_generation_outcome_budget_exhausted` |
| `unknown` | 本层不认识的异常 | 否 | `rag_vllm_generation_attempt_failure_class_unknown` |

三点值得单独记住：

- **500 不在 transient 列表里，这是决定不是遗漏。** 理由不是「vLLM 的 500 通常是确定性拒绝」这类
  本仓库没有验证过的因果判断：HTTP 500 的语义本身是异质的（同一个状态既可能是单个请求被拒，也可能
  是服务级故障），而本仓库没有针对 vLLM 端点的实测证据能证明重放 500 是安全的。所以 transient 是
  一份 **allow-list**——只有 408 / 429 / 502 / 503 / 504 会重试，500 与其它未被证明的状态一律
  fail closed，不重试。这与「无法识别的失败不重试」是同一条规则：重试要靠证据挣到，不靠推断。
  只有在拿到真实运行时证据之后才允许扩大这个列表。
- **无法识别的失败一律不重试。** 重试是失败必须"挣到"的，不是默认行为。
- **`budget_exhausted` 与 `http_transient` 是不同结论。** 前者是本进程预算用尽，后者是对端返回了
  可重试状态。排障时不要把二者混为一谈：`http_transient` 看端点，`budget_exhausted` 看
  `rag_vllm_generation_duration_seconds` 与 deadline 配置。

### 重试策略（有界 / 有 deadline）

```text
VLLM_MAX_ATTEMPTS=2                  # 总 attempt 数（含首次）：默认 2，代码硬上限 3
VLLM_TIMEOUT_SECONDS=10.0            # 单次 attempt 超时
VLLM_GENERATION_DEADLINE_SECONDS=15.0 # 整个请求的总预算（含所有退避 sleep）
VLLM_RETRY_BASE_DELAY_SECONDS=0.2    # 确定性指数退避起点
VLLM_RETRY_MAX_DELAY_SECONDS=2.0     # 退避上限
```

- **次数上限是结构性的，不变式分两层。** 默认 `VLLM_MAX_ATTEMPTS=2`，即**最多 2 个总 attempt**
  （首次 + 1 次 retry）；代码里的硬上限是 `HARD_MAX_ATTEMPTS=3`，即**任何配置下最多 3 个总 attempt**
  （首次 + 最多 2 次 retry）。`VLLM_MAX_ATTEMPTS` 会被 clamp 到 `[1, 3]`，即使误配成 `500` 也只生效 3。
  「最多 2 个 attempt」只是默认取值下的行为，**不是**契约的不变式；把它当成不变式会低估最坏情况的
  负载。这条上限不是约定，是代码里的 clamp，所以配置无法把它变成 retry storm。
- **deadline 约束的是工作量，不只是计数。** 每次 attempt 的 timeout 会被进一步 clamp 到"剩余预算"，
  因此调大 `VLLM_TIMEOUT_SECONDS` 不会让请求突破 deadline。退避 sleep 同样必须在预算内；装不下就终止
  而不是压缩退避。
- **没有随机 jitter。** 退避表是契约的一部分，需要可确定性测试。防 storm 靠上面三个确定性边界
  （次数上限、退避上限、deadline），不靠随机性。
- **`Retry-After` 会被尊重。** 当它比本地退避短时抬高退避；比 `VLLM_RETRY_MAX_DELAY_SECONDS` 更长时
  **不重试**（按原 transient 失败上抛，而不是记成预算耗尽）。提前于服务端要求重试正是 storm 的成因。

### 不会悄悄换模型

endpoint 由调用方（`models/llm_client.py::_resolve_endpoint`）解析一次，之后整次调用的所有 attempt
复用同一个 endpoint key。因此：

- 重试**不可能**把 14B 请求交给 4B 回答；14B 持续失败就是失败并上抛。
- 非 production 模式下 14B → 4B 的降级是既有的部署行为，不由本次契约引入，也不在重试路径上。
- 判断依据是 attempt 实际命中的 base URL 一致，而不是读代码推断。

排障时不要为了"让请求过去"而改 endpoint 映射或临时降级：那会改变回答的模型语义。

### 错误信息不会泄露凭据

`VLLMGenerationError` 的消息是固定模板，只填入 failure class、endpoint **config key**、HTTP status
和 attempt 次数：

```text
vLLM generation failed: class=http_permanent endpoint=gen_14b status=403 attempts=1
```

endpoint URL、credential、token、响应体都不会进入消息。endpoint key 本身若不像合法标识符
（例如调用方传入了一个 URL），会被替换为 `redacted`。原始 transport 异常不进异常链
（`raise ... from None`），所以任何遍历 `__cause__` 的格式化逻辑都无法把带凭据的 URL 变成 API 响应；
原始异常只在 DEBUG 日志里，供本地排障使用。

### 相关指标（全部来自既有 collector）

`monitoring/otel_tracer.py::MetricsCollector` 就是 `GET /api/metrics` 已经导出的那个 collector，
本次没有新增 registry、没有第二个 exporter、没有新的暴露端点。

| series | 含义 |
|---|---|
| `rag_vllm_generation_attempts` | 累计 HTTP attempt 数（含首次） |
| `rag_vllm_generation_attempt_success` / `..._attempt_failed` | attempt 结果，两者之和等于上一行 |
| `rag_vllm_generation_attempt_failure_class_*` | 按上文分类拆分的失败 attempt 数 |
| `rag_vllm_generation_requests` | 累计 `route_chat` 调用数 |
| `rag_vllm_generation_outcome_success` / `..._failed` / `..._budget_exhausted` | 请求终态，三者之和等于上一行 |
| `rag_vllm_generation_retries` | 实际发生的额外 attempt 数 |
| `rag_vllm_generation_duration_seconds` / `..._count` | 请求耗时分布（summary 形式，含 p50/p95/p99） |

查这两个比率（PromQL，不是 exporter 直接吐出的 series）：

```promql
# 重试比例：额外 attempt 占全部 attempt 的比例，持续偏高说明端点长期在抖。
# 上限由代码结构而不是配置决定：默认 VLLM_MAX_ATTEMPTS=2 时单请求最多 1 次额外
# attempt（比值 ≤ 0.5）；即使配到硬上限 3，也最多 2 次额外 attempt（比值 ≤ 0.67）。
# 因此不存在「任何配置下都不会更糟」这种保证，只有「配置无法突破硬上限 3」。
rate(rag_vllm_generation_retries[5m]) / clamp_min(rate(rag_vllm_generation_attempts[5m]), 0.000001)
# 预算耗尽占比
rate(rag_vllm_generation_outcome_budget_exhausted[5m]) / clamp_min(rate(rag_vllm_generation_requests[5m]), 0.000001)
```

`clamp_min` 不是可选的：分母为 0 时该表达式返回 `NaN`，而不是 0。

指标本身**没有**对应的告警规则。`monitoring/prometheus/alerts.yml` 仍是 6 条规则，新增指标尚未接入
告警契约；本仓库也没有任何告警在生产触发过的证据。

### 这条契约不覆盖的范围

- `route_completion`（`rewrite/query_rewriter.py` 使用的 rewrite 路径）**没有**纳入：仍是单次 attempt、
  不分类、不重试，异常类型也仍是原始 httpx 异常。把它一并改造会改变 rewrite 的重试语义，属于另一次改动。
- 不提供熔断器、跨实例状态或排队。它是单进程内的有界重试，不是分布式韧性框架。
- 不改变模型路由语义，也不引入任何新的模型或依赖。

## 1.2 审计与追踪查询

- 审计事件查询：`tail -n 200 logs/audit/$(date +%F).jsonl`
- 按 request_id 串联网关日志、访问日志与审计：`grep "$REQUEST_ID" logs/*.log logs/audit/*.jsonl`
- 追踪状态确认：默认**不启用**导出（`OTEL_EXPORT_ENABLED=false`，exporter 是可选依赖），后端查不到 span。exporter 实现本身已存在且有测试覆盖；启用方式见 [slo-runbook.md#tracelookup](slo-runbook.md#tracelookup)
- 导出是否生效：`rag_otel_exporter_enabled`（0=未启用或不可用，1=已启用）

## 2. 当前值得盯的指标

### 应用可用性

- `/api/health`（无需认证）
- `/api/stats`（配置监控账号的 Bearer token）
- `/api/metrics`（配置监控账号的 Bearer token）
- `/api/auth/metadata`（公开）

### 认证与权限

- 登录成功率
- Token 刷新成功率
- `/api/auth/users` 管理接口返回码
- `/api/media/{doc_id}` 的 401 / 403 / 404 比例

### 依赖健康

- Redis 可连通性
- Qdrant 可连通性
- Elasticsearch 可连通性
- MinIO 可用性

## 3. 当前已知运维风险

- **知识库数据准备**：通过 `run_offline.py` 构建离线快照（`full-rebuild` / `incremental-build`），
  `seal-epoch` 校验并封存，再手动切换 `knowledge_version_epoch` 激活。真实 BGE/CLIP 模型与可选
  PaddleOCR 运行时须由操作者准备；CI 使用确定性测试 embedder，不下载模型。操作细节见
  [数据管理手册](data-admin-guide.md)。
- **单体与微服务并存**：排障时必须先确认当前请求到底走的是 `app.py` 还是单独网关/微服务。
- **认证算法边界**：浏览器登录签发 RS256 access/refresh token，`common/auth` 以 RS256 验签为主；
  `JWT_SECRET`（HS256）仅为可选向后兼容回退。生产环境应配置 `JWT_PRIVATE_KEY_PATH` /
  `JWT_PUBLIC_KEY_PATH` / `JWT_ALGORITHM`，并仅在需要旧 HS256 兼容时设置 `JWT_SECRET`。
- **开发身份开关**：如果生产环境误保留 `AUTH_DEV_MODE=true`，会形成身份伪造风险。
- **运行配置来源**：服务现在会自动读取项目根目录 `.env`；排障时要同时检查 `.env` 与进程环境。
- **Elasticsearch 认证**：Compose 默认启用 `xpack.security.enabled=true`，需要 `ELASTICSEARCH_USERNAME` / `ELASTICSEARCH_PASSWORD`。无认证或凭据错误的 ES 请求会失败，不要回退为关闭 security。
- **登录限流身份**：仅当 TCP 对端属于 `TRUSTED_PROXIES` 时才信任 `X-Forwarded-For`。反向代理未在允许列表内时，所有请求按代理 IP 计限流（可能误伤）；未配置代理却伪造 XFF 无法绕过限流。
- **监控端点认证**：`/api/stats`、`/api/metrics` 需要认证，裸 `curl` 会 401；监控系统需配置 token。
- **L2 缓存权限分区**：L2 物理 key 里带 role/dept 分区，不同角色/部门的用户不会复用同一条物理缓存条目。分区由缓存对象自身强制，不依赖调用方是否记得用权限感知的 key 构造。
- **不回落 legacy 缓存 key**：旧的未分区 key（`rag:l2:<hash>` 形式）**不会被读取**，也没有迁移或回落路径——旧条目无法证明写入者的权限，读它等于把刚关掉的跨权限读取风险重新打开。这些条目按既有 TTL 自然过期。代价是发布后一次性的缓存 miss，这是安全的；跨权限命中才不安全。
- **权限掩码 fail closed**：掩码在任何缓存读写之前校验，非法 identity 让请求失败，而不是继续用缓存或降级成匿名（掩码 0）。JWT 权限 claim 的严格校验是同方向的另一道独立边界：签名有效只证明 token 来自密钥持有者，不证明里面的 claim 合法。排障时不要为了「让请求过去」而放宽校验或给掩码加默认值。
- **提示词框架边界**：检索证据与用户输入中的保留定界标记会被转义成数据，因此文档内容不能改写应用自己的提示词框架。这属于纵深防御，见下方提示。

## 3.1 提示词层控制的边界（不要过度承诺）

检索安全策略与边界标记转义是 **defense-in-depth**，不是 prompt injection 的绝对防护：

- prompt 层的安全说明本身是文字指令，模型可以忽略它。边界转义保证的是「文档无法伪造 framing token 把自己移出不可信区域」，不是「模型一定不执行文档里的指令」。
- 更强的边界需要检索侧检测、文档隔离与输出侧校验，这些**不在本层**，本仓库也没有实现。
- 同理，缓存分区不等于 RBAC 已完备：它不让数据泄漏成为不可能，也不让缓存成为零信任。

因此：排障时若怀疑提示词注入，**不要**把「策略已注入 system prompt」当作已闭环的证据；也不要因为这些控制存在就下调其他输入侧 / 输出侧检查。对外答复不要表述为「prompt injection 已解决」。

代码与测试证据等级见 [Repository Truth Audit](repository-truth-audit.md)。

## 4. 生产巡检建议

### 每次部署后

1. 访问 `GET /api/health`
2. 访问 `GET /api/auth/metadata`
3. 使用管理员账号登录
4. 验证管理员创建用户、更新角色/部门
5. 用不同角色验证证据文档访问权限
6. 打开首页，确认构建后的前端已被静态挂载
7. 打开 `Session` / `Stats` 面板，确认它们分别命中 `/api/dialog_history` 和 `/api/stats`
8. 确认 L2 缓存 key 带权限分区（形如 `rag:l2:rm:<role_mask>:dm:<dept_mask>:<hash>`），且 Redis 中没有新的未分区写入
9. 把发布后 L2 命中率的一次性下跌登记为预期（旧的未分区条目不再被读取，按 TTL 过期），确认它不伴随错误答案；命中率查法见本手册 1.1 节的「缓存命中率怎么查」
10. 抽查一次「claim 非法 → 401」与「合法非管理员 → 403」，确认两种结果可区分，且 claim 非法没有被记成授权拒绝

### 每日

1. 检查 Redis / Qdrant / Elasticsearch / MinIO 容器状态
2. 抽样检查 `/api/stats`（带认证 token）
3. 检查错误日志中的 401 / 403 / 429 / 500
4. 检查 Redis 不可用时是否频繁出现降级日志（会话/限流内存回退）

### 离线知识库

1. 确认 `config.json` 的 `knowledge_version_epoch` 与预期 active epoch 一致
2. 检查 Qdrant 文本/图像 collection 与 Elasticsearch index mapping 是否存在且维度/字段正确
3. 运行或确认最近一次 `seal-epoch` 的快照校验通过（Qdrant text/image + Elasticsearch + RBAC）
4. 检查状态数据库 `knowledge_base.state_db_path` 的最近处理时间与失败源
5. 检查是否仍有未封存的 staging epoch；失败构建不应被自动封存
6. 确认已保留上一 sealed epoch 以便回滚
7. 审核反馈队列（`offline.feedback.store_path`）中 pending 记录

## 5. 常见故障定位

### 首页能访问但问答失败

- 检查 `/api/chat`
- 检查页面当前是否切到了 `Single Query` 模式
- 检查 JWT 或开发身份配置
- 检查 Redis / Qdrant / Elasticsearch 健康状态

### 证据文档打不开

- 检查 `/api/media/{doc_id}` 是否返回 401 / 403 / 404 / 503
- 检查文档元数据中的 `status`
- 检查 MinIO 是否可用

### 管理员面板报权限错误

- 检查登录账号是否真的是 `admin` 角色
- 检查 JWT 是否过期
- 检查生产环境是否混入旧的前端缓存

### 发布后 L2 缓存命中率掉到零

- 先确认这是**预期的一次性 miss**，不是缓存故障：旧的未分区条目不再被读取，也没有回落路径，它们按既有 TTL 自然过期
- 确认命中率确实在回升（新条目按新格式写入），而不是持续为 0
- 确认 Redis 中新写入的 key 都带 `rm:` / `dm:` 分区段
- **不要**为了提高命中率而给未分区 key 加回落、批量改名或手工迁移：那会重新打开跨权限读取
- 如果怀疑是 Redis 本身的问题，再按 [RedisUnavailable](slo-runbook.md#redisunavailable) 排查

### 请求因权限掩码校验失败而报错

- 这是 fail closed 的**预期行为**：非法掩码在任何缓存读写之前就让请求失败，不要通过放宽校验、加默认值或静默转换来「修复」
- 定位掩码来源：登录签发的 JWT claim、上游代理传入的身份头，或调用方传入的缓存参数
- 重点确认非法值没有被降级成匿名 / 公开（掩码 0）继续服务——那才是真正的越权风险
- 确认失败被记为认证失败（401）而不是授权拒绝（403），否则审计事件会把认证问题误报成权限问题
- 合法非管理员被拒是 403，属于正常授权结果，不要与 401 混为一谈

### 怀疑提示词注入未生效

- 先确认注入内容是否只出现在检索证据里，而没有落在应用自己的定界区块之外
- 确认三条 system prompt 路径都带了检索安全策略，没有某一支绕过
- **不要**把「策略已注入 system prompt」当作闭环证据：这是纵深防御，prompt 层文字指令可被模型忽略，更强边界需要检索侧检测、文档隔离与输出侧校验

### 问答报「系统服务暂时不可用」/ 生成路径报错

先按失败分类定位，不要先调超时：

1. 看 `rag_vllm_generation_attempt_failure_class_*` 哪一类在涨
2. `http_permanent`（尤其 401/403）→ vLLM 服务本身在拒绝请求，属于配置或鉴权问题，**重试不会好转**
3. `connection` / `timeout` → 端点不可达或过载。先用 `GET /api/health` 确认依赖，再看
   `rag_vllm_generation_duration_seconds` 是否顶到 deadline
4. `malformed_response` → 端点返回了 2xx 但 body 不是合法 completion。查该端点的服务日志；
   不要靠调大 `VLLM_TIMEOUT_SECONDS` 掩盖，它不会被重试
5. `rag_vllm_generation_outcome_budget_exhausted` 上涨 → 请求总预算不够。判断是端点变慢还是预算太小；
   **优先**确认端点健康，再考虑 `VLLM_GENERATION_DEADLINE_SECONDS`
6. **不要**通过改 endpoint 映射把 14B 悄悄指向 4B 来"解决"故障：那会改变回答的模型语义，
   且重试路径本身不会、也不允许这么做
7. `unknown` 上涨说明出现了本层尚未分类的失败形态。先取 DEBUG 日志里的原始异常再决定是否补分类，
   不要为了让指标好看而把它归进 transient

### 离线构建失败或 `seal-epoch` 被拒绝

- 模型资产缺失：确认 `embedding.text.model_path` / `embedding.image_clip.model_path` 存在
- OCR 报错：安装 `offline/requirements-ocr.txt`
- Qdrant `dimension does not match`：`create-index --recreate --yes`
- ES `field ... must be ...`：mapping 与 writer 不一致，重建 index
- `embedding version changed within epoch`：构建新 epoch 或 `full-rebuild`
- carry-forward `IncompatibleEmbeddingVersion`：改用 `full-rebuild`
- `knowledge epoch ... is sealed`：写入新的未封存 epoch
- 校验失败：查看 `seal-epoch` 报出的具体错误；不要用 `--skip-validation` 绕过
- `source is outside the configured data root`：移动 SOURCE 或提供 `--source-id`

## 6. 备份建议

- SQLite 用户库：每日备份
- Redis：保留 AOF / RDB
- Qdrant：目录快照
- MinIO：对象存储快照
- `config.json` / `.env`：版本化与密钥分离管理
