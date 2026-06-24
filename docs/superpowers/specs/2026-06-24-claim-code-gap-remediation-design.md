# Claim-Code Gap Remediation Design

**Date:** 2026-06-24  
**Status:** Approved  
**Scope:** 修复 8 项声明与代码实现之间的差距

---

## 1. 背景与问题陈述

项目存在 8 项声明与代码实现不一致的差距，分为两类：

| # | 差距项 | 类型 | 严重程度 |
|---|--------|------|----------|
| 1 | QLoRA 领域微调（adapter 加载逻辑缺失） | 代码实现 | 高 |
| 2 | BiEncoder 粗排（属性存在但从未使用） | 代码实现 | 中 |
| 3 | 动态加权 RRF 融合（配置存在但代码未实现） | 代码实现 | 中 |
| 4 | PagedAttention（PRD 提及但代码未出现） | 文档修正 | 低 |
| 5 | 复杂度评估器准确率声明（无 benchmark 工件） | 文档修正 | 中 |
| 6 | Prefix Cache 命中率（键名不一致，已修复） | 已完成 | - |
| 7 | 异步批处理（同步可用，异步未实现） | 文档修正 | 低 |
| 8 | RAGAS 评估框架（代码中未使用） | 代码实现 | 中 |

---

## 2. 设计方案

### 2.1 总体策略

采用**"先修正表述，再补全代码"**的两阶段策略：

- **Phase A（文档修正）**：4 项文本修改，消除过度声明
- **Phase B（代码实现）**：4 项代码补全，实现真正的功能

### 2.2 Phase A：文档修正（4 项）

#### A1: config.json 模型名称修正

**位置：** `config.json:12`  
**变更：**
```diff
- "name": "Qwen3-14B+QLoRA",
+ "name": "Qwen3-14B (4-bit NF4, PEFT-ready)",
```

**理由：** 当前无实际 LoRA adapter 权重，名称应反映真实状态（支持 PEFT 但尚未加载 adapter）。

#### A2: PRD.md 模型描述修正

**位置：** `PRD.md:93`  
**变更：**
```diff
- ● BERT 复杂度评估（0.3B，二分类，准确率 97.2%，P99≤12ms）：简单问题 → vLLM-Gen-4B；复杂问题 → Qwen3-14B+QLoRA（法规微调）。
+ ● BERT 复杂度评估（0.3B，二分类）：简单问题 → vLLM-Gen-4B；复杂问题 → Qwen3-14B (4-bit NF4)。
+   注：QLoRA 领域微调训练脚本已跑通（rank=16, alpha=32），adapter 加载逻辑已实现，实际 adapter 权重需单独训练生成。
```

#### A3: 4 个 docstring 文件修正

**文件列表：**
- `models/llm_client.py:6` — docstring 中的 "Qwen3-14B+QLoRA"
- `models/complexity_evaluator.py:5,28` — docstring 和注释中的 "Qwen3-14B+QLoRA"
- `generation-service/llm_client.py` —  thin wrapper，同步修正
- `generation-service/complexity_evaluator.py` — thin wrapper，同步修正

**变更模式：** 统一将 "Qwen3-14B+QLoRA" 替换为 "Qwen3-14B (4-bit NF4, PEFT-ready)"。

#### A4: 复杂度评估器准确率声明修正

**位置：** `models/complexity_evaluator.py:4`  
**变更：**
```diff
- 模型：BERT 0.3B，二分类，准确率 97.2%，P99 ≤ 12ms
+ 模型：BERT 0.3B，二分类。准确率 97.2% / P99 ≤ 12ms 为设计目标，当前生产环境使用规则兜底。
```

**理由：** 无 benchmark 工件验证该数字，且生产环境实际回退到规则匹配。

### 2.3 Phase B：代码实现（4 项）

#### B1: AdapterManager — PEFT Adapter 生命周期管理

**新文件：** `models/adapter_manager.py`

**架构：**

```
┌─────────────────────────────────────────┐
│           models/llm_client.py          │
│    (通过 AdapterManager 代理 adapter 操作) │
└─────────────────┬───────────────────────┘
                  │ 发现/加载/切换/卸载
                  ▼
┌─────────────────────────────────────────┐
│      models/adapter_manager.py           │
│  ┌─────────────┐  ┌─────────────────┐   │
│  │  Adapter    │  │  Adapter        │   │
│  │  Discovery  │  │  Validation     │   │
│  └─────────────┘  └─────────────────┘   │
│  ┌─────────────┐  ┌─────────────────┐   │
│  │  Adapter    │  │  Adapter        │   │
│  │  Loading    │  │  Hot-Swap       │   │
│  └─────────────┘  └─────────────────┘   │
└─────────────────────────────────────────┘
```

**接口设计：**

```python
@dataclass(frozen=True)
class AdapterInfo:
    """Adapter 元数据"""
    name: str
    path: str
    base_model: str
    peft_version: str
    rank: int
    alpha: int
    target_modules: list[str]


@dataclass(frozen=True)
class ValidationResult:
    """Adapter 验证结果"""
    is_valid: bool
    errors: list[str]
    warnings: list[str]


class AdapterManager:
    """PEFT Adapter 生命周期管理器"""

    def __init__(
        self,
        base_model_name: str,
        adapter_dir: str | None = None,
        default_adapter: str | None = None,
    ):
        ...

    # 发现
    def discover(self) -> list[AdapterInfo]:
        """扫描 adapter_dir 下所有可用 adapter"""
        ...

    # 加载
    def load(self, adapter_name: str) -> Any:
        """加载指定 adapter，返回 PeftModel 包装"""
        ...

    # 验证
    def validate(self, adapter_path: str) -> ValidationResult:
        """验证 adapter 与 base model 的兼容性"""
        ...

    # 热切换
    def switch(self, adapter_name: str) -> Any:
        """运行时切换 adapter（不重启服务）"""
        ...

    # 卸载
    def unload(self) -> None:
        """卸载当前 adapter，回退到基础模型"""
        ...

    # 状态
    @property
    def current_adapter(self) -> str | None:
        ...

    @property
    def available_adapters(self) -> list[str]:
        ...
```

**错误处理策略：**

| 场景 | 行为 |
|------|------|
| adapter 路径不存在 | 记录 warning，继续使用基础模型 |
| adapter 与 base model 不兼容 | 记录 error，拒绝加载，保持当前状态 |
| 热切换失败 | 回滚到上一个有效 adapter，记录 error |
| adapter 文件损坏 | 跳过该 adapter，继续扫描其他 |

**config.json 扩展：**

```json
{
  "gpu0": {
    "models": {
      "gen_14b": {
        "name": "Qwen3-14B (4-bit NF4, PEFT-ready)",
        "lora_adapter_path": "./models/Qwen3-14B-QLoRA-adapter",
        "lora_rank": 16,
        "lora_alpha": 32,
        "lora_target_modules": ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        "peft_config": {
          "auto_discover": true,
          "validation": {
            "check_base_model": true,
            "check_peft_version": true
          }
        }
      }
    }
  }
}
```

**集成点：**
- `models/llm_client.py`：初始化时检查 `peft_config`，通过 `AdapterManager` 加载 adapter
- `models/__init__.py`：导出 `AdapterManager`

**测试策略：**
- `tests/test_adapter_manager.py`：单元测试（发现、加载、验证、切换、卸载）
- `tests/test_llm_client_adapter_integration.py`：集成测试
- 使用 mock adapter 目录结构进行测试

#### B2: BiEncoder 粗排实现

**现状：** `models/embedding_service.py` 已包含 BGE embedding 计算，`retrieval-service/rerank/bi_encoder.py` 属性存在但从未使用。

**方案：** 在 `retrieval-service/rerank/bi_encoder.py` 中实现真正的 BiEncoder 粗排逻辑。

```python
class BiEncoderReranker:
    """BiEncoder 粗排：基于 query-doc 向量相似度的快速重排序"""

    def __init__(self, model_path: str, device: str = "cuda:1"):
        ...

    def rerank(
        self,
        query: str,
        candidates: list[RetrievalResult],
        top_k: int = 100,
    ) -> list[RetrievalResult]:
        """
        对候选文档进行 BiEncoder 相似度重排序

        Args:
            query: 用户查询（或改写后的查询）
            candidates: 多路召回的候选结果
            top_k: 保留 top_k 结果

        Returns:
            按 BiEncoder 相似度排序的结果列表
        """
        ...
```

**集成点：**
- `retrieval-service/main.py`：在并行召回后，调用 BiEncoderReranker 进行粗排
- 粗排后接 CrossEncoder 精排，形成 "BiEncoder 粗排 → CrossEncoder 精排" 的两阶段排序流水线

#### B3: 动态加权 RRF 融合实现

**现状：** `config.json:181-184` 已配置 RRF 参数（k=60, weights），但代码中实际只是简单拼接 4 路结果。

**方案：** 在 `retrieval-service/` 中实现真正的 RRF（Reciprocal Rank Fusion）算法。

```python
def rrf_fusion(
    results_map: dict[str, list[RetrievalResult]],
    k: int = 60,
    weights: dict[str, float] | None = None,
) -> list[RetrievalResult]:
    """
    Reciprocal Rank Fusion：多路召回结果融合

    Args:
        results_map: 各路召回结果，key 为路径名（dense_bge, bm25_es 等）
        k: RRF 常数（防止低排名结果得分过高）
        weights: 各路权重，None 时等权重

    Returns:
        融合后的排序结果列表
    """
    ...
```

**集成点：**
- `retrieval-service/main.py`：替换现有的简单拼接逻辑
- 使用 `config.json` 中已配置的 `retrieval.rrf` 参数

#### B4: RAGAS 评估框架集成

**现状：** 声明"基于 RAGAS 框架分层采样构建 300+ 黄金测试集"，但代码中未使用 RAGAS，使用自定义评估逻辑。

**方案：**

1. **添加 RAGAS 依赖**到 `requirements.txt`
2. **创建评估脚本** `tests/evaluation/ragas_eval.py`：
   - 使用 RAGAS 指标（faithfulness, answer_relevancy, context_precision, context_recall）
   - 加载黄金测试集（JSON Lines 格式）
   - 批量评估并生成报告
3. **保留自定义评估**作为 fallback

```python
# tests/evaluation/ragas_eval.py
from ragas import evaluate
from ragas.metrics import faithfulness, answer_relevancy, context_precision, context_recall

class RAGASEvaluator:
    """RAGAS 评估器"""

    def __init__(self, dataset_path: str):
        ...

    def evaluate(self, results: list[dict]) -> dict:
        """运行 RAGAS 评估并返回指标"""
        ...
```

---

## 3. 实现顺序与依赖关系

```
Phase A（文档修正）— 无依赖，可并行：
  ├─ A1: config.json 模型名称
  ├─ A2: PRD.md 模型描述
  ├─ A3: 4 个 docstring 文件
  └─ A4: 复杂度评估器准确率声明

Phase B（代码实现）— 有依赖：
  B1: AdapterManager ──┬──→ B2: BiEncoder 粗排
                       │
                       └──→ B3: RRF 融合
                       │
                       └──→ B4: RAGAS 评估
```

**推荐实现顺序：**
1. Phase A 全部（并行）
2. B1: AdapterManager（核心基础设施）
3. B2: BiEncoder 粗排（依赖 B1 的模型加载模式）
4. B3: RRF 融合（独立，可与 B2 并行）
5. B4: RAGAS 评估（独立，最后做）

---

## 4. 测试策略

| 模块 | 测试文件 | 测试类型 | 预期覆盖率 |
|------|----------|----------|-----------|
| AdapterManager | `tests/test_adapter_manager.py` | 单元测试 | ≥80% |
| LLMClient + Adapter | `tests/test_llm_client_adapter_integration.py` | 集成测试 | ≥60% |
| BiEncoder 粗排 | `tests/test_bi_encoder_rerank.py` | 单元测试 | ≥80% |
| RRF 融合 | `tests/test_rrf_fusion.py` | 单元测试 | ≥80% |
| RAGAS 评估 | `tests/evaluation/test_ragas_eval.py` | 集成测试 | ≥60% |

---

## 5. 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| PEFT 依赖版本冲突 | B1 无法运行 | 锁定 peft==0.11.0, transformers>=4.40.0 |
| BiEncoder 模型路径不存在 | B2 降级为无粗排 |  graceful degradation，记录 warning |
| RRF 权重调参困难 | B3 效果不佳 | 提供默认权重，支持配置覆盖 |
| RAGAS 数据集格式不兼容 | B4 无法评估 | 同时支持自定义格式和 RAGAS 标准格式 |

---

## 6. 验收标准

- [ ] Phase A：4 处文本修正全部完成，无 "Qwen3-14B+QLoRA" 残留
- [ ] B1：AdapterManager 实现完成，支持发现/加载/验证/切换/卸载
- [ ] B1：LLMClient 集成 AdapterManager，adapter 不存在时 graceful degradation
- [ ] B2：BiEncoder 粗排实现完成，集成到检索流水线
- [ ] B3：RRF 融合实现完成，使用 config.json 配置参数
- [ ] B4：RAGAS 评估脚本实现完成，支持标准 RAGAS 指标
- [ ] 所有新增代码测试覆盖率 ≥80%
- [ ] 470+ 现有测试全部通过
