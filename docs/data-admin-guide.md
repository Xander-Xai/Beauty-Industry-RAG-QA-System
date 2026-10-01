# 化妆品行业 RAG 问答系统数据管理员手册

## 1. 当前仓库状态

当前 checkout 中，在线查询链路完整，**离线建库管线也已完成实现**：

- `run_offline.py` 是预期入口，支持 `incremental`/`full`/`create-index`/`feedback`/`rewrite-feedback` 五种模式
- `offline/` 包包含完整实现：
  - `document_processor.py` — PDF/Word/Excel/TXT 文档清洗、切块
  - `image_processor.py` — 图像增强、PaddleOCR、CLIP 向量化
  - `vectorizer.py` — BGE 文本向量化、CLIP 图像向量化、Qdrant/ES 写入
  - `scheduler.py` — Airflow 风格调度（增量更新/全量重建/版本滚动/过期归档）
  - `feedback_loop.py` — RRF 权重优化、Evidence Gate 阈值调整、A/B 实验分析
  - `finetune_qlora.py` — 可选 QLoRA 微调管线

注意：首次部署需先下载模型权重（PaddleOCR、CLIP-ViT、BGE 等）至 `models/` 目录。

## 2. 当前可以确认的数据约束

### 2.1 向量与索引配置

`config.json` 中当前约定：

- 文本向量集合：`rag_text_768`
- 图像向量集合：`rag_image_512`
- Qdrant 主机：`qdrant:6333`
- Elasticsearch 索引：`cosmetics_docs`
- Elasticsearch 安全：v2.5.0 起启用 `xpack.security`，需通过环境变量 `ELASTICSEARCH_USERNAME` / `ELASTICSEARCH_PASSWORD` 配置凭据

### 2.2 权限模型

后端使用 `role_mask + dept_mask` 控制文档访问。

默认角色：

| 角色 | bit |
|------|-----|
| `admin` | `0x7FFFFFFF` |
| `rd` | `0x01` |
| `quality` | `0x02` |
| `regulation` | `0x04` |
| `sales` | `0x08` |

默认部门：

| 部门 | bit |
|------|-----|
| `rd_dept` | `0x01` |
| `quality_dept` | `0x02` |
| `regulation_dept` | `0x04` |
| `sales_dept` | `0x08` |

`config.json.permission_rules.rules` 仍然是当前仓库里最接近真实导入规则的来源。

### 2.3 文档状态

媒体访问接口 `/api/media/{doc_id}` 当前会校验：

- `doc_id`
- `role_mask`
- `dept_mask`
- `status`

其中：

- `status=active` 才允许取预签名链接
- `status=archived` 会被当作不可访问文档

## 3. 当前可执行的离线导入动作

以下命令对应离线管线入口，已通过 `run_offline.py` 和 `offline/` 模块实现：

### 3.1 创建集合与索引（首次部署）

```bash
python3 run_offline.py --mode create-index
```

此命令创建 Qdrant Collection（`rag_text_768`、`rag_image_512`）和 ES 索引（`cosmetics_docs`）。

### 3.2 增量更新

```bash
python3 run_offline.py --mode incremental
```

基于文件 mtime+md5 指纹检测新增/修改的文档（v2.3.0），仅处理发生变化的部分，支持 `--force-full` 强制全量处理。

### 3.3 全量重建

```bash
python3 run_offline.py --mode full
```

清空现有 Collection 后重新处理所有文档，适用于月度重建。

### 3.4 反馈闭环

```bash
python3 run_offline.py --mode feedback          # RRF 权重与 Evidence Gate 阈值优化
python3 run_offline.py --mode rewrite-feedback  # Query Rewrite 反馈闭环
```

## 4. 如果现在要做数据管理

### 方案 A：使用已存在的外部知识库

适用场景：

- 已经有可用的 Qdrant / Elasticsearch 数据
- 当前目标是联调在线问答，而不是补离线管线

建议动作：

1. 确认 `config.json` 中集合名、索引名、主机地址与现网一致。
2. 抽样验证 `doc_id / role_mask / dept_mask / status` 元数据是否齐全。
3. 用管理员账号和不同角色账号分别验证文档访问边界。

### 方案 B：使用本仓库离线管线

本仓库已提供完整离线管线实现：

1. 下载模型权重（PaddleOCR、CLIP-ViT、BGE 等）至 `models/` 目录
2. 准备原始文档至 `data/` 目录
3. `python3 run_offline.py --mode create-index`
4. `python3 run_offline.py --mode incremental`

注意：部分模型（PaddleOCR）依赖 `requirements.txt` 之外的可选依赖，需单独安装。图像处理管线需要 GPU 支持以加速 CLIP 向量化。

### 5. 离线质量评估

数据集管理员还可以使用 RAGAS 框架进行离线质量评估：

```bash
# 验证黄金数据集格式
python -m tests.evaluation.validate_golden_set --dataset tests/evaluation/golden_set.jsonl

# 运行黄金数据集评估
python -m tests.evaluation.ragas_eval --dataset tests/evaluation/golden_set.jsonl --tag baseline

# 查看最新报告
ls -la data/eval/reports/
```

详细评估指南见 [`docs/ragas-evaluation-guide.md`](ragas-evaluation-guide.md)。
