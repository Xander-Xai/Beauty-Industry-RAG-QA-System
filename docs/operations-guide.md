# 化妆品 RAG 系统运维手册

## 1. 监控指标

### 系统指标

| 指标 | 说明 | 告警阈值 |
|------|------|----------|
| QPS | 每秒查询数 | - |
| P95 延迟 | 95% 请求延迟 | > 4s 持续 5min |
| P99 延迟 | 99% 请求延迟 | > 6s 持续 5min |
| 活跃请求数 | 当前并发 | > 30 |
| 错误率 | HTTP 5xx 比例 | > 5% 持续 3min |

### KV Cache 指标

| 指标 | 说明 | 告警阈值 |
|------|------|----------|
| KV Pressure | KV 缓存使用率 | > 0.9 持续 30s |
| Prefix Cache 命中率 | 系统级缓存效率 | 突降 > 50% |
| Admission 拒绝数 | 被准入控制拒绝的请求 | 持续增长 |

### 检索指标

| 指标 | 说明 | 告警阈值 |
|------|------|----------|
| L1 命中率 | 内存缓存命中 | 突降 > 30% |
| L2 命中率 | Redis 缓存命中 | 突降 > 30% |
| Evidence Gate 分数 | 证据质量 | 均值 < 0.5 |
| NLI 矛盾比例 | 答案矛盾率 | > 10% |
| Rewrite 降级率 | 规则兜底比例 | > 10% |

### 资源指标

| 指标 | 说明 | 告警阈值 |
|------|------|----------|
| GPU0 显存 | 14B 模型显存 | > 90% |
| GPU1 显存 | 4B + Rerank 显存 | > 90% |
| Redis 内存 | 缓存内存 | > 80% |
| ES 索引大小 | 全文检索索引 | > 10GB |

## 2. 告警处理

### KV Pressure > 0.9

1. 检查是否有大批量并发请求
2. 检查是否有异常长序列请求
3. 若持续 > 0.95，系统自动降级 P1/P2 请求
4. 必要时手动重启 vLLM 实例释放 KV Cache

### P95 延迟 > 4s

1. 检查 vLLM 实例状态
2. 检查 Milvus/ES 查询延迟
3. 检查网络连通性
4. 检查 GPU 利用率是否打满

### Redis 降级 > 5 分钟

1. 检查 Redis 容器状态: `docker compose logs redis`
2. 检查 Redis 内存使用
3. 若无法恢复，系统自动切换到 L1 内存缓存模式
4. 重启 Redis: `docker compose restart redis`

### Milvus 不可用

1. 检查 Milvus 容器: `docker compose logs milvus`
2. 检查 etcd 状态（Milvus 依赖）
3. 系统自动切换到 ES Fallback 模式
4. 必要时重建 Milvus: `docker compose restart milvus-standalone`

## 3. 故障演练

### 演练清单

| 场景 | 命令 | 预期行为 |
|------|------|----------|
| Redis 宕机 | `docker compose stop redis` | L1 缓存接管，无 503 |
| Milvus 超时 | `docker compose stop milvus` | ES Fallback 路径 |
| GPU0 OOM | 模拟显存耗尽 | 14B → 4B 降级 |
| vLLM-Rewrite 不可用 | `docker compose stop vllm-rewrite` | 规则兜底路径 |
| ES 不可用 | `docker compose stop elasticsearch` | 纯向量检索 |
| KV 极端压力 | 并发 80+ 请求 | 分级降级策略 |

### 恢复验证

每次故障注入后，验证：
1. API 端点仍可响应（可能降级）
2. 日志中有对应的降级记录
3. 恢复服务后系统自动恢复
4. 无数据丢失

## 4. 扩容指南

### 水平扩容

当前架构为单实例设计。扩容建议：

1. **API 层**: 增加 app 实例 + Nginx 负载均衡
2. **检索层**: Milvus 支持分布式部署（需改配置）
3. **缓存层**: Redis Cluster
4. **推理层**: 多 vLLM 实例 + 路由层修改

### 垂直扩容

1. **GPU 升级**: 从 4B 升级到更大模型
2. **内存增加**: 提升 Redis 容量增加缓存命中率
3. **SSD**: 提升 Milvus/ES 的 I/O 性能

## 5. 备份策略

| 组件 | 备份方式 | 频率 | 保留 |
|------|----------|------|------|
| Milvus | 数据目录快照 | 每日 | 7 天 |
| Redis | AOF 持久化 | 实时 | 3 天 |
| MinIO | 对象存储快照 | 每周 | 4 周 |
| SQLite (用户) | 文件复制 | 每日 | 7 天 |
| 配置文件 | Git 版本控制 | 永久 | - |
