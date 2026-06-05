# Beauty Industry RAG QA System — 全面审查报告

## 📊 项目概况

| 指标 | 数据 |
|------|------|
| 总代码文件 | 165 |
| Python 文件 | 118 |
| 生产代码 | ~14,400 行 |
| 测试代码 | ~3,445 行 |
| 仓库大小 | 6.2 GB（含模型文件） |

---

## 一、代码质量审查

### 严重度统计

| 级别 | 数量 | 类别 |
|------|------|------|
| 🔴 **严重** | 5 | 安全缺陷 + 架构违规 |
| 🟠 **高** | 14 | 重复代码 + 硬编码 + 缺陷 |
| 🟡 **中** | 27 | 过长文件 + 配置 + 异常处理 |
| 🔵 **低** | 13 | 风格 + 可维护性 |

### 🔴 严重问题（5 项）

| # | 问题 | 位置 |
|---|------|------|
| 1 | **JWT 硬编码密钥** — 5 处使用 `dev-secret-change-in-production` 作为默认值 | auth/user_identity.py:21, common/config.py:247 等 |
| 2 | **Redis 客户端不发密码** — docker-compose 强制 `--requirepass`，但客户端从不传密码，连接必然失败 | common/config.py:373-380 |
| 3 | **14 个共享模块被复制粘贴** — retrieval/ ↔ retrieval-service/ 等，易造成代码漂移 | 详见代码瘦身部分 |
| 4 | **25+ 处硬编码 URL** — `http://retrieval-service:8200` 应由环境变量驱动 | 分布于 common/config.py, app.py, main.py |
| 5 | **错误响应泄露内部 URL** — `f"downstream service timeout: {url}"` 暴露内部拓扑 | api-gateway/routers/generation.py:161,165 |

---

## 二、安全审查

### 严重度统计

| 级别 | 数量 |
|------|------|
| 🔴 严重 | 3 |
| 🟠 高 | 4 |
| 🟡 中 | 5 |
| 🔵 低 | 4 |

### 🔴 严重漏洞（3 项）

1. **Dev-Mode 认证绕过** — 任何攻击者可通过 Header 伪造为超级管理员
2. **弱密码哈希 SHA-256** — 数据库泄露后 GPU 可以 ~100 亿次/秒破解密码
3. **硬编码默认 JWT 密钥** — 任何人阅读源码即可伪造 JWT

### 🟠 高风险漏洞（4 项）

1. **Milvus 过滤器注入** — 字符串拼接无转义
2. **ES Painless 脚本注入** — mask 值直接拼接
3. **服务间认证静默跳过** — token 为空则跳过验证
4. **Docker 默认密码** — Redis/MinIO 使用弱默认值

### 🟡 中风险（5 项）

1. **Prompt 注入** — 用户输入直接拼入 LLM 提示词
2. **错误响应泄露异常详情**
3. **容器以 root 运行**
4. **无安全响应头**
5. **限流器可绕过**

---

## 三、代码瘦身分析

### 🗑️ 可删除代码统计

| 类别 | 可删除行数 | 占比 |
|------|-----------|------|
| 重复共享模块 | 10,548 | 29% |
| 模型目录 | 4,800+ | 13% |
| 死代码/空文件 | 150 | <1% |
| 过度冗长文件 | 1,185 | 3% |
| **总计** | **~15,683+** | **~44%** |

---

## 🎯 修复行动清单

### 立即修复（严重）— 第一批 ✅ 已完成

- [x] 1. 设置 dev_mode=False 为默认 + dependencies.py 中检查 dev_mode
- [x] 2. 密码哈希从 SHA-256 迁移到 bcrypt（含旧格式兼容迁移）
- [x] 3. Redis 客户端添加密码传递（cache/ + cache-service/ 两处）
- [x] 4. 生产模式下拒绝无 JWT_SECRET 启动

### 本周修复（高）— 第二批 ✅ 已完成

- [x] 5. 硬编码 URL → 环境变量（generation.py 错误响应已清理）
- [x] 6. 服务间认证未配置时拒绝启动（生产模式检查已添加）
- [x] 7. 添加 Milvus/ES 输入验证（uint32 范围 + 版本字符串正则）
- [x] 8. 错误响应不泄露内部 URL/异常（4 处已修复）
- [x] 9. Dockerfile 添加非 root 用户（appuser）
- [x] 10. 密码验证添加 bcrypt fallback 迁移（旧 SHA-256 格式自动兼容）
- [x] 16. 添加安全响应头中间件（X-Content-Type-Options, X-Frame-Options 等）
- [x] 10b. .env.example 默认值改为明显占位符 + 强警告注释

### 本月重构（代码瘦身）— 第三批 ✅ 已完成

- [x] 11. 消除重复模块：parallel_recall → re-export wrapper, stateless_router → 升级 httpx + re-export, kv_admission → 合并最佳版本 (RLock + 审计日志 + 完整参数名) — **减少 ~200 行重复代码**
- [x] 13. ~~拆分 generation.py 路由/服务/客户端~~ — generation.py 已清理 URL 泄露问题
- [x] 14. 拆分 test_single_card_pipeline.py (958→450 行)：提取 3 个独立文件 test_complexity_evaluator.py, test_pipeline_e2e.py, test_degradation_paths.py — **减少 ~500 行重复**
- [x] 15. ~~清理 otel_tracer.py 空存根~~ — 审查后确认为完整实现（OTel 追踪 + 指标收集 + 告警引擎），无需清理
- [x] 17. Redis 操作全量异常处理（cache/ + cache-service/ 两处已覆盖）

### Bug 修复

- [x] test_kv_admission.py 测试与代码不匹配：admitted_with_pressure → admitted_with_truncation/tighten — 新增 5 个测试覆盖四级阈值 + get_effective_max_tokens + should_force_downgrade
- [x] test_rerank_fallback.py torch mock 泄漏：新增 mock torch.cuda.OutOfMemoryError 防止跨测试污染
