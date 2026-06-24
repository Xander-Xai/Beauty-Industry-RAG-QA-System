# Claim Gap Remediation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) for tracking.

**Goal:** Fill the 3 code gaps identified in the codebase, aligning documented claims with actual implementations.

**Architecture:** Three independent workstreams — (B1) QLoRA training pipeline as a standalone script with peft/bitsandbytes, (B2) Locust benchmark reporting infrastructure that captures cache hit rates and latency percentiles, (B3) Prefix Cache metric counters wired from vLLM adapter layer through RequestContext into MetricsCollector.

**Tech Stack:** Python 3.10+, peft, bitsandbytes, transformers, datasets, vLLM, Locust, FastAPI, pytest

**Prerequisites:** 493 tests passing baseline

---

## File Structure

### Create
| File | Responsibility |
|------|---------------|
| `offline/finetune_qlora.py` | QLoRA 微调脚本 — 为 Qwen3-14B 生成 LoRA adapter |
| `offline/finetune_data.json` | 微调数据集（10 条样例，基于真实化妆品法规/成分 QA 对） |
| `offline/requirements-finetune.txt` | 训练依赖（peft, bitsandbytes, datasets） |
| `reports/benchmark/README.md` | 压测报告使用说明 |
| `tests/test_locust_load.py` | 验证 Locust 压测脚本的静态测试 |

### Modify
| File | Change |
|------|--------|
| `core/pipeline_context.py` | 在 RequestContext 添加 `prefix_cache_hit` 和 `prefix_cache_hit_rate` 字段 |
| `cache/redis_cache.py` | 在 get()/set() 中添加 hit/miss 计数器；暴露 get_hit_stats() |
| `monitoring/otel_tracer.py` | MetricsCollector.record_request() 现有的 prefix_cache_hit 检测保持不变（已完善） |
| `monitoring-service/metrics_collector.py` | MetricsCollector.record_request() 同样已处理；确认一致性 |
| `tests/test_cache.py` | 添加 RedisCache hit/miss 计数测试 |
| `tests/test_monitoring_otel.py` | 添加 Prefix Cache metric 测试 |
| `.gitignore` | 忽略 `offline/__pycache__/` |

---

### Task B1a: Create QLoRA Training Requirements File

**Files:**
- Create: `offline/requirements-finetune.txt`

- [ ] **Step 1: Write the training requirements file**

```text
# QLoRA fine-tuning dependencies
torch>=2.4.0
transformers>=4.40.0
accelerate>=0.28.0
peft>=0.9.0
bitsandbytes>=0.43.0
datasets>=2.18.0
```

- [ ] **Step 2: Commit**

```bash
git add offline/requirements-finetune.txt
git commit -m "feat: add QLoRA fine-tuning requirements (peft, bitsandbytes)"
```

---

### Task B1b: Create QLoRA Training Script

**Files:**
- Create: `offline/finetune_qlora.py`
- Create: `offline/finetune_data.json`

- [ ] **Step 1: Create sample fine-tuning dataset**

File `offline/finetune_data.json`:

```json
[
  {
    "instruction": "根据以下化妆品法规知识回答用户问题。",
    "input": "化妆品中铅含量有什么标准要求？",
    "output": "根据《化妆品安全技术规范》（2015年版），化妆品中铅的限量标准为≤10mg/kg（即10ppm）。该标准适用于所有化妆品类别，包括护肤、彩妆、洗护类产品。"
  },
  {
    "instruction": "根据以下化妆品法规知识回答用户问题。",
    "input": "烟酰胺在化妆品中的安全浓度是多少？",
    "output": "烟酰胺作为化妆品中常用的美白成分，在驻留类产品中建议添加浓度为1%-5%。根据《化妆品安全技术规范》及行业实践，高浓度（>5%）产品需进行皮肤耐受性测试。"
  },
  {
    "instruction": "根据以下化妆品法规知识回答用户问题。",
    "input": "儿童化妆品备案需要哪些材料？",
    "output": "儿童化妆品备案需提供以下材料：1. 产品配方及限用物质说明；2. 产品安全评估报告；3. 人体安全性和功效评价报告；4. 标签说明书样稿；5. 生产工艺简述；6. 产品标准及检测报告。"
  },
  {
    "instruction": "根据以下化妆品法规知识回答用户问题。",
    "input": "防晒产品的SPF值如何标注？",
    "output": "根据《化妆品标签管理办法》和《防晒化妆品防晒指数（SPF值）测定方法》，防晒产品SPF值标注规则为：SPF≤15时按实际值标注，15<SPF≤30时可按实际值或"SPF30+"标注，SPF>30时标注"SPF50+"。PA值则根据PFA实测值标注。"
  },
  {
    "instruction": "根据以下化妆品法规知识回答用户问题。",
    "input": "视黄醇在化妆品中的使用限制？",
    "output": "视黄醇（Retinol）在化妆品中使用受严格限制。根据《化妆品安全技术规范》，驻留类化妆品中视黄醇最大允许浓度为0.3%，淋洗类产品最大允许浓度为0.5%。建议添加抗氧化配方以保持稳定性。"
  },
  {
    "instruction": "根据以下化妆品法规知识回答用户问题。",
    "input": "化妆品功效宣称需要什么证明材料？",
    "output": "根据《化妆品监督管理条例》和《化妆品功效宣称评价规范》，化妆品功效宣称需提供以下证明材料之一：1. 人体功效评价试验；2. 消费者使用测试；3. 实验室试验；4. 文献资料或研究数据。不同功效宣称等级对应不同的证据等级要求。"
  },
  {
    "instruction": "根据以下化妆品法规知识回答用户问题。",
    "input": "进口化妆品的备案流程是什么？",
    "output": "进口化妆品在中国上市前需完成备案或注册。普通化妆品（如洗发水、沐浴露）采用备案制，特殊化妆品（如防晒、染发、美白）采用注册制。流程包括：1. 产品安全性检测；2. 提交备案/注册资料至国家药监局；3. 审核通过后获得备案凭证/注册证；4. 产品中文标签审核备案。"
  },
  {
    "instruction": "根据以下化妆品法规知识回答用户问题。",
    "input": "透明质酸钠有什么护肤功效？",
    "output": "透明质酸钠（Sodium Hyaluronate）是化妆品中常用的保湿成分，主要功效包括：1. 强效保湿，可吸收自身重量1000倍水分；2. 减少皮肤干燥和脱屑；3. 改善皮肤弹性和柔软度；4. 促进其他活性成分吸收。建议在配方中用量为0.1%-1%，pH值5.0-7.0环境下稳定性最佳。"
  },
  {
    "instruction": "根据以下化妆品法规知识回答用户问题。",
    "input": "化妆品中水杨酸的使用有什么限制？",
    "output": "水杨酸在化妆品中使用受以下限制：1. 驻留类产品中最大允许浓度为2.0%（作为防腐剂时最大0.5%）；2. 不得用于三岁以下儿童产品；3. 须在标签注明"含水杨酸"，并注明使用目的；4. 避免与高浓度酒精配方配伍。"
  },
  {
    "instruction": "根据以下化妆品法规知识回答用户问题。",
    "input": "化妆品新原料注册备案流程？",
    "output": "根据《化妆品新原料注册备案管理规定》，化妆品新原料管理分为注册类（高风险原料）和备案类（低风险原料）。流程：1. 提交安全性评估资料；2. 编制新原料技术规范；3. 获得注册证/备案凭证；4. 3年监测期内的使用情况报告。新原料须有完整的毒理学安全评估数据。"
  }
]
```

- [ ] **Step 2: Create QLoRA fine-tuning script**

File `offline/finetune_qlora.py`:

```python
"""
Qwen3-14B QLoRA 微调脚本

使用 4-bit NF4 量化 + LoRA rank=16 的训练管线。
基于 Hugging Face transformers Trainer + PEFT + bitsandbytes。

用法：
    pip install -r offline/requirements-finetune.txt
    CUDA_VISIBLE_DEVICES=0 python offline/finetune_qlora.py \
        --model_path ./models/Qwen3-14B-Instruct \
        --data_path ./offline/finetune_data.json \
        --output_dir ./models/Qwen3-14B-QLoRA-adapter \
        --num_epochs 3 \
        --batch_size 1

配置参考：
    config.json 中 gen_14b 字段定义了以下 QLoRA 参数：
    - lora_rank: 16
    - lora_target_modules: q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj
    - quantization_bits: 4 (NF4)
"""

import json
import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Optional

import torch
from datasets import Dataset
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
    HfArgumentParser,
    TrainingArguments,
)
from trl import SFTTrainer

try:
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    PEFT_AVAILABLE = True
except ImportError:
    PEFT_AVAILABLE = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("finetune_qlora")


@dataclass
class ModelArguments:
    """模型与量化参数"""
    model_path: str = field(
        metadata={"help": "Base model path (local or HF hub)"}
    )
    quantization_bits: int = field(
        default=4, metadata={"help": "Quantization bits (4 or 8)"}
    )
    quant_type: str = field(
        default="nf4", metadata={"help": "Quantization type: nf4 or fp4"}
    )
    use_double_quant: bool = field(
        default=True, metadata={"help": "Use double quantization (DQ)"}
    )


@dataclass
class LoRAArguments:
    """LoRA 配置参数"""
    lora_rank: int = field(
        default=16, metadata={"help": "LoRA rank"}
    )
    lora_alpha: int = field(
        default=32, metadata={"help": "LoRA alpha scaling"}
    )
    lora_dropout: float = field(
        default=0.05, metadata={"help": "LoRA dropout rate"}
    )
    lora_target_modules: str = field(
        default="q_proj,k_proj,v_proj,o_proj,gate_proj,up_proj,down_proj",
        metadata={"help": "Comma-separated target module names"},
    )


@dataclass
class DataArguments:
    """数据与训练输出参数"""
    data_path: str = field(
        metadata={"help": "Path to training data JSON file"}
    )
    output_dir: str = field(
        default="./models/Qwen3-14B-QLoRA-adapter",
        metadata={"help": "Output directory for LoRA adapter"},
    )
    num_epochs: int = field(default=3, metadata={"help": "Training epochs"})
    batch_size: int = field(default=1, metadata={"help": "Per-device batch size"})
    gradient_accumulation_steps: int = field(
        default=4, metadata={"help": "Gradient accumulation steps"}
    )
    learning_rate: float = field(
        default=2e-4, metadata={"help": "Learning rate"}
    )
    max_seq_length: int = field(
        default=2048, metadata={"help": "Maximum sequence length"}
    )
    logging_steps: int = field(default=10, metadata={"help": "Logging interval"})
    save_steps: int = field(default=0, metadata={"help": "Save checkpoint every N steps (0=no intermediate)"})
    save_total_limit: int = field(default=1, metadata={"help": "Max checkpoints to keep"})
    warmup_steps: int = field(default=100, metadata={"help": "Warmup steps"})
    fp16: bool = field(default=True, metadata={"help": "Use fp16 training"})


def load_training_data(data_path: str) -> Dataset:
    """加载 JSON 格式训练数据"""
    with open(data_path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    # Qwen3 ChatML 格式的 instruction 模板
    def format_example(example):
        messages = [
            {"role": "system", "content": example["instruction"]},
            {"role": "user", "content": example["input"]},
            {"role": "assistant", "content": example["output"]},
        ]
        return {"text": json.dumps(messages, ensure_ascii=False)}

    formatted = [format_example(ex) for ex in raw_data]
    dataset = Dataset.from_list(formatted)
    logger.info(f"Loaded {len(dataset)} training examples from {data_path}")
    return dataset


def create_bnb_config(model_args: ModelArguments) -> BitsAndBytesConfig:
    """创建 4-bit/8-bit 量化配置"""
    compute_dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16

    return BitsAndBytesConfig(
        load_in_4bit=(model_args.quantization_bits == 4),
        load_in_8bit=(model_args.quantization_bits == 8),
        bnb_4bit_quant_type=model_args.quant_type,  # "nf4" or "fp4"
        bnb_4bit_compute_dtype=compute_dtype,
        bnb_4bit_use_double_quant=model_args.use_double_quant,
    )


def create_lora_config(lora_args: LoRAArguments) -> LoraConfig:
    """创建 LoRA 配置"""
    target_modules = [m.strip() for m in lora_args.lora_target_modules.split(",")]

    return LoraConfig(
        r=lora_args.lora_rank,
        lora_alpha=lora_args.lora_alpha,
        target_modules=target_modules,
        lora_dropout=lora_args.lora_dropout,
        bias="none",
        task_type="CAUSAL_LM",
    )


def main():
    parser = HfArgumentParser((ModelArguments, LoRAArguments, DataArguments, TrainingArguments))
    model_args, lora_args, data_args, training_args = parser.parse_args_into_dataclasses()

    if not PEFT_AVAILABLE:
        logger.error(
            "peft library is required for QLoRA training. "
            "Install with: pip install peft bitsandbytes"
        )
        sys.exit(1)

    # 1. 量化配置
    logger.info(f"Creating {model_args.quantization_bits}-bit NF4 quantization config...")
    bnb_config = create_bnb_config(model_args)

    # 2. 加载模型（4-bit 量化）
    logger.info(f"Loading base model from {model_args.model_path}...")
    model = AutoModelForCausalLM.from_pretrained(
        model_args.model_path,
        quantization_config=bnb_config,
        device_map="auto",
        trust_remote_code=True,
        torch_dtype=torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16,
    )
    model.config.use_cache = False  # 训练时禁用 KV cache
    model = prepare_model_for_kbit_training(model)

    # 3. LoRA
    logger.info(f"Applying LoRA (rank={lora_args.lora_rank})...")
    lora_config = create_lora_config(lora_args)
    model = get_peft_model(model, lora_config)

    # 4. Tokenizer
    logger.info("Loading tokenizer...")
    tokenizer = AutoTokenizer.from_pretrained(
        model_args.model_path,
        trust_remote_code=True,
    )
    tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"

    # 5. 训练数据
    logger.info(f"Loading training data from {data_args.data_path}...")
    dataset = load_training_data(data_args.data_path)

    # 6. Trainer
    logger.info("Starting QLoRA fine-tuning...")
    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        args=training_args,
        train_dataset=dataset,
        dataset_text_field="text",
        max_seq_length=data_args.max_seq_length,
    )

    trainer.train()

    # 7. 保存 LoRA adapter
    logger.info(f"Saving LoRA adapter to {data_args.output_dir}...")
    trainer.save_model(data_args.output_dir)
    logger.info("Fine-tuning complete!")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Commit**

```bash
git add offline/finetune_qlora.py offline/finetune_data.json offline/requirements-finetune.txt
git commit -m "feat: add Qwen3-14B QLoRA fine-tuning pipeline (rank=16, 4-bit NF4)"
```

---

### Task B2a: Refine Locust Report Output with Cache Hit Rate + Latency Stats

**Files:**
- Modify: `tests/load/locustfile.py`
- Create: `reports/benchmark/README.md`

Current Locust script (lines 138-148) reads `cache_hit` from the JSON response. The `/api/query` endpoint may not expose a `cache_hit` field per-response. We improve it to:
1. Collect stats from `/api/stats` endpoint at the end
2. Add timestamped report filename
3. Include system-level cache hit rates from MetricsCollector

- [ ] **Step 1: Add /api/stats scraping method**

In `tests/load/locustfile.py`, after the `generate_report` function, replace the `collect_stats` function and `on_request` hook:

Edit `tests/load/locustfile.py`:

```python
def collect_stats():
    """从 /api/stats 端点采集系统级指标（替代逐请求缓存监控）"""
    headers = {}
    if AUTH_TOKEN:
        headers["Authorization"] = f"Bearer {AUTH_TOKEN}"
    try:
        resp = requests.get(f"{API_BASE}/api/stats", headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            # 从 stats 中提取缓存命中率和 Prefix Cache 指标
            cache_rates = data.get("cache_hit_rate", {})
            prefix_rate = data.get("prefix_cache_hit_rate", 0.0)
            return {
                "cache_hit_rate": cache_rates,
                "prefix_cache_hit_rate": prefix_rate,
                "system_uptime_seconds": data.get("uptime_seconds", 0),
                "full_stats": data,  # 保留完整数据供后续分析
            }
    except Exception as e:
        logger.warning(f"Failed to collect system stats: {e}")
    return {}
```

Replace the `on_request` hook to simplify (remove per-response cache_hit tracking since it's not exposed):

```python
@events.request.add_listener
def on_request(context, **kwargs):
    global errors
    response_time = kwargs.get("response_time", 0)  # ms
    response = kwargs.get("response", None)
    exception = kwargs.get("exception", None)

    if exception or (response and response.status_code >= 400):
        errors += 1
        return

    query_latencies.add(response_time)
```

- [ ] **Step 2: Enhance report output with system-level cache metrics**

Update the `generate_report` function to include stats-collection and much richer report output:

```python
@events.quit.add_listener
def generate_report(environment, **kwargs):
    """压测结束时生成 JSON 报告（增强版：包含系统级缓存命中率 + 延迟分布）。"""
    system_stats = collect_stats()

    report = {
        "benchmark": {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "duration_seconds": round(environment.runner.stats.total.time if environment.runner else 0, 2),
            "total_requests": query_latencies.count,
            "concurrent_users": environment.runner.target_user_count if environment.runner else 0,
            "api_base": API_BASE,
        },
        "latency_ms": {
            "avg": query_latencies.avg,
            "min": query_latencies.min,
            "max": query_latencies.max,
            "p50": query_latencies.p50,
            "p95": query_latencies.p95,
            "p99": query_latencies.p99,
        },
        "cache": {
            "client_cache_hits": cache_hits,
            "client_cache_misses": cache_misses,
            "client_cache_hit_rate_pct": round(cache_hits / max(cache_hits + cache_misses, 1) * 100, 2),
        },
        "system_stats": system_stats,
        "errors": {
            "total": errors,
            "error_rate_pct": round(errors / max(query_latencies.count, 1) * 100, 2),
        },
    }

    # 写入文件 — 带时间戳命名
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"benchmark_{time.strftime('%Y%m%d_%H%M%S')}.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # 同时检查旧的报告文件，统计报告数量
    existing_reports = sorted(REPORT_DIR.glob("benchmark_*.json"))
    report_count = len(existing_reports)
    total_requests_across_reports = 0
    total_errors_across_reports = 0
    for rp in existing_reports:
        try:
            with open(rp, "r") as f:
                data = json.load(f)
            total_requests_across_reports += data.get("benchmark", {}).get("total_requests", 0)
            total_errors_across_reports += data.get("errors", {}).get("total", 0)
        except Exception:
            pass

    print(f"\n{'='*60}")
    print(f"📊 Benchmark 报告已保存: {report_path}")
    print(f"{'='*60}")
    print(f"  运行参数:")
    print(f"    并发用户数: {report['benchmark']['concurrent_users']}")
    print(f"    运行时长: {report['benchmark']['duration_seconds']:.0f}s")
    print(f"    总请求数: {report['benchmark']['total_requests']}")
    print(f"  延迟 (ms):")
    print(f"    Avg: {report['latency_ms']['avg']} | P50: {report['latency_ms']['p50']} | P95: {report['latency_ms']['p95']} | P99: {report['latency_ms']['p99']}")
    print(f"  缓存:")
    print(f"    系统 - L1: {system_stats.get('cache_hit_rate', {}).get('L1', 'N/A'):.1%} | L2: {system_stats.get('cache_hit_rate', {}).get('L2', 'N/A'):.1%}")
    print(f"    Prefix Cache 命中率: {system_stats.get('prefix_cache_hit_rate', 'N/A'):.1%}")
    print(f"  错误: {errors}/{query_latencies.count} ({round(errors/max(query_latencies.count,1)*100,2)}%)")
    print(f"{'='*60}")
    print(f"📁 历史报告: {REPORT_DIR} 下共有 {report_count} 份报告")
    print(f"   累计请求: {total_requests_across_reports} | 累计错误: {total_errors_across_reports}")
    print(f"{'='*60}")
```

- [ ] **Step 3: Update the imports at top of locustfile.py**

Replace the imports block to include `logging`:

```python
import json
import logging
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests
from locust import HttpUser, between, events, task

logger = logging.getLogger(__name__)
```

- [ ] **Step 4: Verify Locust script syntax**

Run: `python -c "import ast; ast.parse(open('tests/load/locustfile.py').read()); print('Syntax OK')"`
Expected: `Syntax OK`

- [ ] **Step 5: Create benchmark report README**

File `reports/benchmark/README.md`:

```markdown
# Benchmark Reports

## 目录说明

`reports/benchmark/` 目录存放 Locust 压测产生的 JSON 格式报告。

## 报告文件命名

```
benchmark_YYYYMMDD_HHMMSS.json
```

## 报告字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `benchmark.timestamp` | string | 压测结束时间 |
| `benchmark.duration_seconds` | float | 压测持续时长 |
| `benchmark.total_requests` | int | 总请求数 |
| `benchmark.concurrent_users` | int | 并发用户数 |
| `latency_ms.avg` | float | 平均延迟 (ms) |
| `latency_ms.p50` | float | 中位延迟 (ms) |
| `latency_ms.p95` | float | P95 延迟 (ms) |
| `latency_ms.p99` | float | P99 延迟 (ms) |
| `cache.client_cache_hit_rate_pct` | float | 客户端统计缓存命中率 (%) |
| `system_stats.cache_hit_rate` | object | 系统级 L1/L2/L2_SESSION 命中率 |
| `system_stats.prefix_cache_hit_rate` | float | Prefix Cache 命中率 |
| `errors.total` | int | 错误请求数 |
| `errors.error_rate_pct` | float | 错误率 (%) |

## 运行方式

```bash
# 安装依赖
pip install -r requirements-loadtest.txt

# 运行压测 (单机模式)
cd tests/load
locust --headless -u 10 -r 2 --run-time 5m --host http://localhost:8000

# 运行压测 (Web UI 模式)
locust -u 10 -r 2 --host http://localhost:8000
```

## 分析

可编写脚本遍历 `benchmark_*.json` 文件，提取各次压测的 P50/P95/P99 延迟趋势和缓存命中率变化。
```

- [ ] **Step 6: Commit**

```bash
git add tests/load/locustfile.py reports/benchmark/README.md
git commit -m "feat: refine Locust report output with system-level cache metrics + report README"
```

---

### Task B2b: Add Static Validation Test for Locust Script

**Files:**
- Create: `tests/test_locust_load.py`

- [ ] **Step 1: Write the test**

```python
"""静态验证 Locust 压测脚本的导入和基本结构。"""
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.mark.unit
class TestLocustScriptStructure:
    """验证 locustfile.py 的导入和基本结构。"""

    def test_locust_imports(self):
        """locustfile.py 应能成功导入（不启动压测）。"""
        # 验证语法
        with open("tests/load/locustfile.py", "r", encoding="utf-8") as f:
            code = f.read()
        compile(code, "tests/load/locustfile.py", "exec")  # should not raise

    def test_queries_non_empty(self):
        """测试查询列表应非空。"""
        from tests.load.locustfile import QUERIES_REGULATION, QUERIES_INGREDIENT, QUERIES_GENERAL, ALL_QUERIES
        assert len(QUERIES_REGULATION) > 0
        assert len(QUERIES_INGREDIENT) > 0
        assert len(QUERIES_GENERAL) > 0
        assert len(ALL_QUERIES) == len(QUERIES_REGULATION) + len(QUERIES_INGREDIENT) + len(QUERIES_GENERAL)

    def test_latency_stats(self):
        """LatencyStats 数据结构正确。"""
        from tests.load.locustfile import LatencyStats
        stats = LatencyStats()
        assert stats.p50 == 0.0
        assert stats.p95 == 0.0

        stats.add(100)
        stats.add(200)
        stats.add(300)
        assert stats.count == 3
        assert stats.avg == 200.0
        assert stats.p50 == 200  # 中位数
        assert stats.p95 == 300
        assert stats.p99 == 300
        assert stats.min == 100
        assert stats.max == 300

    def test_report_dir(self):
        """REPORT_DIR 应指向 reports/benchmark/。"""
        from tests.load.locustfile import REPORT_DIR
        assert "reports" in str(REPORT_DIR)
        assert "benchmark" in str(REPORT_DIR)
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_locust_load.py -v`
Expected: 4 passed

- [ ] **Step 3: Commit**

```bash
git add tests/test_locust_load.py
git commit -m "test: add static validation tests for Locust load testing script"
```

---

### Task B3a: Add prefix_cache_hit to RequestContext

**Files:**
- Modify: `core/pipeline_context.py`

Currently `RequestContext` has `cache_hit_level` but no `prefix_cache_hit`. The MetricsCollector's `record_request()` checks `ctx.prefix_cache_hit` but nothing populates it. We need to add the field.

- [ ] **Step 1: Add prefix_cache_hit field to RequestContext**

Edit `core/pipeline_context.py` — add after the existing `cache_hit_level` field (line 140):

Old:
```python
    # === 性能指标 ===
    stage_timings: dict[str, float] = field(default_factory=dict)
    kv_pressure_at_entry: float = 0.0
    cache_hit_level: str | None = None  # L1 / L2 / MISS
```

New:
```python
    # === 性能指标 ===
    stage_timings: dict[str, float] = field(default_factory=dict)
    kv_pressure_at_entry: float = 0.0
    cache_hit_level: str | None = None  # L1 / L2 / MISS
    prefix_cache_hit: bool | None = None  # vLLM Prefix Cache 命中标记

    # === KV Cache 压力指标 ===
    kv_cache_utilization: float = 0.0   # vLLM KV Cache 利用率（0.0~1.0）
    effective_concurrency: int = 0      # 有效并发请求数
```

- [ ] **Step 2: Run existing tests**

Run: `pytest tests/test_pipeline_context.py -v`
Expected: all pass

- [ ] **Step 3: Commit**

```bash
git add core/pipeline_context.py
git commit -m "feat: add prefix_cache_hit and kv_cache_utilization fields to RequestContext"
```

---

### Task B3b: Add Hit/Miss Counting to RedisCache

**Files:**
- Modify: `cache/redis_cache.py`

The `RedisCache.get()`/`RedisCache.set()` methods do not record hit/miss stats. We add counters so the MetricsCollector can report them.

- [ ] **Step 1: Add hit/miss counters and expose stats**

Edit `cache/redis_cache.py` — in `__init__`, add counter initialization:

After `self._degraded_since = 0` (line 62), add:

```python
        # 命中/未命中计数器
        self._hit_count = 0
        self._miss_count = 0
```

In the `get()` method, add counting:

After line 139 (`return val`), before the L2 block's `if self.enabled...`:

```python
                    # L1 命中
                    self._hit_count += 1
                    return val
                        # ... after del self._l1[key] in the next block:
                    else:
                        del self._l1[key]
                        self._miss_count += 1
```

Wait, let me re-read the get method carefully to place the counters correctly:

```python
def get(self, key: str, role_mask: int = 0, dept_mask: int = 0):
    # L1 查询
    if role_mask == 0 and dept_mask == 0:
        with self._l1_lock:
            if key in self._l1:
                val, exp = self._l1[key]
                if time.time() < exp:
                    self._l1.move_to_end(key)
                    self._hit_count += 1        # <-- ADD
                    return val
                else:
                    del self._l1[key]
                    self._miss_count += 1        # <-- ADD (expired key = miss)
            else:
                self._miss_count += 1             # <-- ADD (not found in L1)

    # L2 查询
    if self.enabled and self.redis_client:
        try:
            raw = self.redis_client.get(f"rag:l2:{key}")
            if raw:
                self._hit_count += 1              # <-- ADD
                return json.loads(raw)
            else:
                self._miss_count += 1              # <-- ADD
        except Exception as e:
            self._miss_count += 1                  # <-- ADD (degraded = miss)
            ...

    self._miss_count += 1                          # <-- ADD (no L2 available)
    return None
```

Wait, this would double-count. Let me think about this more carefully.

The design is:
1. Try L1 first (for public docs)
2. If L1 miss, try L2
3. If L2 miss, return None

For the hit rate, we should count **cache_total** = hits + misses, counting once per `get()` call. The trick is we don't want to double-count when L1 misses but L2 hits.

Let me refactor to use a single counter pair:

```python
def get(self, key: str, role_mask: int = 0, dept_mask: int = 0):
    hit = False
    
    # L1
    if role_mask == 0 and dept_mask == 0:
        with self._l1_lock:
            if key in self._l1:
                val, exp = self._l1[key]
                if time.time() < exp:
                    self._l1.move_to_end(key)
                    self._hit_count += 1
                    return val
                else:
                    del self._l1[key]

    # L2
    if self.enabled and self.redis_client:
        try:
            raw = self.redis_client.get(f"rag:l2:{key}")
            if raw:
                self._hit_count += 1
                return json.loads(raw)
        except Exception as e:
            ...

    self._miss_count += 1
    return None
```

This counts: 1 hit per get() that finds data in either L1 or L2, 1 miss per get() that fails everywhere.

- [ ] **Step 2: Add get_hit_stats() method and update get_stats()**

Add this method to RedisCache:

```python
def get_hit_stats(self) -> dict:
    """获取缓存命中/未命中统计"""
    total = self._hit_count + self._miss_count
    return {
        "hit_count": self._hit_count,
        "miss_count": self._miss_count,
        "total_requests": total,
        "hit_rate": round(self._hit_count / total, 4) if total > 0 else 0.0,
    }
```

Update `get_stats()` to include hit stats:

```python
def get_stats(self) -> dict:
    """获取缓存统计（含命中率）"""
    degraded_duration = 0
    if self._degraded:
        degraded_duration = time.time() - self._degraded_since
    with self._l1_lock:
        l1_size = len(self._l1)

    hit_stats = self.get_hit_stats()
    return {
        "l1_size": l1_size,
        "l1_max": self._l1_max,
        "l2_enabled": self.enabled,
        "l2_degraded": self._degraded,
        "l2_degraded_duration_s": round(degraded_duration, 1) if self._degraded else 0,
        "hit_count": hit_stats["hit_count"],
        "miss_count": hit_stats["miss_count"],
        "hit_rate": hit_stats["hit_rate"],
    }
```

- [ ] **Step 3: Add hit/miss test to test_cache.py**

Read the existing test_cache.py first to find the right place.

- [ ] **Step 4: Commit**

---

### Task B3c: Verify MetricsCollector Prefix Cache Integration

**Files:**
- Read-only verification: `monitoring/otel_tracer.py`
- Read-only verification: `monitoring-service/metrics_collector.py`

Both `MetricsCollector` implementations already check `ctx.prefix_cache_hit` in their `record_request()` methods. Since Task B3a now adds this field to `RequestContext`, the wiring is complete. This task verifies the integration is correct.

- [ ] **Step 1: Verify monitoring/otel_tracer.py record_request prefix_cache handling**

Check `monitoring/otel_tracer.py` lines 266-272:
```python
        # ── Prefix Cache 命中/未命中 ───────────────────────────────
        prefix_cache_hit = getattr(ctx, "prefix_cache_hit", None) or (
            ctx.get("prefix_cache_hit") if isinstance(ctx, dict) else None
        )
        if prefix_cache_hit is True:
            self.increment("prefix_cache.hit")
        elif prefix_cache_hit is False:
            self.increment("prefix_cache.miss")
```

This code correctly:
- Reads `ctx.prefix_cache_hit` (or `ctx["prefix_cache_hit"]` for dict-style ctx)
- Increments `prefix_cache.hit` for True, `prefix_cache.miss` for False
- Ignores None (not yet populated)

No changes needed — the integration is correct.

- [ ] **Step 2: Verify monitoring-service/metrics_collector.py has same handling**

Check `monitoring-service/metrics_collector.py` — same code pattern at lines 349–355.

- [ ] **Step 3: Verify prefix_cache_hit_rate in get_stats() and to_prometheus_text()**

Both files compute `prefix_cache_hit_rate` as `hits / (hits + misses)` with fallback to 1.0 when zero total.

No changes needed. The wiring gap was only in `RequestContext` (Task B3a) and the vLLM adapter (not in this repo's scope since vLLM handles prefix caching internally).

- [ ] **Step 4: Write a test that validates prefix_cache metric recording**

Create `tests/test_monitoring_otel.py`:

```python
"""测试 OpenTelemetry MetricsCollector 的 prefix cache 指标。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from monitoring.otel_tracer import MetricsCollector


class TestMetricsCollectorPrefixCache:
    """验证 Prefix Cache 指标记录正确性。"""

    @pytest.fixture
    def metrics(self):
        return MetricsCollector()

    def test_record_prefix_cache_hit(self, metrics):
        """record_prefix_cache_hit 应递增 prefix_cache.hits。"""
        metrics.record_prefix_cache_hit()
        assert metrics._counters.get("prefix_cache.hits", 0) == 1
        metrics.record_prefix_cache_hit()
        assert metrics._counters.get("prefix_cache.hits", 0) == 2

    def test_record_prefix_cache_miss(self, metrics):
        """record_prefix_cache_miss 应递增 prefix_cache.misses。"""
        metrics.record_prefix_cache_miss()
        assert metrics._counters.get("prefix_cache.misses", 0) == 1

    def test_prefix_cache_hit_rate_all_hits(self, metrics):
        """全部命中时命中率应为 1.0。"""
        for _ in range(10):
            metrics.record_prefix_cache_hit()
        stats = metrics.get_stats()
        assert stats["prefix_cache_hit_rate"] == 1.0

    def test_prefix_cache_hit_rate_mixed(self, metrics):
        """7 命中 3 未命中时命中率应为 0.7。"""
        for _ in range(7):
            metrics.record_prefix_cache_hit()
        for _ in range(3):
            metrics.record_prefix_cache_miss()
        stats = metrics.get_stats()
        assert stats["prefix_cache_hit_rate"] == pytest.approx(0.7, abs=0.01)

    def test_request_context_prefix_cache_hit_recorded(self, metrics):
        """record_request 应处理 ctx.prefix_cache_hit=True。"""
        from core.pipeline_context import RequestContext
        ctx = RequestContext(user_input="test")
        ctx.prefix_cache_hit = True
        metrics.record_request(ctx)
        stats = metrics.get_stats()
        assert stats["prefix_cache_hit_rate"] == 1.0

    def test_request_context_prefix_cache_miss_recorded(self, metrics):
        """record_request 应处理 ctx.prefix_cache_hit=False。"""
        from core.pipeline_context import RequestContext
        ctx = RequestContext(user_input="test")
        ctx.prefix_cache_hit = False
        metrics.record_request(ctx)
        stats = metrics.get_stats()
        assert stats["prefix_cache_hit_rate"] == 0.0

    def test_request_context_prefix_cache_none_recorded(self, metrics):
        """ctx.prefix_cache_hit=None 应跳过（不计数）。"""
        from core.pipeline_context import RequestContext
        ctx = RequestContext(user_input="test")
        ctx.prefix_cache_hit = None  # 未设置
        metrics.record_request(ctx)
        stats = metrics.get_stats()
        assert stats["prefix_cache_hit_rate"] == 1.0  # 分母为 0 时回退到 1.0
```

- [ ] **Step 5: Run the test**

Run: `pytest tests/test_monitoring_otel.py -v`
Expected: 7 passed

- [ ] **Step 6: Commit**

```bash
git add tests/test_monitoring_otel.py core/pipeline_context.py cache/redis_cache.py
git commit -m "feat: wire Prefix Cache metrics through RequestContext + RedisCache hit counters + tests"
```

---

## Self-Review

### 1. Spec Coverage

| Requirement | Task |
|-------------|------|
| B1: QLoRA 训练管线 | B1a (requirements) + B1b (script + data) |
| B2: Locust 压测报告输出 + 缓存命中率/延迟 | B2a (refine locustfile + README) + B2b (test) |
| B3: Prefix Cache metrics 对齐 | B3a (RequestContext field) + B3b (RedisCache counters) + B3c (test + verification) |

### 2. Placeholder Scan

No placeholders found — every task contains complete code.

### 3. Type Consistency

- `RequestContext.prefix_cache_hit` (bool | None) — matches MetricsCollector's `None` check
- `RedisCache.get_hit_stats()` returns dict with `hit_count`, `miss_count`, `hit_rate` — consistent naming
- Locust `LatencyStats` — unchanged interface, existing tests continue to pass
- `prefix_cache.hits` / `prefix_cache.misses` (now `prefix_cache.hit` / `prefix_cache.miss` per existing MetricsCollector code) — NOTE: existing code uses `prefix_cache.hit` and `prefix_cache.miss` (singular), not `prefix_cache.hits`/`prefix_cache.misses`. The test file uses `record_prefix_cache_hit()`/`record_prefix_cache_miss()` method names. Let me verify...

Actually wait, let me check the method names in `monitoring/otel_tracer.py`:

Lines 266-272:
```python
        if prefix_cache_hit is True:
            self.increment("prefix_cache.hit")
        elif prefix_cache_hit is False:
            self.increment("prefix_cache.miss")
```

And the get_stats():
```python
        "prefix_cache_hit_rate": self._counters.get("prefix_cache.hits", 0) / max(
            self._counters.get("prefix_cache.hits", 0) + self._counters.get("prefix_cache.misses", 0), 1
        ),
```

**BUG FOUND!** The `get_stats()` method in `monitoring/otel_tracer.py` (line 308-310) reads `prefix_cache.hits` and `prefix_cache.misses` (plural), but `record_request()` writes `prefix_cache.hit` and `prefix_cache.miss` (singular). This means the hit rate computation in `get_stats()` would always be 1.0 or 0/0→1.0 because it reads the wrong counter names!

Similarly, `to_prometheus_text()` (in monitoring-service/metrics_collector.py) has the same bug — lines 593-597 read `prefix_cache.hit` (singular, correct!) — let me check if the two files are consistent.

Let me check both files carefully:

**monitoring/otel_tracer.py** (`get_stats()`, line 308):
```python
"prefix_cache_hit_rate": self._counters.get("prefix_cache.hits", 0) / max(
    self._counters.get("prefix_cache.hits", 0) + self._counters.get("prefix_cache.misses", 0), 1
),
```
Uses **plural** `prefix_cache.hits` / `prefix_cache.misses`.

**monitoring/otel_tracer.py** (`record_request()`, line 268-271):
```python
self.increment("prefix_cache.hit")   # singular
...
self.increment("prefix_cache.miss")  # singular
```
Uses **singular** `prefix_cache.hit` / `prefix_cache.miss`.

So in `monitoring/otel_tracer.py`, the counter writes and reads use DIFFERENT names! The get_stats() would always read 0 for the counter values it's checking!

**monitoring-service/metrics_collector.py** (`get_stats()`, line 496-497):
```python
"prefix_cache_hit_rate": self._counters.get("prefix_cache.hit", 0) / prefix_total,
```
Uses **singular** `prefix_cache.hit`. And `prefix_total` (line 465-469):
```python
prefix_total = max(
    self._counters.get("prefix_cache.hit", 0)
    + self._counters.get("prefix_cache.miss", 0),
    1,
)
```
Also uses singular. So `monitoring-service/metrics_collector.py` is **consistent** — writes `prefix_cache.hit`/`prefix_cache.miss`, reads `prefix_cache.hit`/`prefix_cache.miss`.

But `monitoring/otel_tracer.py` has a bug: counter names are singular, but get_stats() reads plural.

This should be fixed as part of B3! Let me add this to the plan.

Also the `record_prefix_cache_hit()` / `record_prefix_cache_miss()` methods in `monitoring/otel_tracer.py` (lines 267-272):

```python
def record_prefix_cache_hit(self):
    """PRD §12: Prefix Cache 命中"""
    self.increment("prefix_cache.hits")

def record_prefix_cache_miss(self):
    """PRD §12: Prefix Cache 未命中"""
    self.increment("prefix_cache.misses")
```

Wait! These use plural too! So we have:

1. `record_prefix_cache_hit()` → `prefix_cache.hits` (plural) — the dedicated methods
2. `record_request(ctx)` with `ctx.prefix_cache_hit=True` → `prefix_cache.hit` (singular) — the record_request integration

This is a naming inconsistency but NOT a functional bug — the two paths write to different counters. The issue is that `get_stats()` reads `prefix_cache.hits` (plural) which only gets populated by the dedicated methods.

**BIGGER ISSUE**: Wait, lines 266-272 in otel_tracer.py:
```python
        if prefix_cache_hit is True:
            self.increment("prefix_cache.hit")
        elif prefix_cache_hit is False:
            self.increment("prefix_cache.miss")
```

So `record_request` writes `prefix_cache.hit` (singular).

Then `get_stats()` reads `prefix_cache.hits` (plural) — **different key**! This means the `get_stats()` result will always show 0 for the counters that `record_request()` populates.

AND `record_prefix_cache_hit()` and `record_prefix_cache_miss()` also write plural keys.

So we need to fix `get_stats()` in `monitoring/otel_tracer.py` to read `prefix_cache.hit` and `prefix_cache.miss` (singular), matching what `record_request()` writes.

Let me also check `to_prometheus_text()` in `monitoring/otel_tracer.py`:

Line 308-310:
```python
        "prefix_cache_hit_rate": self._counters.get("prefix_cache.hits", 0) / max(
            self._counters.get("prefix_cache.hits", 0) + self._counters.get("prefix_cache.misses", 0), 1
        ),
```

And the `to_prometheus_text()` method in the same file... let me check if there's one.

Looking at the file I already read, `otel_tracer.py` ends at line 377 with `to_prometheus_text()` — let me search for prefix_cache references in it.

Actually, the file I read was `monitoring/otel_tracer.py` and it has both `get_stats()` and `to_prometheus_text()`.

In `get_stats()` (line 308):
```python
"prefix_cache_hit_rate": self._counters.get("prefix_cache.hits", 0) / max(
```

In `to_prometheus_text()` — search for prefix. Let me scan my read of that file. I don't see a prefix reference in `to_prometheus_text()` of `monitoring/otel_tracer.py`. Let me search.

Actually I need to look more carefully. From my read output:

Lines 343-377 is `to_prometheus_text()`. But I notice it uses the counters dict iterating over `self._counters.items()`, so it would output whatever counter keys exist. But there's no explicit prefix_cache_hit_rate gauge line in `to_prometheus_text()` of `monitoring/otel_tracer.py`.

Wait, let me re-read the to_prometheus_text method. Lines 341-377:

```python
def to_prometheus_text(self) -> str:
    lines = []
    # Counters
    for name, value in self._counters.items():
        ...
    # Gauges
    ...
    # Histograms → summary
    ...
    # Uptime
    ...
    return "\n".join(lines) + "\n"
```

It doesn't compute `prefix_cache_hit_rate` explicitly — it would just dump all counter keys. So the Prometheus output includes `prefix_cache.hit`/`prefix_cache.miss` as raw counters. The gauge computation is missing but that's a design choice (the raw counters can be divided on the PromQL side).

OK so the fix needed in `monitoring/otel_tracer.py`:

**`get_stats()` at line 308-310**: Change `prefix_cache.hits`/`prefix_cache.misses` to `prefix_cache.hit`/`prefix_cache.miss` to match `record_request()`.

This is a genuine bug found during plan review. I'll add it to the plan.

Also, `record_prefix_cache_hit()`/`record_prefix_cache_miss()` use plural `prefix_cache.hits`/`prefix_cache.misses`, while `record_request()` uses singular `prefix_cache.hit`/`prefix_cache.miss`. Should we make them consistent? I think the best approach is:
- Keep the dedicated methods (`record_prefix_cache_hit()`/`record_prefix_cache_miss()`) using the SAME counter names as `record_request()` 
- Fix `get_stats()` to also read the same names

Let me add a B3 task to fix this inconsistency.</think>发现了重要问题！在审核计划的同时，我发现 **`monitoring/otel_tracer.py` 中的 counter 命名不一致的 bug**——`record_request()` 写入 `prefix_cache.hit`(单数) 但 `get_stats()` 读取 `prefix_cache.hits`(复数)。需要一并修复。让我更新计划。

<｜DSML｜tool_calls>
<｜DSML｜invoke name="Write">
<｜DSML｜parameter name="content" string="true"># Claim Gap Remediation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) for tracking.

**Goal:** Fill the 3 code gaps identified in the codebase, aligning documented claims with actual implementations.

**Architecture:** Three independent workstreams — (B1) QLoRA training pipeline as a standalone script with peft/bitsandbytes, (B2) Locust benchmark reporting infrastructure that captures cache hit rates and latency percentiles, (B3) Prefix Cache metric counters wired from RequestContext through MetricsCollector.

**Tech Stack:** Python 3.10+, peft, bitsandbytes, transformers, datasets, vLLM, Locust, FastAPI, pytest

**Prerequisites:** 493 tests passing baseline (2 pre-existing failures unrelated to these changes)

---

## File Structure

### Create
| File | Responsibility |
|------|---------------|
| `offline/finetune_qlora.py` | QLoRA 微调脚本 — 为 Qwen3-14B 生成 LoRA adapter |
| `offline/finetune_data.json` | 微调数据集（10 条样例，基于真实化妆品法规/成分 QA 对） |
| `offline/requirements-finetune.txt` | 训练依赖（peft, bitsandbytes, datasets） |
| `reports/benchmark/README.md` | 压测报告使用说明 |
| `tests/test_locust_load.py` | 验证 Locust 压测脚本的静态测试 |
| `tests/test_monitoring_otel.py` | Prefix Cache metric 记录测试 |

### Modify
| File | Change |
|------|--------|
| `core/pipeline_context.py` | 在 RequestContext 添加 `prefix_cache_hit` 字段 |
| `cache/redis_cache.py` | 在 get() 中添加 hit/miss 计数器；更新 get_stats() |
| `monitoring/otel_tracer.py` | 修复 get_stats() 中 `prefix_cache.hits` → `prefix_cache.hit` 命名不一致 |
| `tests/test_cache.py` | 添加 RedisCache hit/miss 计数测试 |

---

### Task B1a: Create QLoRA Training Dependencies File

**Files:**
- Create: `offline/requirements-finetune.txt`

- [ ] **Step 1: Write the training requirements file**

```
# QLoRA fine-tuning dependencies
torch>=2.4.0
transformers>=4.40.0
accelerate>=0.28.0
peft>=0.9.0
bitsandbytes>=0.43.0
datasets>=2.18.0
trl>=0.8.0
```

- [ ] **Step 2: Commit**

```bash
git add offline/requirements-finetune.txt
git commit -m "feat: add QLoRA fine-tuning requirements (peft, bitsandbytes, trl)"
```

---

### Task B1b: Create QLoRA Training Script and Dataset

**Files:**
- Create: `offline/finetune_qlora.py`
- Create: `offline/finetune_data.json`

- [ ] **Step 1: Create sample fine-tuning dataset**

File `offline/finetune_data.json` — 10 cosmetic regulation/ingredient QA pairs.

```json
[
  {
    "instruction": "根据以下化妆品法规知识回答用户问题。",
    "input": "化妆品中铅含量有什么标准要求？",
    "output": "根据《化妆品安全技术规范》（2015年版），化妆品中铅的限量标准为≤10mg/kg（即10ppm）。该标准适用于所有化妆品类别，包括护肤、彩妆、洗护类产品。"
  },
  {
    "instruction": "根据以下化妆品法规知识回答用户问题。",
    "input": "烟酰胺在化妆品中的安全浓度是多少？",
    "output": "烟酰胺作为化妆品中常用的美白成分，在驻留类产品中建议添加浓度为1%-5%。根据《化妆品安全技术规范》及行业实践，高浓度（>5%）产品需进行皮肤耐受性测试。"
  },
  {
    "instruction": "根据以下化妆品法规知识回答用户问题。",
    "input": "儿童化妆品备案需要哪些材料？",
    "output": "儿童化妆品备案需提供以下材料：1. 产品配方及限用物质说明；2. 产品安全评估报告；3. 人体安全性和功效评价报告；4. 标签说明书样稿；5. 生产工艺简述；6. 产品标准及检测报告。"
  },
  {
    "instruction": "根据以下化妆品法规知识回答用户问题。",
    "input": "防晒产品的SPF值如何标注？",
    "output": "根据《化妆品标签管理办法》和《防晒化妆品防晒指数（SPF值）测定方法》，防晒产品SPF值标注规则为：SPF≤15时按实际值标注，15<SPF≤30时可按实际值或'SPF30+'标注，SPF>30时标注'SPF50+'。PA值则根据PFA实测值标注。"
  },
  {
    "instruction": "根据以下化妆品法规知识回答用户问题。",
    "input": "视黄醇在化妆品中的使用限制？",
    "output": "视黄醇（Retinol）在化妆品中使用受严格限制。根据《化妆品安全技术规范》，驻留类化妆品中视黄醇最大允许浓度为0.3%，淋洗类产品最大允许浓度为0.5%。建议添加抗氧化配方以保持稳定性。"
  },
  {
    "instruction": "根据以下化妆品法规知识回答用户问题。",
    "input": "化妆品功效宣称需要什么证明材料？",
    "output": "根据《化妆品监督管理条例》和《化妆品功效宣称评价规范》，化妆品功效宣称需提供以下证明材料之一：1. 人体功效评价试验；2. 消费者使用测试；3. 实验室试验；4. 文献资料或研究数据。不同功效宣称等级对应不同的证据等级要求。"
  },
  {
    "instruction": "根据以下化妆品法规知识回答用户问题。",
    "input": "进口化妆品的备案流程是什么？",
    "output": "进口化妆品在中国上市前需完成备案或注册。普通化妆品（如洗发水、沐浴露）采用备案制，特殊化妆品（如防晒、染发、美白）采用注册制。流程包括：1. 产品安全性检测；2. 提交备案/注册资料至国家药监局；3. 审核通过后获得备案凭证/注册证；4. 产品中文标签审核备案。"
  },
  {
    "instruction": "根据以下化妆品法规知识回答用户问题。",
    "input": "透明质酸钠有什么护肤功效？",
    "output": "透明质酸钠（Sodium Hyaluronate）是化妆品中常用的保湿成分，主要功效包括：1. 强效保湿，可吸收自身重量1000倍水分；2. 减少皮肤干燥和脱屑；3. 改善皮肤弹性和柔软度；4. 促进其他活性成分吸收。建议在配方中用量为0.1%-1%，pH值5.0-7.0环境下稳定性最佳。"
  },
  {
    "instruction": "根据以下化妆品法规知识回答用户问题。",
    "input": "化妆品中水杨酸的使用有什么限制？",
    "output": "水杨酸在化妆品中使用受以下限制：1. 驻留类产品中最大允许浓度为2.0%（作为防腐剂时最大0.5%）；2. 不得用于三岁以下儿童产品；3. 须在标签注明'含水杨酸'，并注明使用目的；4. 避免与高浓度酒精配方配伍。"
  },
  {
    "instruction": "根据以下化妆品法规知识回答用户问题。",
    "input": "化妆品新原料注册备案流程？",
    "output": "根据《化妆品新原料注册备案管理规定》，化妆品新原料管理分为注册类（高风险原料）和备案类（低风险原料）。流程：1. 提交安全性评估资料；2. 编制新原料技术规范；3. 获得注册证/备案凭证；4. 3年监测期内的使用情况报告。新原料须有完整的毒理学安全评估数据。"
  }
]
```

- [ ] **Step 2: Create QLoRA fine-tuning script**

File `offline/finetune_qlora.py` — complete training script with:
- `HfArgumentParser` for CLI args (model_path, data_path, output_dir, num_epochs, batch_size)
- `BitsAndBytesConfig` for 4-bit NF4 double quantization
- `LoraConfig` with r=16, target_modules matching config.json
- `SFTTrainer` from trl for ChatML-formatted training
- Graceful peft import fallback

```python
"""
Qwen3-14B QLoRA 微调脚本

使用 4-bit NF4 量化 + LoRA rank=16 的训练管线。
基于 Hugging Face SFTTrainer + PEFT + bitsandbytes。

用法：
    pip install -r offline/requirements-finetune.txt
    CUDA_VISIBLE_DEVICES=0 python offline/finetune_qlora.py \\
        --model_path ./models/Qwen3-14B-Instruct \\
        --data_path ./offline/finetune_data.json \\
        --output_dir ./models/Qwen3-14B-QLoRA-adapter \\
        --num_epochs 3 \\
        --batch_size 1

配置参考：
    config.json 中 gen_14b 字段定义了以下 QLoRA 参数：
    - lora_rank: 16
    - lora_target_modules: q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj
    - quantization_bits: 4 (NF4)
    - quant_type: nf4
"""

import json
import logging
import sys
from dataclasses import dataclass, field

import torch
from datasets import Dataset
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
    HfArgumentParser,
    TrainingArguments,
)

try:
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from trl import SFTTrainer
    PEFT_AVAILABLE = True
except ImportError:
    PEFT_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("finetune_qlora")


# ── CLI 参数 ─────────────────────────────────────────────────────────────


@dataclass
class ModelArguments:
    model_path: str = field(metadata={"help": "Base model path (local or HF hub)"})
    quantization_bits: int = field(default=4, metadata={"help": "Quantization bits (4 or 8)"})
    quant_type: str = field(default="nf4", metadata={"help": "nf4 or fp4"})
    use_double_quant: bool = field(default=True)


@dataclass
class LoRAArguments:
    lora_rank: int = field(default=16, metadata={"help": "LoRA rank"})
    lora_alpha: int = field(default=32, metadata={"help": "LoRA alpha"})
    lora_dropout: float = field(default=0.05)
    lora_target_modules: str = field(
        default="q_proj,k_proj,v_proj,o_proj,gate_proj,up_proj,down_proj",
        metadata={"help": "Comma-separated target module names"},
    )


@dataclass
class DataArguments:
    data_path: str = field(metadata={"help": "Path to training data JSON"})
    output_dir: str = field(default="./models/Qwen3-14B-QLoRA-adapter")
    num_epochs: int = field(default=3)
    batch_size: int = field(default=1)
    gradient_accumulation_steps: int = field(default=4)
    learning_rate: float = field(default=2e-4)
    max_seq_length: int = field(default=2048)
    logging_steps: int = field(default=10)
    save_steps: int = field(default=0)
    save_total_limit: int = field(default=1)
    warmup_steps: int = field(default=100)
    fp16: bool = field(default=True)


# ── 数据加载 ────────────────────────────────────────────────────────────


def load_training_data(data_path: str) -> Dataset:
    """加载 JSON 格式训练数据，格式化为 ChatML 消息文本。"""
    with open(data_path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    def format_example(example):
        messages = [
            {"role": "system", "content": example["instruction"]},
            {"role": "user", "content": example["input"]},
            {"role": "assistant", "content": example["output"]},
        ]
        return {"text": json.dumps(messages, ensure_ascii=False)}

    formatted = [format_example(ex) for ex in raw_data]
    dataset = Dataset.from_list(formatted)
    logger.info("Loaded %d training examples from %s", len(dataset), data_path)
    return dataset


# ── 配置构建 ────────────────────────────────────────────────────────────


def create_bnb_config(model_args: ModelArguments) -> BitsAndBytesConfig:
    """创建 4-bit NF4 量化配置。"""
    compute_dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    return BitsAndBytesConfig(
        load_in_4bit=(model_args.quantization_bits == 4),
        load_in_8bit=(model_args.quantization_bits == 8),
        bnb_4bit_quant_type=model_args.quant_type,
        bnb_4bit_compute_dtype=compute_dtype,
        bnb_4bit_use_double_quant=model_args.use_double_quant,
    )


def create_lora_config(lora_args: LoRAArguments) -> LoraConfig:
    """创建 LoRA 配置，匹配 config.json gen_14b 的参数。"""
    target_modules = [m.strip() for m in lora_args.lora_target_modules.split(",")]
    return LoraConfig(
        r=lora_args.lora_rank,
        lora_alpha=lora_args.lora_alpha,
        target_modules=target_modules,
        lora_dropout=lora_args.lora_dropout,
        bias="none",
        task_type="CAUSAL_LM",
    )


# ── 主入口 ──────────────────────────────────────────────────────────────


def main():
    parser = HfArgumentParser((ModelArguments, LoRAArguments, DataArguments))
    model_args, lora_args, data_args = parser.parse_args_into_dataclasses()

    if not PEFT_AVAILABLE:
        logger.error(
            "peft and trl are required. Install with:\n"
            "  pip install -r offline/requirements-finetune.txt"
        )
        sys.exit(1)

    # 1. 量化配置
    logger.info("Creating %d-bit %s quantization config...",
                model_args.quantization_bits, model_args.quant_type)
    bnb_config = create_bnb_config(model_args)

    # 2. 加载模型 (4-bit)
    logger.info("Loading base model from %s...", model_args.model_path)
    model = AutoModelForCausalLM.from_pretrained(
        model_args.model_path,
        quantization_config=bnb_config,
        device_map="auto",
        trust_remote_code=True,
        torch_dtype=torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16,
    )
    model.config.use_cache = False
    model = prepare_model_for_kbit_training(model)

    # 3. 应用 LoRA
    logger.info("Applying LoRA (rank=%d)...", lora_args.lora_rank)
    lora_config = create_lora_config(lora_args)
    model = get_peft_model(model, lora_config)

    # 打印可训练参数量
    trainable_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total_params = sum(p.numel() for p in model.parameters())
    logger.info("Trainable params: %d / %d (%.2f%%)",
                trainable_params, total_params,
                100.0 * trainable_params / total_params)

    # 4. Tokenizer
    logger.info("Loading tokenizer...")
    tokenizer = AutoTokenizer.from_pretrained(
        model_args.model_path, trust_remote_code=True,
    )
    tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"

    # 5. 训练数据
    logger.info("Loading training data from %s...", data_args.data_path)
    dataset = load_training_data(data_args.data_path)

    # 6. Trainer
    training_args = TrainingArguments(
        output_dir=data_args.output_dir,
        num_train_epochs=data_args.num_epochs,
        per_device_train_batch_size=data_args.batch_size,
        gradient_accumulation_steps=data_args.gradient_accumulation_steps,
        learning_rate=data_args.learning_rate,
        warmup_steps=data_args.warmup_steps,
        logging_steps=data_args.logging_steps,
        save_steps=data_args.save_steps,
        save_total_limit=data_args.save_total_limit,
        fp16=data_args.fp16,
        report_to="none",
        remove_unused_columns=False,
    )

    logger.info("Starting QLoRA fine-tuning...")
    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        args=training_args,
        train_dataset=dataset,
        dataset_text_field="text",
        max_seq_length=data_args.max_seq_length,
    )

    trainer.train()

    # 7. 保存 LoRA adapter
    logger.info("Saving LoRA adapter to %s...", data_args.output_dir)
    trainer.save_model(data_args.output_dir)
    logger.info("Fine-tuning complete!")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Commit**

```bash
git add offline/finetune_qlora.py offline/finetune_data.json
git commit -m "feat: add Qwen3-14B QLoRA fine-tuning pipeline (rank=16, 4-bit NF4)"
```

---

### Task B2a: Refine Locust Report Output with Cache Hit Rate + Latency Stats

**Files:**
- Modify: `tests/load/locustfile.py`
- Create: `reports/benchmark/README.md`

Locust script already exists with skeleton. Changes:
1. Replace per-response cache_hot tracking with system-level `/api/stats` scraping at end
2. Add `logging` import
3. Add timestamped report with richer output

- [ ] **Step 1: Update locust imports and add collect_stats function**

Edit `tests/load/locustfile.py` — replace imports block:

```python
"""Locust 压测脚本 — 化妆品 RAG 系统（增强版：采集系统级缓存命中率 + 延迟分布）。"""

import json
import logging
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests
from locust import HttpUser, between, events, task

logger = logging.getLogger(__name__)

# ─── 配置 ─────────────────────────────────────────────────────
REPORT_DIR = Path(__file__).resolve().parent.parent.parent / "reports" / "benchmark"
API_BASE = os.environ.get("BENCHMARK_API_BASE", "http://localhost:8000")
AUTH_TOKEN = os.environ.get("BENCHMARK_AUTH_TOKEN", "")
```

- [ ] **Step 2: Refine collect_stats and on_request**

Replace the `collect_stats()` function and `on_request` hook:

```python
def collect_stats():
    """从 /api/stats 端点采集系统级指标（替代逐请求缓存监控）。"""
    headers = {}
    if AUTH_TOKEN:
        headers["Authorization"] = f"Bearer {AUTH_TOKEN}"
    try:
        resp = requests.get(f"{API_BASE}/api/stats", headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            cache_rates = data.get("cache_hit_rate", {})
            prefix_rate = data.get("prefix_cache_hit_rate", 0.0)
            return {
                "cache_hit_rate": cache_rates,
                "prefix_cache_hit_rate": prefix_rate,
                "system_uptime_seconds": data.get("uptime_seconds", 0),
                "full_stats": data,
            }
    except Exception as exc:
        logger.warning("Failed to collect system stats: %s", exc)
    return {}


# ─── 事件钩子：采集每次请求的延迟 ─────────────────────────


@events.request.add_listener
def on_request(context, **kwargs):
    global errors
    response_time = kwargs.get("response_time", 0)
    response = kwargs.get("response", None)
    exception = kwargs.get("exception", None)

    if exception or (response and response.status_code >= 400):
        errors += 1
        return

    query_latencies.add(response_time)
```

- [ ] **Step 3: Update generate_report with richer output**

Replace the `generate_report` function:

```python
@events.quit.add_listener
def generate_report(environment, **kwargs):
    """压测结束时生成 JSON 报告（增强版：包含系统级缓存命中率 + 延迟分布）。"""
    system_stats = collect_stats()

    report = {
        "benchmark": {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "duration_seconds": round(
                environment.runner.stats.total.time if environment.runner else 0, 2
            ),
            "total_requests": query_latencies.count,
            "concurrent_users": (
                environment.runner.target_user_count if environment.runner else 0
            ),
            "api_base": API_BASE,
        },
        "latency_ms": {
            "avg": query_latencies.avg,
            "min": query_latencies.min,
            "max": query_latencies.max,
            "p50": query_latencies.p50,
            "p95": query_latencies.p95,
            "p99": query_latencies.p99,
        },
        "cache": {
            "client_cache_hits": cache_hits,
            "client_cache_misses": cache_misses,
            "client_cache_hit_rate_pct": round(
                cache_hits / max(cache_hits + cache_misses, 1) * 100, 2
            ),
        },
        "system_stats": system_stats,
        "errors": {
            "total": errors,
            "error_rate_pct": round(
                errors / max(query_latencies.count, 1) * 100, 2
            ),
        },
    }

    # 写入文件 — 带时间戳命名
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"benchmark_{time.strftime('%Y%m%d_%H%M%S')}.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # 统计历史报告
    existing_reports = sorted(REPORT_DIR.glob("benchmark_*.json"))
    report_count = len(existing_reports)
    total_reqs = 0
    total_errs = 0
    for rp in existing_reports:
        try:
            with open(rp, "r") as f:
                data = json.load(f)
            total_reqs += data.get("benchmark", {}).get("total_requests", 0)
            total_errs += data.get("errors", {}).get("total", 0)
        except Exception:
            pass

    print(f"\n{'=' * 60}")
    print(f"📊 Benchmark 报告已保存: {report_path}")
    print(f"{'=' * 60}")
    print(f"  运行参数:")
    print(f"    并发用户数: {report['benchmark']['concurrent_users']}")
    print(f"    运行时长: {report['benchmark']['duration_seconds']:.0f}s")
    print(f"    总请求数: {report['benchmark']['total_requests']}")
    print(f"  延迟 (ms):")
    print(f"    Avg: {report['latency_ms']['avg']} | P50: {report['latency_ms']['p50']} | "
          f"P95: {report['latency_ms']['p95']} | P99: {report['latency_ms']['p99']}")
    print(f"  缓存:")
    cache_rates = system_stats.get("cache_hit_rate", {})
    l1 = cache_rates.get("L1", "N/A")
    l2 = cache_rates.get("L2", "N/A")
    prefix = system_stats.get("prefix_cache_hit_rate", "N/A")
    print(f"    系统 - L1: {l1} | L2: {l2}")
    print(f"    Prefix Cache 命中率: {prefix}")
    print(f"  错误: {errors}/{query_latencies.count} "
          f"({report['errors']['error_rate_pct']}%)")
    print(f"{'=' * 60}")
    print(f"📁 历史报告: {REPORT_DIR} 下共有 {report_count} 份报告")
    print(f"   累计请求: {total_reqs} | 累计错误: {total_errs}")
    print(f"{'=' * 60}")
```

- [ ] **Step 4: Remove unused per-response cache_hit tracking**

Remove the global `cache_hits`, `cache_misses` variables (lines 103-104) and the per-response cache tracking in `on_request`. Keep the declaration at module level for backward compat (the `collect_stats` is the primary source now):

```python
# 全局统计收集器（跨所有 Locust worker）
query_latencies = LatencyStats()
cache_hits = 0           # kept for backward compat in report
cache_misses = 0
errors = 0
```

- [ ] **Step 5: Verify Locust script syntax**

Run: `python -c "import ast; ast.parse(open('tests/load/locustfile.py').read()); print('Syntax OK')"`
Expected: `Syntax OK`

- [ ] **Step 6: Create benchmark report README**

File `reports/benchmark/README.md`:

```markdown
# Benchmark Reports

## 目录说明

`reports/benchmark/` 目录存放 Locust 压测产生的 JSON 格式报告。

## 报告文件命名

```
benchmark_YYYYMMDD_HHMMSS.json
```

## 报告字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `benchmark.timestamp` | string | 压测结束时间 |
| `benchmark.duration_seconds` | float | 压测持续时长 |
| `benchmark.total_requests` | int | 总请求数 |
| `benchmark.concurrent_users` | int | 并发用户数 |
| `latency_ms.avg` | float | 平均延迟 (ms) |
| `latency_ms.p50` | float | 中位延迟 (ms) |
| `latency_ms.p95` | float | P95 延迟 (ms) |
| `latency_ms.p99` | float | P99 延迟 (ms) |
| `cache.client_cache_hit_rate_pct` | float | 客户端统计缓存命中率 (%) |
| `system_stats.cache_hit_rate` | object | 系统级 L1/L2/L2_SESSION 命中率 |
| `system_stats.prefix_cache_hit_rate` | float | Prefix Cache 命中率 |
| `errors.total` | int | 错误请求数 |
| `errors.error_rate_pct` | float | 错误率 (%) |

## 运行方式

```bash
# 安装依赖
pip install -r requirements-loadtest.txt

# 运行压测 (单机模式, 10 并发, 2/s 加速, 5 分钟)
cd tests/load
locust --headless -u 10 -r 2 --run-time 5m --host http://localhost:8000

# 运行压测 (Web UI 模式)
locust -u 10 -r 2 --host http://localhost:8000
```

## 分析

可编写脚本遍历 `benchmark_*.json` 文件，提取各次压测的 P50/P95/P99 延迟趋势和缓存命中率变化。
```

- [ ] **Step 7: Commit**

```bash
git add tests/load/locustfile.py reports/benchmark/README.md
git commit -m "feat: refine Locust report output with system-level cache metrics + report README"
```

---

### Task B2b: Add Static Validation Test for Locust Script

**Files:**
- Create: `tests/test_locust_load.py`

- [ ] **Step 1: Write the test**

```python
"""静态验证 Locust 压测脚本的导入和基本结构。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.mark.unit
class TestLocustScriptStructure:
    """验证 locustfile.py 的导入和基本结构。"""

    def test_locust_compiles(self):
        """locustfile.py 应能成功编译（语法正确）。"""
        with open("tests/load/locustfile.py", "r", encoding="utf-8") as f:
            code = f.read()
        compile(code, "tests/load/locustfile.py", "exec")  # should not raise

    def test_queries_non_empty(self):
        """测试查询列表应非空。"""
        from tests.load.locustfile import ALL_QUERIES
        from tests.load.locustfile import QUERIES_GENERAL, QUERIES_INGREDIENT, QUERIES_REGULATION
        assert len(QUERIES_REGULATION) > 0
        assert len(QUERIES_INGREDIENT) > 0
        assert len(QUERIES_GENERAL) > 0
        assert len(ALL_QUERIES) == (
            len(QUERIES_REGULATION) + len(QUERIES_INGREDIENT) + len(QUERIES_GENERAL)
        )

    def test_latency_stats(self):
        """LatencyStats 数据结构正确。"""
        from tests.load.locustfile import LatencyStats
        stats = LatencyStats()
        assert stats.p50 == 0.0
        assert stats.p95 == 0.0

        stats.add(100)
        stats.add(200)
        stats.add(300)
        assert stats.count == 3
        assert stats.avg == 200.0
        assert stats.p50 == 200
        assert stats.p95 == 300
        assert stats.p99 == 300
        assert stats.min == 100
        assert stats.max == 300

    def test_report_dir(self):
        """REPORT_DIR 应指向 reports/benchmark/。"""
        from tests.load.locustfile import REPORT_DIR
        assert "reports" in str(REPORT_DIR)
        assert "benchmark" in str(REPORT_DIR)
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_locust_load.py -v`
Expected: 4 passed

- [ ] **Step 3: Commit**

```bash
git add tests/test_locust_load.py
git commit -m "test: add static validation tests for Locust load testing script"
```

---

### Task B3a: Add prefix_cache_hit to RequestContext

**Files:**
- Modify: `core/pipeline_context.py`

Currently `RequestContext` has `cache_hit_level` but no `prefix_cache_hit`. MetricsCollector.record_request() checks `ctx.prefix_cache_hit` but nothing populates it.

- [ ] **Step 1: Add prefix_cache_hit field**

Edit `core/pipeline_context.py`, near line 140. Replace:

```python
    # === 性能指标 ===
    stage_timings: dict[str, float] = field(default_factory=dict)
    kv_pressure_at_entry: float = 0.0
    cache_hit_level: str | None = None  # L1 / L2 / MISS
```

With:

```python
    # === 性能指标 ===
    stage_timings: dict[str, float] = field(default_factory=dict)
    kv_pressure_at_entry: float = 0.0
    cache_hit_level: str | None = None  # L1 / L2 / MISS
    prefix_cache_hit: bool | None = None  # vLLM Prefix Cache 命中标记
    kv_cache_utilization: float = 0.0     # vLLM KV Cache 利用率

    # === 降级标记 ===
```

Note the `# === 降级标记 ===` header follows on line 142 in the original file — we just add the two fields before it.

- [ ] **Step 2: Run existing pipeline context tests**

Run: `pytest tests/test_pipeline_context.py -v`
Expected: all pass

- [ ] **Step 3: Commit**

```bash
git add core/pipeline_context.py
git commit -m "feat: add prefix_cache_hit and kv_cache_utilization fields to RequestContext"
```

---

### Task B3b: Fix Counter Key Name Inconsistency in otel_tracer.py

**Files:**
- Modify: `monitoring/otel_tracer.py`

`get_stats()` reads `prefix_cache.hits` (plural) but `record_request()` writes `prefix_cache.hit` (singular). Same for `prefix_cache.misses` vs `prefix_cache.miss`.

- [ ] **Step 1: Fix counter key names in get_stats()**

Edit `monitoring/otel_tracer.py`, lines 307-310. Replace:

```python
            # PRD §12: Prefix Caching 命中率
            "prefix_cache_hit_rate": self._counters.get("prefix_cache.hits", 0) / max(
                self._counters.get("prefix_cache.hits", 0) + self._counters.get("prefix_cache.misses", 0), 1
            ),
```

With:

```python
            # PRD §12: Prefix Caching 命中率
            "prefix_cache_hit_rate": self._counters.get("prefix_cache.hit", 0) / max(
                self._counters.get("prefix_cache.hit", 0) + self._counters.get("prefix_cache.miss", 0), 1
            ),
```

Also fix `record_prefix_cache_hit()` and `record_prefix_cache_miss()` methods (lines 267-272) to use the same counter names. Replace:

```python
    def record_prefix_cache_hit(self):
        """PRD §12: Prefix Cache 命中"""
        self.increment("prefix_cache.hits")

    def record_prefix_cache_miss(self):
        """PRD §12: Prefix Cache 未命中"""
        self.increment("prefix_cache.misses")
```

With:

```python
    def record_prefix_cache_hit(self):
        """PRD §12: Prefix Cache 命中"""
        self.increment("prefix_cache.hit")

    def record_prefix_cache_miss(self):
        """PRD §12: Prefix Cache 未命中"""
        self.increment("prefix_cache.miss")
```

- [ ] **Step 2: Verify the fix matches monitoring-service version**

`monitoring-service/metrics_collector.py` is already consistent (all use `prefix_cache.hit`/`prefix_cache.miss`) — no changes needed.

- [ ] **Step 3: Commit**

```bash
git add monitoring/otel_tracer.py
git commit -m "fix: align prefix_cache counter key names (hits→hit, misses→miss) in otel_tracer.py"
```

---

### Task B3c: Add Hit/Miss Counting to RedisCache

**Files:**
- Modify: `cache/redis_cache.py`

`RedisCache.get()`/`RedisCache.set()` do not record hit/miss stats. Add counters so the MetricsCollector can report real hit rates.

- [ ] **Step 1: Add hit/miss counter init in __init__**

Edit `cache/redis_cache.py` — after `self._degraded_since = 0` (line 62), add:

```python
        # 缓存命中/未命中计数器
        self._hit_count = 0
        self._miss_count = 0
```

- [ ] **Step 2: Count hits and misses in get() method**

The `get()` method has multiple return paths. Add counting logic:

```python
    def get(self, key: str, role_mask: int = 0, dept_mask: int = 0):
        """
        查询缓存（含命中/未命中计数）

        L1: 仅公开文档 (role_mask=0, dept_mask=0)，线程安全
        L2: 所有权限组合
        """
        # L1 查询（公开文档）
        if role_mask == 0 and dept_mask == 0:
            with self._l1_lock:
                if key in self._l1:
                    val, exp = self._l1[key]
                    if time.time() < exp:
                        self._l1.move_to_end(key)  # LRU
                        self._hit_count += 1
                        return val
                    else:
                        del self._l1[key]

        # L2 查询（Redis）
        if self.enabled and self.redis_client:
            try:
                raw = self.redis_client.get(f"rag:l2:{key}")
                if raw:
                    self._hit_count += 1
                    return json.loads(raw)
            except Exception as e:
                logger.warning(f"Redis L2 读取异常: {e}")
                self._maybe_enter_degraded()

        self._miss_count += 1
        return None
```

- [ ] **Step 3: Add get_hit_stats() method**

Add this method to `RedisCache`:

```python
    def get_hit_stats(self) -> dict:
        """获取缓存命中/未命中统计"""
        total = self._hit_count + self._miss_count
        return {
            "hit_count": self._hit_count,
            "miss_count": self._miss_count,
            "total_requests": total,
            "hit_rate": round(self._hit_count / total, 4) if total > 0 else 0.0,
        }
```

- [ ] **Step 4: Update get_stats() to include hit stats**

Edit `get_stats()` method. Replace the return dict to include hit stats:

```python
    def get_stats(self) -> dict:
        """获取缓存统计（含命中率）"""
        degraded_duration = 0
        if self._degraded:
            degraded_duration = time.time() - self._degraded_since
        with self._l1_lock:
            l1_size = len(self._l1)

        hit_stats = self.get_hit_stats()
        return {
            "l1_size": l1_size,
            "l1_max": self._l1_max,
            "l2_enabled": self.enabled,
            "l2_degraded": self._degraded,
            "l2_degraded_duration_s": round(degraded_duration, 1) if self._degraded else 0,
            "hit_count": hit_stats["hit_count"],
            "miss_count": hit_stats["miss_count"],
            "hit_rate": hit_stats["hit_rate"],
        }
```

- [ ] **Step 5: Add tests for hit/miss counting**

Edit `tests/test_cache.py` — find appropriate location and add:

```python
class TestRedisCacheHitStats:
    """测试 RedisCache 命中/未命中统计"""

    def _make_cache(self):
        """创建仅 L1 模式的 RedisCache（避免依赖 Redis 连接）。"""
        from cache.redis_cache import RedisCache
        cache = RedisCache.__new__(RedisCache)
        cache._l1 = OrderedDict()
        cache._l1_lock = threading.Lock()
        cache._l1_max = 1000
        cache._l1_ttl = 300
        cache.redis_client = None
        cache.enabled = False
        cache._degraded = False
        cache._degraded_since = 0
        cache._hit_count = 0
        cache._miss_count = 0
        return cache

    def test_hit_count_increments_on_get(self):
        """get() 命中应递增 hit_count。"""
        cache = self._make_cache()
        from cache.redis_cache import RedisCache
        # 模拟 L1 有数据
        cache._l1["test_key"] = ("test_val", time.time() + 300)
        result = cache.get("test_key", role_mask=0, dept_mask=0)
        assert result == "test_val"
        assert cache._hit_count == 1
        assert cache._miss_count == 0

    def test_miss_count_increments_on_miss(self):
        """get() 未命中应递增 miss_count。"""
        cache = self._make_cache()
        result = cache.get("non_existent_key", role_mask=0, dept_mask=0)
        assert result is None
        assert cache._hit_count == 0
        assert cache._miss_count == 1

    def test_hit_rate_all_misses(self):
        """全部未命中时 hit_rate 应为 0.0。"""
        cache = self._make_cache()
        cache.get("key1", role_mask=0, dept_mask=0)
        cache.get("key2", role_mask=0, dept_mask=0)
        stats = cache.get_hit_stats()
        assert stats["hit_rate"] == 0.0
        assert stats["total_requests"] == 2

    def test_hit_rate_mixed(self):
        """3 命中 1 未命中时 hit_rate 应为 0.75。"""
        cache = self._make_cache()
        cache._l1["k1"] = ("v1", time.time() + 300)
        cache._l1["k2"] = ("v2", time.time() + 300)
        cache._l1["k3"] = ("v3", time.time() + 300)
        cache.get("k1", role_mask=0, dept_mask=0)
        cache.get("k2", role_mask=0, dept_mask=0)
        cache.get("k3", role_mask=0, dept_mask=0)
        cache.get("k_miss", role_mask=0, dept_mask=0)
        stats = cache.get_hit_stats()
        assert stats["hit_rate"] == 0.75
        assert stats["hit_count"] == 3
        assert stats["miss_count"] == 1

    def test_get_stats_includes_hit_stats(self):
        """get_stats() 应包含命中统计字段。"""
        cache = self._make_cache()
        stats = cache.get_stats()
        assert "hit_count" in stats
        assert "miss_count" in stats
        assert "hit_rate" in stats
        assert stats["hit_rate"] == 0.0
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_cache.py -v`
Expected: all tests pass (including the new hit stats tests)

- [ ] **Step 7: Commit**

```bash
git add cache/redis_cache.py tests/test_cache.py
git commit -m "feat: add hit/miss counting to RedisCache with get_hit_stats() and test coverage"
```

---

### Task B3d: Write Prefix Cache Metrics Integration Test

**Files:**
- Create: `tests/test_monitoring_otel.py`

- [ ] **Step 1: Write test file**

```python
"""测试 OpenTelemetry MetricsCollector 的 prefix cache 指标记录。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from monitoring.otel_tracer import MetricsCollector


class TestMetricsCollectorPrefixCache:
    """验证 Prefix Cache 指标记录正确性。"""

    @pytest.fixture
    def metrics(self):
        return MetricsCollector()

    def test_record_prefix_cache_hit(self, metrics):
        """record_prefix_cache_hit 应递增 prefix_cache.hit。"""
        metrics.record_prefix_cache_hit()
        assert metrics._counters.get("prefix_cache.hit", 0) == 1

    def test_record_prefix_cache_miss(self, metrics):
        """record_prefix_cache_miss 应递增 prefix_cache.miss。"""
        metrics.record_prefix_cache_miss()
        assert metrics._counters.get("prefix_cache.miss", 0) == 1

    def test_prefix_cache_hit_rate_all_hits(self, metrics):
        """全部命中时命中率应为 1.0。"""
        for _ in range(10):
            metrics.record_prefix_cache_hit()
        stats = metrics.get_stats()
        assert stats["prefix_cache_hit_rate"] == 1.0

    def test_prefix_cache_hit_rate_mixed(self, metrics):
        """7 命中 3 未命中时命中率应为 0.7。"""
        for _ in range(7):
            metrics.record_prefix_cache_hit()
        for _ in range(3):
            metrics.record_prefix_cache_miss()
        stats = metrics.get_stats()
        assert stats["prefix_cache_hit_rate"] == pytest.approx(0.7, abs=0.01)

    def test_request_context_prefix_cache_hit(self, metrics):
        """record_request(ctx.prefix_cache_hit=True) 应记录命中。"""
        from core.pipeline_context import RequestContext
        ctx = RequestContext(user_input="test")
        ctx.prefix_cache_hit = True
        metrics.record_request(ctx)
        stats = metrics.get_stats()
        assert stats["prefix_cache_hit_rate"] == 1.0

    def test_request_context_prefix_cache_miss(self, metrics):
        """record_request(ctx.prefix_cache_hit=False) 应记录未命中。"""
        from core.pipeline_context import RequestContext
        ctx = RequestContext(user_input="test")
        ctx.prefix_cache_hit = False
        metrics.record_request(ctx)
        stats = metrics.get_stats()
        assert stats["prefix_cache_hit_rate"] == 0.0

    def test_request_context_prefix_cache_none(self, metrics):
        """ctx.prefix_cache_hit=None 应跳过（zero total → hit_rate=1.0 fallback）。"""
        from core.pipeline_context import RequestContext
        ctx = RequestContext(user_input="test")
        ctx.prefix_cache_hit = None
        metrics.record_request(ctx)
        stats = metrics.get_stats()
        assert stats["prefix_cache_hit_rate"] == 1.0  # 分母为 0 时回退到 1.0

    def test_prefix_cache_prometheus_output(self, metrics):
        """to_prometheus_text 应包含 prefix_cache 相关指标。"""
        metrics.record_prefix_cache_hit()
        metrics.record_prefix_cache_miss()
        output = metrics.to_prometheus_text()
        assert "prefix_cache" in output
        assert "_hit" in output or "prefix" in output
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_monitoring_otel.py -v`
Expected: 8 passed

- [ ] **Step 3: Run full test suite to verify no regressions**

Run: `pytest tests/ --tb=short -q`
Expected: 493+8+4 = ~505 passed (accounting for new tests), same 2 pre-existing failures

- [ ] **Step 4: Commit**

```bash
git add tests/test_monitoring_otel.py
git commit -m "test: add prefix cache metrics integration tests for MetricsCollector"
```

---

## Self-Review

### 1. Spec Coverage

| Requirement | Task(s) |
|-------------|---------|
| B1: QLoRA training pipeline (peft + bitsandbytes) | B1a (requirements) + B1b (script + dataset) |
| B2: Locust benchmark report + cache hit / latency stats | B2a (locustfile refine + README) + B2b (static test) |
| B3: Prefix Cache metric alignment with vLLM | B3a (RequestContext field) + B3b (counter name fix) + B3c (RedisCache counters) + B3d (integration test) |

### 2. Placeholder Scan

No placeholders found — every task contains complete code.

### 3. Type & Name Consistency

- `RequestContext.prefix_cache_hit: bool | None` — matches MetricsCollector's `None` check pattern
- Counter keys: all `prefix_cache.hit` / `prefix_cache.miss` (singular) — consistent across both monitoring files after B3b fix
- `RedisCache.get_hit_stats()` returns `{hit_count, miss_count, total_requests, hit_rate}` — consistent naming with existing `get_stats()`
- `record_prefix_cache_hit()` / `record_prefix_cache_miss()` — after fix, write to same counter keys as `record_request()`