# Agent 工程标准 V1.0 — 行业 RAG 项目责任边界与验收台账

**2026-11-25 增量**：#155 已合入主线；工业候选的来源审批/有效期、实时权限、故障状态、容器及 readiness 变更见 [工业发布合同](industrial-release-contract.md)。CI 通过也不替代真实模型/数据及运行环境验收。

> 状态：执行台账，不是生产验收报告。基线：`main` @ `f746ae5b8cfd89f7d228af9f6dc4fba8f1a35149`（2026-11-25 核查；CI/Lint/Security 成功；不是生产验收）。

## 技术范围与数据边界

- **唯一主责**：企业多模态知识工程、来源可信度、混合检索、排序架构裁决、文档权限与证据拒答、检索实验。
- `Customer-Service-AI-Agent` 独立负责 Agent 编排与 Runtime；本项目仅实现行业知识解析、检索和证据质量机制，不重复构建 Agent/Harness。
- `Gaobao-Advisor` 单独演示只读结构化业务任务、Skills/Context 和 Eval；本项目不继承其技术验收状态。
- 本仓是独立维护的公开工程参考实现，测试结果仅对应指定源码、测试数据和运行环境；不代表组织内部生产系统。

## V1.0 模块责任

| 模块 | 本仓责任 | 已有入口 | 当前证据边界 |
| --- | --- | --- | --- |
| M08（主责） | PDF/DOCX/XLSX/OCR，多路召回、RRF、BiEncoder/CrossEncoder、证据绑定 | `offline/` `retrieval/` `core/pipeline.py` | 代码可核，复杂版面 MinerU/Docling 专项尚未集成与比较，不是必上组件 |
| M13（检索部分） | Golden Set、来源审批、检索消融、Bad Case、RAGAS | `benchmarks/` `tests/evaluation/` | 真实 ES BM25 27 条已审核基准有据；Dense/Hybrid/全链路仍 PENDING |
| M15（RAG 部分） | RBAC、知识 epoch、证据来源、注入与租户隔离 | `common/auth.py` `auth/` `tests/test_rbac_cross_identity.py` | 核心防御有测试，真实多租户全链路未验收 |
| M16（RAG 部分） | 单体服务部署、负载评估与故障指标 | `docker-compose.yml` `tests/load/` `docs/slo-runbook.md` | SLO 是设计目标，非实际生产结果 |

**明确不做**：为了让矩阵变绿而给 RAG 增加独立 Agent Runtime、Supervisors、退款写工具、MCP Server 或 Deep Agents；这些分别由客服或 Gaobao 证明。

## 当前架构治理与证据边界

- #144/#145 已合入真实模型资产绑定及严格 BiEncoder/CE 执行器；#157 已合入非 canonical 部署隔离，仍不代表实际集群部署。
- #155 是十组排序消融的集成候选，包含 CE 冷启动分数方向修复、缓存策略隔离、失败精排门禁与并发状态回归。#156 为重叠的旧单 CE Draft，不独立合并。最新 PR/CI 以 GitHub 实时记录为准，真实权重、独立校准、法规拒答/质量和延迟仍待验收。
- #158 已于本轮合并（`5e2441eb1a894d1daaca88388f85d93412262dd7`），合并前 CI/Lint/Security 全部通过；包含来源审批和索引内容漂移检测。#155 新增图片、运行时 A/B 覆盖及降级回答缓存隔离，尚待合并；#159 独立拒答判分亦待验收。不能把合成测试或旧 BM25 单臂证据冒充完整排名验收。保持 #154/#18/#125 OPEN。

## 第一批补全任务

| 编号 | 执行项 | 通过标准 | 当前状态 / 关联变更 |
| --- | --- | --- | --- |
| RAG-P0-01 | 无证据 Answer Gate 一律拒答 | `top_doc=None` 即 fail-closed；覆盖普通和法规类请求，正控制仍可用 | **MERGED TO MAIN**：PR #121 已于 2026-11-23 合并（merge SHA `5b14f187329f3e64f615408987432f2dcc499d87`），对应 CI/Lint/Security 通过；真实生产外部验收仍 PENDING |
| RAG-P0-02 | 真实检索 Bad Case 复核 | 从排名结果独立重算 MRR/Recall/Hit，拒绝重复 ID、伪造分值与错误来源 | **MERGED TO MAIN**：PR #122 已于 2026-11-24 合并（merge SHA `910ad800de50e9700fcbdd139d00d2a86b8cdaa8`）；后续检索质量改进仍待真实模型与语料消融 |
| RAG-P0-03 | 同金标 BM25 vs Dense vs Hybrid vs Rerank 消融 | 真实 ES/Qdrant/BGE/CE 权重、固定语料与标注、同一运行条件、逐样本结果和失败分类 | **PARTIAL**；只有 BM25 的局部真实测量 |
| RAG-P0-04 | 复杂版面与多模态真实解析验收 | 扫描 PDF、表格、图片的版面保真与 OCR 准确性样本；原文映射/检索质量对照 | **REFERENCE_IMPLEMENTED**：`offline/document_processor.py` 可选 `pdf_extract_tables=True` 经真实 PyMuPDF 处理带表格 PDF，并保留页码/表号/块 ID；`benchmarks/document_structure_audit.py` 检查缺页、OCR 渲染丢页、重复身份及表格行区间；测试包含合成 PDF 真解析和失败反例。**尚无真实行业复杂版面人工标注、OCR 正确率或检索收益实验，默认解析行为未改变** |
| RAG-P1-05 | 越权与 Prompt Injection 对抗样本 | 服务端/存储层过滤及缓存租户隔离，无证据和恶意文档必须可解释地拒答；指定对抗集回归 | **PARTIAL**；现有单位/集成测试不能证明生产攻击免疫 |
| RAG-P1-06 | GPU/服务端点/运行时压测 | vLLM 真模型、CE 真权重、完整请求链、P95/P99/错误率/Cost per Task 实测 | **PENDING**；配置拓扑不是运行验收 |

## 可复查的已有证据

- 真实 ES 8.11.0、212 文档、27 条签署金标，BM25 `Recall@10=0.7037`、`MRR@10=0.4088`，仅代表**该索引与该单臂实验**：`docs/validation/real-v1-approved-bm25-run.md`；可复核数据：`docs/validation/evidence/real-v1-approved-bm25-evidence.json`。
- 工程现状与所有待验收项：`docs/production-readiness.md`、`docs/architecture-baseline.md`、`docs/evidence-map.md`。
- 可执行基础检查：

```bash
python -m pytest tests/offline/test_pdf_table_extraction.py tests/benchmark/test_document_structure_audit.py tests/benchmark/test_document_structure_cli.py -q
python -m scripts.audit_document_structure /path/to/approved.pdf
python -m pytest tests/ -q
ruff check .
python scripts/check_repo_consistency.py
python -m benchmarks.retrieval_benchmark --list-configs
```

**注意**：`--list-configs` 输出 BLOCKED 是配置/依赖诊断，不是四路实测完成。禁止将旧的 301-case 候选直接当成人工金标，禁止把历史计划中的双 GPU / 微服务写成生产部署事实。

## 发布和验收限制

用 `IMPLEMENTED / REPO_VERIFIED / LOCAL_REAL_VALIDATION / PENDING / DESIGN_TARGET` 记录实际状态。仅真实验收通过后升级证据级别。此项目的可验证范围是 RAG 解析、检索、证据和权限边界，不承担 Agent Runtime/编排模块的主要功能。

参考：`DATA_PROVENANCE.md`、`docs/production-readiness.md`、`docs/slo-runbook.md`。
