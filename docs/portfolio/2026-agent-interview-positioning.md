# 2026 Agent 求职定位：企业多模态 RAG 与证据可信性（早期任职阶段）

> **文档性质**：工程证据与求职验收映射。仓库所有者将本项目与 Customer-Service-AI-Agent 归于同一较早任职阶段；公开实现不是任何雇主的官方源码/生产镜像。主分支后续公开改动及指标不能自动算作历史任职贡献。

## 唯一主线

企业知识摄取、版本发布、权限隔离、BM25/Dense/视觉多路检索、RRF/Rerank、Evidence/Answer 双 Gate，以及可归因的真实检索评测。本仓库不补客服多 Agent Runtime，也不引入 Gaobao 独立 Harness 作为主线。

## 当前代码与证据边界

| 责任 | 入口 | 当前证据 |
|---|---|---|
| 离线多格式解析、OCR、知识版本 | `offline/`, `dags/` | 代码/确定性测试；真实调度未执行 |
| 动态召回与融合 | `retrieval/parallel_recall.py`, `core/pipeline.py` | 代码/测试；Dense/Hybrid 真实数据效果未测 |
| 重排与双 Gate | `retrieval/cross_encoder_ensemble.py`, `retrieval/evidence_gate.py`, `retrieval/answer_gate.py` | 缺权重时会触发拒答降级，不能称完整正常运行 |
| RBAC / 版本 / 缓存隔离 | `docs/architecture-baseline.md`, `tests/test_bitmask_rbac.py` | 代码级验证不等于真实全集成 |
| 检索证据 | `docs/validation/real-v1-approved-bm25-run.md` | 27 条具名审阅金标、真实 ES 8.11.0 的 BM25 单臂；不能当 Dense/Hybrid 结果 |
| 生产门禁 | `docs/production-readiness.md`, `docs/deferred-runtime-validation.md` | 真实完整 API、生产集群、吞吐/SLO 仍待验证 |

## 缺口责任与执行顺序（只在本仓库处理）

1. **P0 — 真正的 Dense/Hybrid benchmark（Issue #18）**：在**相同合规真实语料、同一冻结 Gold、相同检索条件**下实跑 BM25、BGE Dense、Hybrid+RRF；提交模型版本、权重哈希、索引别名、语料 hash、逐样本排名、Hit/Recall/MRR/NDCG 与成本/延迟。缺模型资源时明确 `PENDING`，不得使用 Mock 冒充。
2. **P0 — Rerank/Gate 正常路径**：加载真实 CrossEncoder 权重，跑至少一条有证据成功、一条缺证据拒答、一条法规冲突拒答；报告无权重的全拒答降级以及是否达到业务可用标准。Answer Gate 的 fail-closed 改进已有 PR #121。
3. **P0 — Bad Case 归因（PR #122）**：独立复算提交的排名与 gold 身份，分类 Top1/Top10 命中和未命中、错误来源/版本、误拒答、越权风险；不把回放分析冒充新的 Dense/Hybrid 真实检索测量。
4. **P1 — 实际浏览器到 RAG（Issue #60）**：React → FastAPI → 真正 ES/Qdrant/Redis/模型服务，完成允许/拒绝权限、引用、会话延续、降级的跨端 smoke；提交执行日期、环境、日志脱敏与 git SHA。
5. **P1 — 真实答案质量与性能（Issue #12、#32）**：RAGAS/人工判断有可核验真值；性能单独记录真实请求 p50/p95/p99、环境与负载。2021 历史法规模型语料不能作为 2026 现行法规准确率证明。
6. **P2 — 真实调度/部署（Issue #8、#54）**：PaddleOCR、CLIP、Airflow、K8s 仅在真实运行证据提交后提升等级；微服务目录可导入不是完成生产联调。

## 面试最小验收包

- 实际同题 BM25 vs Dense vs Hybrid 排名和检索效果对照，附 3 条 Bad Case 修复前后结果。
- 有权限用户与无权限用户对同一知识问题的对照 Trace，证据只来自获授权文档。
- 正常权重下的 Evidence Gate 与 Answer Gate 示例，以及缺权重的 fail-closed 路径。
- 知识 epoch 封存、激活、回滚的操作演示（实际做过才宣称操作结果）。
- 可复算报告：gold 来源、评审范围、样本量、语料/模型版本、运行 SHA、指标局限。

## 跨项目分工

- [Customer Service Agent](https://github.com/Xander-Xai/Customer-Service-AI-Agent)：同一较早任职阶段，负责 Agent 编排、执行与故障治理。
- [Gaobao](https://github.com/Xander-Xai/Gaobao-Advisor)：后续不同任职阶段，负责 Harness/模型业务 Eval/全栈交付。
- 这里的“任职阶段”是履历时间线而非代码溯源证明；所有生产和绩效数字需独立核验。
