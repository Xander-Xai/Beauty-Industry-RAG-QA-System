# 工业发布合同与验收门禁（候选）

日期：2026-11-25。基于 main 合入 #155 后的 ea46ab8a。本文描述治理候选分支，不是生产上线报告。版本仍为 2.4.0 / Unreleased。

## 唯一架构

- 离线：批准来源 → 文件/版本/哈希校验 → PDF、表格、OCR、图片解析 → 文本 BGE 与图像 CLIP 向量化 → Qdrant + Elasticsearch 同 epoch 写入 → 完整性验证/封存 → 人工批准发布、保留回滚。
- 在线：JWT 身份 → 最新 RBAC → Rewrite 与复杂度路由 → 动态 Dense/BM25/Rewrite variants/CLIP → RBAC 后置校验 → 审定证据过滤 → RRF → BiEncoder → 双 CrossEncoder → Evidence Gate → 4B/14B 生成 → Answer Gate → 发布前证据复核 → 引用/审计/会话。
- 单一 FastAPI 模块化单体；旧微服务/K8s 实验不作为第二套生产架构。不得在没有质量与延迟消融证据时删除排序模块。Redis 不是证据库。

## 工业模式

通过 RAG_RELEASE_PROFILE=industrial 启用，要求 production、RS256、预配置匹配密钥、持久身份库、HTTPS CORS、认证后端和有效发布清单。默认开发模式保留测试兼容能力，不得对外发布。工业模式禁用 HS256 兼容回退、内存会话/限流回退、答案缓存、在线 A/B、异步视觉补充和实时 BLIP 内容注入。原始图片路径及未版本绑定的媒体接口明确拒绝。CLIP 召回仅可提供审定的 OCR 文本，不代表图像内容已经获批。每次 API 鉴权复核身份库中角色、部门和 active；权限变化后旧 token 拒绝。会话作用域包含用户、角色、部门、epoch、manifest SHA256、北京时间日期。

## 独立审定发布清单

JSON schema 字段：schema="rag-release/v1"、epoch（禁止 default）、valid_from、valid_until、approval_ref、records。records 每项：record_id（chunk_id 或 image_id）、doc_id、content_sha256（精确 UTF-8 文本哈希）、role_mask、dept_mask、valid_from、valid_until、source_ref、review_ref。日期 ISO 8601，闭区间。部署者须独立核验来源、法规生效/废止、提取文本及授权；禁止从待发布索引自动导出后自行认定获批。清单作为只读受控部署输入，SHA256 独立传入。哈希校验不是数字签名，也不是法律审查。

清单无效/整体过期/epoch 不符即拒绝服务。逐条身份、状态、哈希、权限和日期不符合的证据不得进入融合或生成；生成完成再次复核。不自动切换现有索引。跨日会话隔离不是法律适用性判定，历史法律查询需单独设计审定策略。

## 部署与验收

`docker compose --env-file .env.industrial -f docker-compose.industrial.yml config --quiet` 校验候选部署配置（不要输出凭据）。配置文件、独立审批清单、私钥及用户库、模型目录从仓外只读/受控挂载；镜像只保留 Python 模型代码。外部 Qdrant/ES、Redis、两个 vLLM endpoint 由运营者提供。入口绑定回环地址，由企业 HTTPS 反代接入；工业配置不自动创建/切换索引。单机单 worker 为当前边界，SQLite 仅适合单实例身份管理；不能宣称 HA 或水平扩展。

| 门禁 | 状态/验收要求 |
|---|---|
| API、索引和身份保护 | 已编码；需 exact-head CI、真实依赖故障注入与攻击回归 |
| 发布清单及权限/时效 | 已编码；必须使用独立审定的当前有效语料，验证无未审批/过期/越权证据 |
| 模型 | CE 排序修复已在 main；BGE/CLIP/CE/NLI/4B/14B 权重、GPU 启动及真实门控待实测，不能以探针连通代替推理通过 |
| 会话 | 已隔离发布范围并禁止 Redis 静默回退；同 session 并行写冲突、重试幂等及故障恢复待验收 |
| 视觉 | 离线 OCR/CLIP 与在线文字证据检索保留；原图对象版本、权限撤销及安全预签名未验收，工业接口关闭 |
| 评测 | 历史 BM25 27 条样本，仅对该样本/配置有效；真实 Dense/RRF/单双 CE/NLI、端到端 RAGAS 和拒答质量仍缺证据 |
| 运营 | 依赖认证、TLS/ACL、审计脱敏、备份恢复、负载/P95/成本、回滚、真实数据及模型签收均需运行环境验收 |

**不应上线的条件**：任一业务必需能力未通过验收、真实模型缺失、审批清单不存在或过期、索引版本不一致、真实权限测试失败。不得为通过 readiness 放松门控。此候选未运行真实生产数据、未修改生产索引、未部署服务。


交付修复：模型代码目录采用逐文件 Docker context 白名单，仅允许已跟踪的六个 Python 源码文件，禁止重新引入模型权重或秘密。发布合同不在模块导入阶段引用 core，避免 API 启动的循环导入。图像召回必须保留审核所需的 image_id/status/epoch 元数据；缺字段仍不得通过发布清单。#159 独立拒答理由与精确率评测已合并，属于离线评测而非上线证据。
