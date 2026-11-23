# 2026 企业 RAG 技术专项定位与验收

## 经历定位
本仓与 Customer-Service-AI-Agent 对应**此前同一家公司的同一段任职经历**。本项目只集中证明企业知识解析、索引更新、权限检索、多模态召回、RRF 重排和证据门控，避免在这里重复实现客服 Agent Runtime。最新另一段经历的产品全栈/业务 Agent 项目是 Gaobao-Advisor。

公开项目是独立参考实现，不含未经授权的企业私有数据；公司生产部署及个人贡献不能从此公开仓库直接推出。

## 已验证与未验证
- 已有 Real Corpus V1 正式单路 BM25 实验：真实 ES、27 个审核样本、212 条审批索引文档，Recall@10=0.7037、MRR@10=0.4088；此结果**不是**整个多模态 RAG 的性能。
- Dense/Hybrid/RAGAS、生产延迟及旧版 301 条金标等工作仍然缺合法权重、人工标签或运行证据；PR #121 正在单独修复无证据 Answer Gate 防御。
- 保留的微服务代码不等于默认线上已完成联调，当前可验证主线是 FastAPI 单体。

## 本轮代码增量：真实检索 Bad Case 的诊断工具
新增 `benchmarks/failure_triage.py`，读取已有 benchmark 的 `per_query_results.jsonl`，分开统计：缺少金标而排除、Top10 未命中、2–10 名迟到命中、Top1 命中，并按配置与业务类型分桶；对重复样本/自相矛盾的命中结果 fail closed。

```bash
python -m benchmarks.failure_triage --per-query artifacts/benchmarks/<RUN_ID>/per_query_results.jsonl --out /tmp/rag-triage.json
pytest -q tests/benchmark/test_failure_triage.py
```

该输出**仅供诊断**，绝不赋予 benchmark 发布资格；来源审批、数据契约、真实检索执行器、代码归因仍必须通过现有 benchmark 门禁。后续优先获取合法 BGE/CLIP 权重，按同一金标比较 BM25/Dense/Hybrid/Rerank，定位八条已知 BM25 漏召回，并补答案级引用/权限/拒答负例验证。没有测出的指标继续标记 `PENDING` / `NOT_VERIFIED`。
