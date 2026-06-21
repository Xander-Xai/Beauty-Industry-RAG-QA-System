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
