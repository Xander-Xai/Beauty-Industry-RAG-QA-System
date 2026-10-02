# RAGAS 评估指南

> 本文档说明如何使用 RAGAS 框架对本项目进行离线质量评估。

---

## 1. 概述

RAGAS（Retrieval Augmented Generation Assessment）是 RAG 系统评估的事实标准框架。它通过 4 个核心指标量化评估 RAG 管线的质量：

| 指标 | 评估对象 | 说明 | 面试话术 |
|------|---------|------|---------|
| **Faithfulness** | 生成答案 | 答案是否忠于检索上下文（有没有幻觉）。衡量模型的"诚实度"。 | "最核心的指标——直接衡量 RAG 的检索有没有真正发挥作用，防止模型凭空编造。" |
| **Answer Relevancy** | 生成答案 | 答案和问题的相关程度。 | "衡量生成质量——检索到了正确信息，但模型答非所问就是这里得分低。" |
| **Context Precision** | 检索过程 | 检索到的上下文中有多少比例是相关的。 | "衡量检索精度——检索回了 10 篇文档但只有 3 篇有用，就是 precision 低。" |
| **Context Recall** | 检索过程 | 所有相关信息中，有多少比例被检索到了。 | "衡量检索覆盖度——如果相关信息没被检索到，神仙模型也答不对。" |

**评估流程**：
1. 准备黄金数据集（`golden_set.jsonl`），包含 **question / answer / contexts / ground_truth**
2. 运行评估器，RAGAS 调用 LLM 对每个指标打分
3. 生成结构化报告，按业务类型和难度做类别拆分
4. 支持基线对比和版本演进追踪

---

## 2. 快速开始

### 2.1 安装依赖（隔离环境）

RAGAS **不在默认 `requirements.txt` 中**（上游依赖存在未修复的安全问题，且最新版
`ragas 0.4.x` 当前 `import` 即失败）。请在一次性 venv 中安装固定的 evaluator 依赖：

```bash
python -m venv .venv-ragas && . .venv-ragas/bin/activate
pip install -r requirements-ragas.txt   # ragas==0.2.15 + 兼容的 langchain 0.3.x
pip check                                # 应为 No broken requirements found
```

安全提示：`requirements-ragas.txt` 已记录 `pip-audit` 发现的已知漏洞，仅用于本地/临时
评估环境，**不得**合并进生产镜像。

### 2.2 配置 LLM API Key

RAGAS 需要调用 LLM 来评估答案质量。默认使用 OpenAI（provider/model 来自
`config.json` → `ragas.llm_backend`，可用环境变量覆盖）：

```bash
export OPENAI_API_KEY=sk-your-key-here
# 可选覆盖：
# export RAGAS_EVALUATOR_PROVIDER=openai
# export RAGAS_EVALUATOR_MODEL=gpt-4o-mini
# export RAGAS_EVALUATOR_BASE_URL=
```

### 2.3 运行评估

一次 run 只调用 evaluator 一次；缺少依赖或 evaluator key 时明确失败且不生成报告。

```bash
# 评估器 smoke（使用数据集 reference 答案；不代表真实 pipeline 质量）
python -m tests.evaluation.ragas_eval --require-ragas --limit 15

# 真实 pipeline 端到端评估（需要 vLLM/Qdrant/ES/Redis）
python -m tests.evaluation.ragas_eval --require-ragas --pipeline --limit 15 \
  --dataset tests/evaluation/golden_set.jsonl --tag baseline-v1

# 指定样本 id
python -m tests.evaluation.ragas_eval --require-ragas --pipeline \
  --sample-ids 0000-ab12cd34 0007-ef56ab78
```

退出码：缺 RAGAS 依赖 `2`；缺 evaluator key `3`；管线初始化失败 `4`；全部样本失败 `5`；
成功 `0`。任何失败路径都不会写出“成功”报告。

### 2.4 查看报告

CLI 输出为格式化 Markdown，同时自动保存到 `./data/eval/reports/` 目录：

```bash
# 查看已保存的报告
cat data/eval/reports/ragas_report_baseline-v1_2026-06-26.json
```

---

## 3. 黄金数据集管理

### 3.1 数据格式

每条数据是一个 JSON 对象，包含以下字段：

| 字段 | 类型 | 必须 | 说明 |
|------|------|------|------|
| `question` | string | ✅ | 用户问题 |
| `answer` | string | ✅ | 标准答案（黄金答案） |
| `contexts` | array[string] | ✅ | 检索上下文列表 |
| `ground_truth` | string | ✅ | 完整标注答案（用于评估上下文召回率） |
| `business_type` | string | ❌ | 业务类型：`regulation` / `ingredient` / `formula` / `image` / `general` |
| `difficulty` | string | ❌ | 难度：`easy` / `medium` / `hard` |

分类字段会用于报告的类别拆分分析，建议每条都标注。

### 3.2 验证工具

```bash
python -m tests.evaluation.validate_golden_set --dataset tests/evaluation/golden_set.jsonl
```

校验项：
- 所有必需字段存在
- contexts 为字符串列表
- 无空 question / answer / ground_truth
- business_type 和 difficulty 在允许集合内
- 条目数 ≥ 300（`validate_golden_set.MIN_ENTRIES`；最初 seed 为 27 条，现已扩展）
- 分类覆盖完整

> **条目数以工具输出为准**：数据集会持续增长，文档不固化精确条数。以
> `python -m tests.evaluation.validate_golden_set --dataset tests/evaluation/golden_set.jsonl`
> 的输出和 `golden_set.jsonl` 实际行数为准。

### 3.3 添加新条目

1. 按上述 JSON 格式编写一条数据
2. 追加到 `golden_set.jsonl` 末尾
3. 运行验证工具确认格式正确
4. 重新运行评估器验证指标

**设计原则**：
- 覆盖所有业务类型（成分、法规、配方、图像、通用）
- 覆盖所有难度级别（简单、中等、困难）
- 包含边缘案例：否定查询、多意图查询、模糊查询、跨域查询
- 定期更新以反映知识库变化

---

## 4. 管线评估

### 4.1 端到端管线评估

```bash
python -m tests.evaluation.ragas_eval --pipeline --tag v1.0-review --dataset tests/evaluation/golden_set.jsonl
```

管线模式会：
1. 遍历选定样本（`--limit` / `--sample-ids`）
2. 通过真实 RAG 管线（`OnlineRAGPipeline.process()`）生成答案并记录真实 retrieved contexts
3. 用管线答案与真实召回上下文评估 RAGAS 指标
4. 生成带 pipeline provenance 的报告

**前置条件**：确保所有基础设施已就绪（vLLM、Qdrant、Elasticsearch、Redis）。

**重要**：只有此模式才是“真实 pipeline 质量”。`--pipeline` 之外的默认模式使用数据集
reference 答案，仅为 evaluator smoke，不能作为项目质量证据。管线中失败的样本会被记录并
从聚合分母中排除；全部失败时整次运行失败。

### 4.2 编程式使用

```python
from tests.evaluation.ragas_eval import RAGASEvaluator
from tests.evaluation.ragas_report import RAGASReporter

# 初始化评估器
evaluator = RAGASEvaluator("tests/evaluation/golden_set.jsonl")

# 方式 1：仅评估黄金数据集本身（验证数据集质量）
results = evaluator.evaluate()


# 方式 2：使用自定义答案函数（比如调用管线）
def my_rag_answer(question: str, contexts: list[str]) -> str:
    # 调用你的 RAG 管线
    return pipeline_answer


results = evaluator.evaluate_with_custom_answer_fn(my_rag_answer)

# 方式 3：端到端真实管线评估（记录真实 answer + contexts）
results = evaluator.run_pipeline_samples(pipeline, limit=15)

# 方式 4：基于已完成的评估生成报告（不会重复调用 evaluator）
reporter = RAGASReporter(evaluator)
report = reporter.build_report(tag="my-experiment", pipeline_mode=True)
print(reporter.format_report_markdown(report))
reporter.save_report(report, "./data/eval/reports")
```

报告包含 provenance：`git_commit`、`dataset`/`dataset_sha256`、`sample_ids`、
`evaluator_provider`/`evaluator_model`、`generation_model`、`mode`、`metrics`、
`requested/successful/failed/skipped` 计数与 `failures`。一次 run 只调用 evaluator 一次
（`run_and_report` 向后兼容，同样只调用一次）。

### 4.3 对比实验

```python
from tests.evaluation.ragas_report import RAGASReporter

reporter = RAGASReporter(evaluator)

# 基线
baseline = reporter.load_report("data/eval/reports/ragas_report_baseline_2026-06-26.json")

# 新版本
comparison = reporter.load_report("data/eval/reports/ragas_report_v2_2026-06-27.json")

# 对比
result = reporter.compare_reports(baseline, comparison)
print(f"判定: {result.verdict}")  # improved / regressed / mixed
print(reporter.format_comparison_markdown(result))
```

---

## 5. 配置参考

在 `config.json` 中配置 RAGAS：

```json
{
  "ragas": {
    "enabled": true,
    "dataset_path": "tests/evaluation/golden_set.jsonl",
    "default_metrics": ["faithfulness", "answer_relevancy", "context_precision", "context_recall"],
    "llm_backend": {
      "type": "openai",
      "model": "gpt-4o-mini",
      "api_base": "",
      "api_key_env": "OPENAI_API_KEY"
    },
    "report_output_dir": "./data/eval/reports",
    "baseline_tag": "baseline",
    "comparison_tags": []
  }
}
```

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `enabled` | true | 是否启用 RAGAS 评估模块 |
| `dataset_path` | `tests/evaluation/golden_set.jsonl` | 黄金数据集路径 |
| `default_metrics` | 4 个标准指标 | 默认评估指标列表 |
| `llm_backend.model` | `gpt-4o-mini` | LLM 模型（建议使用低成本模型） |
| `llm_backend.api_key_env` | `OPENAI_API_KEY` | API Key 的环境变量名 |
| `report_output_dir` | `./data/eval/reports` | 报告输出目录 |
| `baseline_tag` | `baseline` | 基线报告标签 |
| `comparison_tags` | `[]` | 需要与基线对比的报告标签列表 |

---

## 6. CI 集成

CI **分为两个职责分离的 job**（见 `.github/workflows/ci.yml`）：

### 6.1 deterministic evaluation guard（始终运行）

不安装 `ragas`，只做与依赖无关的确定性校验：

- golden set schema / 条目数 / 分类校验（`validate_golden_set`）
- RAGAS harness 与 reporter 单元测试（`pytest tests/evaluation/`）

这保证了“缺依赖时的失败路径”是可测试的：CLI 不会生成被误读为质量结果的报告。

### 6.2 real RAGAS evaluation（显式启用）

默认不运行。仅当仓库变量 `RAGAS_EVAL_ENABLED == 'true'` 且配置了 `OPENAI_API_KEY` 时才运行，并显式安装 `ragas`：

```bash
python -m tests.evaluation.ragas_eval \
  --dataset tests/evaluation/golden_set.jsonl \
  --tag ci-${{ github.sha }} \
  --report-dir data/eval/reports \
  --require-ragas
```

`--require-ragas` 在缺少 RAGAS 依赖时以退出码 2 失败，**绝不生成零分报告**。这是有意的安全策略：默认依赖集合不安装存在未决安全问题的 RAGAS 包，真实评估必须由维护者显式启用并自行评估依赖/安全策略。

---

## 7. 面试话术

当面试官问"怎么评估你的 RAG 系统"时，可以这样展示：

### 开场

> "我使用了 RAGAS 框架对 RAG 系统进行离线评估，覆盖 faithfulness、answer_relevancy、context_precision、context_recall 四个维度。黄金数据集最初是 27 条 seed，现已扩展到 300+ 条（实际条数以 `validate_golden_set` 输出为准），覆盖成分、法规、配方、图像、通用五种业务类型和 easy/medium/hard 三个难度级别。"

### 如何计算指标

> "Faithfulness 和 Answer Relevancy 评估生成质量——Faithfulness 看模型有没有编造（幻觉），Answer Relevancy 看答案是不是答非所问。Context Precision 和 Context Recall 评估检索质量——Precision 看检索回来的有多少是有用的，Recall 看该检的有没有漏检。这样检索和生成两个环节都有了量化评估。"

### 如何做优化

> "我做了基线评估后，通过优化检索策略（调整 BM25 权重、改进 chunk 策略）来提升 context recall。每次优化都通过 RAGAS 报告做对比验证，确保不会顾此失彼。"
>
> **示例占位符说明**：不要在未提供真实 RAGAS 报告前声称任何具体提升百分比（如 "从 X% 到 Y%"）。本项目当前没有经过验证的 RAGAS quality score；格式校验通过 ≠ 领域事实正确，golden set 存在 ≠ 质量分数有效。

### 体系设计

> "评估体系分为三部分：一是黄金数据集评估，用标准答案验证系统；二是管线端到端评估，用系统实际生成的答案验证；三是基线对比，跟踪版本演进。评估报告会自动保存为 JSON，支持加载历史报告做对比分析。"

---

## 8. 故障排除

| 问题 | 原因 | 解决 |
|------|------|------|
| `No module named 'ragas'` / `langchain_community...vertexai` | RAGAS 未安装，或最新版 `ragas` 与 `langchain-community` 不兼容 | 在隔离 venv 中 `pip install -r requirements-ragas.txt`；CLI 报 UNAVAILABLE 且不生成报告 |
| evaluate() 返回全 0 分数 | 库级 fallback，不是质量结果 | CLI 已 fail fast：以非 0 退出且不写报告；用 `--require-ragas` 强制真实依赖 |
| `RAGAS BLOCKED` / exit 3 | 真实 evaluator 缺少 provider API key | `export OPENAI_API_KEY=...` |
| `AuthenticationError` | API Key 无效或未配置 | 检查 `OPENAI_API_KEY` 与 provider/base_url |
| 管线评估报错 / exit 4 | 基础设施未就绪 | 确保 vLLM / Qdrant / ES / Redis 在运行 |
| 部分样本失败 | 单条 pipeline 请求失败 | 查看报告 `failures`；成功样本仍会聚合，全部失败则 exit 5 |
| CLI 报 `FileNotFoundError` | 数据集路径错误 | 使用绝对路径或从项目根目录运行 |
| 报告输出为纯 JSON 而非 Markdown | 报告生成器依赖错误 | 检查 `tests/evaluation/ragas_report.py` 的导入 |

---

## 相关文件

| 文件 | 说明 |
|------|------|
| [tests/evaluation/ragas_eval.py](../tests/evaluation/ragas_eval.py) | RAGAS 评估器 |
| [tests/evaluation/test_ragas_eval.py](../tests/evaluation/test_ragas_eval.py) | 评估器单元测试 |
| [tests/evaluation/ragas_report.py](../tests/evaluation/ragas_report.py) | 评估报告生成器 |
| [tests/evaluation/golden_set.jsonl](../tests/evaluation/golden_set.jsonl) | 黄金数据集（seed 27 条，现 300+；实际条数以校验工具输出为准） |
| [tests/evaluation/validate_golden_set.py](../tests/evaluation/validate_golden_set.py) | 数据集验证工具 |
| [tests/evaluation/sample_golden_set.jsonl](../tests/evaluation/sample_golden_set.jsonl) | 示例数据集（5 条，兼容旧版） |