# 化妆品行业 RAG 问答系统数据管理手册

本手册描述离线知识库管线的实际操作。实现证据与测试证据分离列于
[Repository Truth Audit](repository-truth-audit.md)；设计目标见 [PRD](../PRD.md)。

## 1. 能力与边界

**已在代码与确定性测试中实现：** 多格式解析（TXT/PDF/DOCX/XLSX/图片）、扫描页 OCR 路由、
确定性字符切块、稳定逻辑身份、BGE/CLIP embedding adapter、Qdrant 文本与图像 writer、
Elasticsearch writer、内容哈希增量检测、snapshot carry-forward、全量重建、快照校验、
epoch 封存、调度抽象与反馈导出。

**需要外部运行时 / 资产：** 真实 BGE 模型、真实 CLIP 模型（视觉检索启用时）、
PaddleOCR/PaddlePaddle（需要 OCR 时，见 `offline/requirements-ocr.txt`）、Airflow（可选）。

**尚未作为生产结果验证：** 真实模型质量、生产延迟/QPS、大规模语料吞吐。真实 BGE/CLIP/
PaddleOCR smoke 需要本地模型资产；未执行时为 `EXTERNAL_MODEL_ASSET_REQUIRED`，
不得用确定性测试 embedder 冒充真实模型验证。

## 2. 依赖与模型资产

```bash
python3 -m pip install -r requirements.txt
# 可选：需要处理扫描件/图片时才安装 OCR 运行时
python3 -m pip install -r offline/requirements-ocr.txt
```

- 文本/图像模型路径来自 `config.json` 的 `embedding.text.model_path` 与
  `embedding.image_clip.model_path`。仓库不随附这些权重。
- `transformers` / `torch` 在 `requirements.txt` 中；模型在首次使用时懒加载，不在 import
  阶段下载或联网。
- 真实 BGE smoke：`python3 scripts/smoke_bge_ingestion.py`（缺少模型资产时退出码 3）。

## 3. 数据目录与文档身份

- `knowledge_base.data_dir`（默认 `./data`）是源文档根目录。
- 默认逻辑身份 `source_id` = 文件相对 `data_dir` 的 POSIX 路径，因此同一目录树挂载到不同
  根目录会得到相同 `doc_id`。
- SOURCE 必须位于 `data_dir` 内；外部路径必须显式提供稳定的 `--source-id`（例如业务文档键）。
  文件移动时继续使用同一 source ID。
- `knowledge_base.supported_extensions` 是可选白名单，会在启动扫描时对照解析器支持集合校验；
  配置了不受支持的扩展名会直接报错，而不会被静默声明为已支持。

## 4. 权限规则

- `permission_rules.rules` 按路径 glob 先匹配先命中，返回 `role_mask` / `dept_mask`。
- 未命中时使用 `default_role_mask` / `default_dept_mask`；缺少默认值会 fail closed（报错）。
- 单源 `ingest` 必须显式给出 `--role-mask` 与 `--dept-mask`；掩码必须是 uint32。缺少或非法掩码
  一律拒绝，绝不会默认公开。
- 公开内容使用两个掩码 `0`；受限内容使用对应非零角色/部门掩码。

## 5. 创建索引

```bash
python3 run_offline.py create-index
```

确保 Qdrant 文本 collection、Qdrant 图像 collection 与 Elasticsearch index 存在并校验维度/
距离/mapping。默认 fail-safe，不删除既有数据。

```bash
python3 run_offline.py create-index --recreate --yes
```

`--recreate` 会删除并重建 Qdrant 文本/图像 collection 与 Elasticsearch index；
缺少 `--yes` 时拒绝执行。这是破坏性操作。

## 6. 单源导入

```bash
python3 run_offline.py ingest ./data/public-guide.txt \
  --role-mask 0 --dept-mask 0 --epoch phase_1
```

- 格式由扩展名自动识别；图片走 OCR/CLIP 路径。
- 导入写入的是**未封存的 staging epoch**，可反复替换同一文档。
- `ingest-text` 是保留的向后兼容 TXT 专用子命令（`python3 run_offline.py ingest-text ...`）。

## 7. 全量重建

```bash
python3 run_offline.py full-rebuild --epoch phase_2
# 可选：校验通过后直接封存
python3 run_offline.py full-rebuild --epoch phase_2 --seal
```

处理 `data_dir` 下所有受支持源，写入新 epoch。**不会自动激活**新 epoch。

## 8. 增量构建

```bash
python3 run_offline.py incremental-build --from-epoch phase_1 --to-epoch phase_2
```

- 变更检测以**内容哈希为准**；`file_size`/`mtime_ns` 仅用于记录，不用于跳过哈希比较。
  即使大小与 mtime 不变但内容变化，也会判定为 MODIFIED。
- 未变化文档从 `--from-epoch` **carry forward** 到 `--to-epoch`（复制 Qdrant 文本/图像点与 ES
  文档，改写 `doc_version_epoch` 与物理 ID，保持逻辑 ID 不变）。
- 修改/新增文档重新处理，删除文档不出现在目标 epoch。
- 目标 epoch 是一份**完整快照**，而非仅包含变更文件。
- 若源 epoch 的 `embedding_version` 与当前构建契约不一致，carry-forward 会拒绝并要求执行
  全量重建（模型权重或预处理变化必须进入新 epoch）。

## 9. 校验与封存

```bash
python3 run_offline.py seal-epoch --epoch phase_2
```

`seal-epoch` 默认先运行快照校验（Qdrant 文本、Qdrant 图像、Elasticsearch、RBAC 元数据、
epoch、embedding version、重复逻辑 ID、孤儿图像），任何错误都会阻止封存。

```bash
python3 run_offline.py seal-epoch --epoch phase_2 --skip-validation
```

`--skip-validation` 是明确的危险逃生口，会跳过校验并打印警告；正常路径不应使用。

## 10. epoch 的三种状态与激活

- **staging epoch**：未封存、可变。同 epoch 文档替换会先归档旧点再写入新点。
- **sealed epoch**：已封存、不可变。不能再新增/修改/删除文档。
- **active epoch**：`config.json` 的 `knowledge_version_epoch` 指向的 epoch，在线检索只读取它。

激活是明确的人工动作：封存并核对完整后，修改 `config.json` 的 `knowledge_version_epoch`
为新 epoch 并重启在线服务。调度器最多 build/validate/seal，**不会**自动切换生产 epoch。

回滚：把 `knowledge_version_epoch` 切回旧 epoch 并重启。旧 epoch 点保留，因此可回切。

## 11. 状态数据库

- 位置：`knowledge_base.state_db_path`（默认 `./data/offline_state.sqlite3`）。
- 记录 `source_id`、相对路径、大小、mtime、内容哈希、文档类型、最近成功 epoch、状态。
- 使用 SQLite 事务，崩溃安全；测试使用临时数据库。运行库不提交到仓库（`.gitignore`）。

## 12. embedding version 契约

- 每个 point 记录 `embedding_version`（模型 revision + 预处理契约）。
- 每个 epoch 通过持久 manifest 固定为一个 embedding version；同 epoch 混入不同版本会被拒绝。
- 模型权重、tokenizer 或 pooling/归一化契约变化时，必须提升 revision 并构建新 epoch。
- 文本契约：attention-mask-aware mean pooling；图像契约：CLIP `get_image_features` + L2 归一化。

## 13. legacy default epoch

升级前写入、缺少 `doc_version_epoch` 的旧文本/图像点在 `default` epoch 下仍可检索；
一旦 active epoch 不是 `default`，这些 legacy 点不会被匹配。新写入的点始终带
`doc_version_epoch`，因此图像检索与文本检索一样遵循 active epoch。

## 14. 文件锁

- 相同文档/epoch 的替换由 OS 咨询锁串行化；封存使用排他锁等待在途写入完成。
- 锁实现跨平台：POSIX 使用 `fcntl`，Windows 使用 `msvcrt`。
- 默认锁目录在本机临时目录；容器/进程使用独立临时目录时，用 `OFFLINE_INGESTION_LOCK_DIR`
  指向共享的、仅当前用户可访问的挂载目录。锁不依赖可淘汰的 Redis。

## 15. 调度（可选）

- 业务逻辑位于 `offline/scheduler.py`，cron、Airflow 与 CLI 共用。
- 频率来自 `config.json` 的 `offline.scheduler`（`incremental_cron`、`full_rebuild_cron`、
  `incremental_enabled`、`full_rebuild_enabled`、`auto_seal`）。
- Airflow DAG 位于 `dags/knowledge_base_dags.py`，仅在 Airflow 已安装且离线模块可发现时注册。
  默认 `docker compose` 不启动 Airflow。
- `auto_seal` 默认 `false`；调度器不会激活 epoch。

## 16. 反馈审核与导出

- 统一反馈存储：`offline.feedback.store_path`（默认 `./data/feedback/feedback.sqlite3`）。
- 反馈记录带 `review_status`（`pending`/`accepted`/`rejected`）。用户反馈不是自动 ground truth。
- 仅 `accepted` 记录进入 hard-negative / rewrite-correction / evaluation / QLoRA-DPO 候选导出；
  `pending` 记录进入人工审核队列。
- 复用现有 rewrite 反馈日志，不重复造第二套系统。

## 17. 故障排查

| 现象 | 检查 |
|---|---|
| `failed to initialize BGE/CLIP model` | 模型资产是否存在；`embedding.*.model_path` 是否正确 |
| OCR 报 `failed to initialize PaddleOCR` | 安装 `offline/requirements-ocr.txt` |
| Qdrant `dimension does not match` | collection 维度与 `embedding.*.dimension` 是否一致；必要时 `create-index --recreate --yes` |
| ES `field ... must be ...` | index mapping 与 `offline/elasticsearch_writer.py` 不一致；重建 index |
| `embedding version changed within epoch` | 模型/预处理变化；构建新 epoch 或全量重建 |
| `knowledge epoch ... is sealed` | 该 epoch 已封存；写入新 epoch |
| `source is outside the configured data root` | 移动 SOURCE 到 `data_dir` 或提供 `--source-id` |
| `role_mask must be an integer uint32` | 显式提供合法掩码 |
| carry-forward `IncompatibleEmbeddingVersion` | 执行 `full-rebuild` 而非增量 |
| 缺少默认权限 | 配置 `permission_rules.default_role_mask` / `default_dept_mask` |

## 18. 微调数据

`offline/finetune_data.json` 是 QLoRA 脚本的示例训练数据；`offline/requirements-finetune.txt`
是可选依赖。示例文件不代表企业知识库数据集，也不代表已经生成了模型 adapter。微调脚本不能
替代文档 ingestion。
