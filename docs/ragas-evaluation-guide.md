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

### 2.1 安装依赖

```bash
pip install ragas datasets
```

### 2.2 配置 LLM API Key

RAGAS 需要调用 LLM 来评估答案质量。默认使用 OpenAI：

```bash
export OPENAI_API_KEY=sk-your-key-here
```

### 2.3 运行评估

```bash
# 使用默认数据集（config.json 中配置的 golden_set.jsonl）
python -m tests.evaluation.ragas_eval

# 指定数据集和标签
python -m tests.evaluation.ragas_eval \
  --dataset tests/evaluation/golden_set.jsonl \
  --tag baseline-v1
```

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
- 条目数 ≥ 20
- 分类覆盖完整

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
1. 遍历黄金数据集中的每个问题
2. 通过 RAG 管线（`OnlineRAGPipeline.process()`）生成答案
3. 使用管线生成的答案评估 RAGAS 指标
4. 生成带管线答案的报告

**前置条件**：确保所有基础设施已就绪（vLLM、Qdrant、Elasticsearch、Redis）。

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

# 方式 3：端到端管线评估
results = evaluator.evaluate_with_pipeline(pipeline)

# 方式 4：生成结构化报告
reporter = RAGASReporter(evaluator)
report = reporter.run_and_report(tag="my-experiment")
print(reporter.format_report_markdown(report))
reporter.save_report(report, "./data/eval/reports")
```

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

RAGAS 评估在 CI 中作为可选 Job 运行，不阻塞合并：

```yaml
ragas-eval:
  name: RAGAS Evaluation (optional)
  runs-on: ubuntu-latest
  if: github.event_name == 'push' && github.ref == 'refs/heads/main'
  steps:
    - uses: actions/checkout@v4
    - name: Set up Python 3.11
      uses: actions/setup-python@v5
      with:
        python-version: "3.11"
    - name: Install dependencies
      run: pip install -r requirements.txt
    - name: Run RAGAS evaluation
      if: ${{ secrets.OPENAI_API_KEY != '' }}
      env:
        OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
        DEPLOYMENT_MODE: testing
      run: |
        python -m tests.evaluation.ragas_eval \
          --dataset tests/evaluation/golden_set.jsonl \
          --tag ci-${{ github.sha }} \
          --report-dir data/eval/reports
    - name: Upload report artifact
      if: always()
      uses: actions/upload-artifact@v4
      with:
        name: ragas-eval-report
        path: data/eval/reports/
```

---

## 7. 面试话术

当面试官问"怎么评估你的 RAG 系统"时，可以这样展示：

### 开场

> "我使用了 RAGAS 框架对 RAG 系统进行离线评估，覆盖 faithfulness、answer_relevancy、context_precision、context_recall 四个维度。我设计了一个 27 条数据的黄金数据集，覆盖了成分、法规、配方、图像、通用五种业务类型和 easy/medium/hard 三个难度级别。"

### 如何计算指标

> "Faithfulness 和 Answer Relevancy 评估生成质量——Faithfulness 看模型有没有编造（幻觉），Answer Relevancy 看答案是不是答非所问。Context Precision 和 Context Recall 评估检索质量——Precision 看检索回来的有多少是有用的，Recall 看该检的有没有漏检。这样检索和生成两个环节都有了量化评估。"

### 如何做优化

> "我做了基线评估后，通过优化检索策略（调整 BM25 权重、改进 chunk 策略）将 context recall 从 X% 提升到了 Y%。每次优化都通过 RAGAS 报告做对比验证，确保不会顾此失彼。"

### 体系设计

> "评估体系分为三部分：一是黄金数据集评估，用标准答案验证系统；二是管线端到端评估，用系统实际生成的答案验证；三是基线对比，跟踪版本演进。评估报告会自动保存为 JSON，支持加载历史报告做对比分析。"

---

## 8. 故障排除

| 问题 | 原因 | 解决 |
|------|------|------|
| `No module named 'ragas'` | RAGAS 未安装 | `pip install ragas datasets` |
| 所有分数为 0.0 | RAGAS 未安装（优雅降级） | 同上 |
| `AuthenticationError` | API Key 未配置 | `export OPENAI_API_KEY=sk-...` |
| 管线评估报错 | 基础设施未就绪 | 确保 vLLM / Qdrant / ES / Redis 在运行 |
| CLI 报 `FileNotFoundError` | 数据集路径错误 | 使用绝对路径或从项目根目录运行 |
| 报告输出为纯 JSON 而非 Markdown | 报告生成器依赖错误 | 检查 `tests/evaluation/ragas_report.py` 的导入 |

---

## 相关文件

| 文件 | 说明 |
|------|------|
| [tests/evaluation/ragas_eval.py](../tests/evaluation/ragas_eval.py) | RAGAS 评估器 |
| [tests/evaluation/test_ragas_eval.py](../tests/evaluation/test_ragas_eval.py) | 评估器单元测试 |
| [tests/evaluation/ragas_report.py](../tests/evaluation/ragas_report.py) | 评估报告生成器 |
| [tests/evaluation/golden_set.jsonl](../tests/evaluation/golden_set.jsonl) | 黄金数据集（27 条） |
| [tests/evaluation/validate_golden_set.py](../tests/evaluation/validate_golden_set.py) | 数据集验证工具 |
| [tests/evaluation/sample_golden_set.jsonl](../tests/evaluation/sample_golden_set.jsonl) | 示例数据集（5 条，兼容旧版） |