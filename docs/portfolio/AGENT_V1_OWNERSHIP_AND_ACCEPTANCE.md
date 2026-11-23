# Agent 工程标准 V1.0 — 行业 RAG 项目责任边界与验收台账

> 状态：执行台账，不是生产验收报告。基线：`main` @ `7a6dfff0dbfda9577ec6dcc6e83c42967d17b583`（2026-11-24 核查）。

## 求职项目定位与时间边界

- **唯一主责**：企业多模态知识工程、来源可信度、混合检索、两阶段排序、文档权限与证据拒答、检索实验。
- 与 `Customer-Service-AI-Agent` 属于**同一历史任职阶段**；客服项目负责 Agent 编排和 Runtime，本项目负责知识与检索，不重复造 Agent/Harness。
- `Gaobao-Advisor` 属于**另一段更近期任职经历**；不要把后续 Harness 实验写进本行业 RAG 项目的历史贡献。
- 本仓是独立维护的公开参考实现，不是任何企业的真实内部生产系统镜像；后续代码修复与任职期间的实际工作必须分开陈述。

## V1.0 模块责任

| 模块 | 本仓责任 | 已有入口 | 当前证据边界 |
| --- | --- | --- | --- |
| M08（主责） | PDF/DOCX/XLSX/OCR，多路召回、RRF、BiEncoder/CrossEncoder、证据绑定 | `offline/` `retrieval/` `core/pipeline.py` | 代码可核，复杂版面 MinerU/Docling 专项尚未集成与比较，不是必上组件 |
| M13（检索部分） | Golden Set、来源审批、检索消融、Bad Case、RAGAS | `benchmarks/` `tests/evaluation/` | 真实 ES BM25 27 条已审核基准有据；Dense/Hybrid/全链路仍 PENDING |
| M15（RAG 部分） | RBAC、知识 epoch、证据来源、注入与租户隔离 | `common/auth.py` `auth/` `tests/test_rbac_cross_identity.py` | 核心防御有测试，真实多租户全链路未验收 |
| M16（RAG 部分） | 单体服务部署、负载评估与故障指标 | `docker-compose.yml` `tests/load/` `docs/slo-runbook.md` | SLO 是设计目标，非实际生产结果 |

**明确不做**：为了让矩阵变绿而给 RAG 增加独立 Agent Runtime、Supervisors、退款写工具、MCP Server 或 Deep Agents；这些分别由客服或 Gaobao 证明。

## 第一批补全任务

| 编号 | 执行项 | 通过标准 | 当前状态 / 关联变更 |
| --- | --- | --- | --- |
| RAG-P0-01 | 无证据 Answer Gate 一律拒答 | `top_doc=None` 即 fail-closed；覆盖普通和法规类请求，正控制仍可用 | **PR #121 Open**；未合并 |
| RAG-P0-02 | 真实检索 Bad Case 复核 | 从排名结果独立重算 MRR/Recall/Hit，拒绝重复 ID、伪造分值与错误来源 | **PR #122 Open**；未合并 |
| RAG-P0-03 | 同金标 BM25 vs Dense vs Hybrid vs Rerank 消融 | 真实 ES/Qdrant/BGE/CE 权重、固定语料与标注、同一运行条件、逐样本结果和失败分类 | **PARTIAL**；只有 BM25 的局部真实测量 |
| RAG-P0-04 | 复杂版面与多模态真实解析验收 | 扫描 PDF、表格、图片的版面保真与 OCR 准确性样本；原文映射/检索质量对照 | **REFERENCE_IMPLEMENTED**：`benchmarks/document_structure_audit.py` + `tests/benchmark/test_document_structure_audit.py` 对解析后的页码覆盖、OCR 渲染丢页、身份重复、表格行区间冲突给出 PASS_STRUCTURE/FAIL/INCONCLUSIVE；**未对真实复杂版面 OCR 正确性或生产检索质量作结论** |
| RAG-P1-05 | 越权与 Prompt Injection 对抗样本 | 服务端/存储层过滤及缓存租户隔离，无证据和恶意文档必须可解释地拒答；指定对抗集回归 | **PARTIAL**；现有单位/集成测试不能证明生产攻击免疫 |
| RAG-P1-06 | GPU/服务端点/运行时压测 | vLLM 真模型、CE 真权重、完整请求链、P95/P99/错误率/Cost per Task 实测 | **PENDING**；配置拓扑不是运行验收 |

## 可复查的已有证据

- 真实 ES 8.11.0、212 文档、27 条签署金标，BM25 `Recall@10=0.7037`、`MRR@10=0.4088`，仅代表**该索引与该单臂实验**：`docs/validation/real-v1-approved-bm25-run.md`。
- 工程现状与所有待验收项：`docs/production-readiness.md`、`docs/architecture-baseline.md`、`docs/evidence-map.md`。
- 可执行基础检查：

```bash
python -m pytest tests/benchmark/test_document_structure_audit.py -q
python -m pytest tests/ -q
ruff check .
python scripts/check_repo_consistency.py
python -m benchmarks.retrieval_benchmark --list-configs
```

**注意**：`--list-configs` 输出 BLOCKED 是配置/依赖诊断，不是四路实测完成。禁止将旧的 301-case 候选直接当成人工金标，禁止把历史计划中的双 GPU / 微服务写成生产部署事实。

## 发布与简历约束

用 `IMPLEMENTED / REPO_VERIFIED / LOCAL_REAL_VALIDATION / PENDING / DESIGN_TARGET` 记录实际状态。仅真实验收通过后升级证据级别。此项目证明 RAG 设计与检索思维，不承担 Agent Runtime/编排能力的主要叙事。

参考：`DATA_PROVENANCE.md`、`docs/production-readiness.md`、`docs/slo-runbook.md`。
