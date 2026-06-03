import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from datasets import load_dataset
import json

# 1. 加载模型和分词器
model_path = "chatglm3-6b"  # 替换为你的ChatGLM3-6B模型路径
tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)

# 2. 配置 BitsAndBytes 用于 QLoRA
quantization_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_quant_type="nf4",
    bnb_4bit_use_double_quant=True,
    bnb_4bit_compute_dtype=torch.bfloat16
)

model = AutoModelForCausalLM.from_pretrained(
    model_path,
    quantization_config=quantization_config,
    trust_remote_code=True,
    device_map="auto"
)

# 3. 准备模型进行 k-bit 训练
model = prepare_model_for_kbit_training(model)

# 4. 配置 LoRA
lora_config = LoraConfig(
    r=8,
    lora_alpha=32,
    target_modules=["query_key_value"],
    lora_dropout=0.05,
    bias="none",
    task_type="CAUSAL_LM"
)

model = get_peft_model(model, lora_config)
model.print_trainable_parameters()

# 5. 加载数据集
# 假设你的微调数据在 data/finetune_data.json 中
with open('data/finetune_data.json', 'r', encoding='utf-8') as f:
    raw_data = json.load(f)['data']

# 格式化数据集以适应模型输入
def format_example(example):
    instruction = example["instruction"]
    input_text = example["input"]
    output_text = example["output"]

    if input_text:
        prompt = f"Instruction: {instruction}\nInput: {input_text}\nOutput: {output_text}"
    else:
        prompt = f"Instruction: {instruction}\nOutput: {output_text}"
    return {"text": prompt}

dataset = load_dataset("json", data_files="data/finetune_data.json")
dataset = dataset.map(format_example, remove_columns=["instruction", "input", "output"])

# 6. 训练
from transformers import TrainingArguments, Trainer

training_args = TrainingArguments(
    output_dir="./qlora_finetuned_model",
    per_device_train_batch_size=1,
    gradient_accumulation_steps=4,
    learning_rate=2e-4,
    num_train_epochs=3,
    logging_steps=10,
    save_steps=100,
    fp16=True,  # 启用混合精度训练
    optim="paged_adamw_8bit",
    report_to="none" # 不上报到任何平台
)

class CustomTrainer(Trainer):
    def compute_loss(self, model, inputs, return_outputs=False):
        labels = inputs.pop("labels")
        outputs = model(**inputs)
        logits = outputs.logits
        loss_fct = torch.nn.CrossEntropyLoss()
        loss = loss_fct(logits.view(-1, logits.size(-1)), labels.view(-1))
        return (loss, outputs) if return_outputs else loss

trainer = CustomTrainer(
    model=model,
    args=training_args,
    train_dataset=dataset["train"],
    tokenizer=tokenizer,
)

trainer.train()

# 7. 保存微调后的模型
model.save_pretrained("./qlora_finetuned_model")
tokenizer.save_pretrained("./qlora_finetuned_model")

print("QLoRA 微调完成，模型已保存到 ./qlora_finetuned_model")

# 对应config.json修改路径"llm_path": "./path/to/your/finetuned_chatglm3-6b",
