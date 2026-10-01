# 化妆品行业 RAG 问答系统数据管理手册

## 当前能力边界

在线检索代码会读取 Qdrant 和搜索服务中的数据。当前 Phase 1 只支持 UTF-8 TXT → 确定性字符切块 → BGE 文本 embedding adapter → Qdrant text collection；真实 BGE 模型 smoke test 尚未验证。PDF/DOCX/XLSX、OCR、CLIP、Elasticsearch 写入、调度、完整重建和反馈闭环尚未实现。完整后续范围见 [Issue #2](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/2)。

`run_offline.py ingest-text SOURCE --role-mask N --dept-mask N --epoch EPOCH` 是当前唯一支持的文档 ingestion 命令；`run_offline.py seal-epoch --epoch EPOCH` 用于封存完整快照。ingest 要求显式给出权限掩码和知识版本 epoch；掩码必须是 uint32。公开内容使用两个掩码 `0`，受限内容使用对应的非零角色和部门掩码。命令使用 `config.json` 中的 BGE 模型路径、768 维度和 `rag_text_768` collection；Qdrant 地址优先读取 `.env`/环境变量 `QDRANT_HOST`、`QDRANT_PORT` 和 `QDRANT_GRPC_PORT`，否则回退到 `config.json`。在线 reader 读取相同的环境覆盖后连接同一 Qdrant endpoint。Docker Compose app 服务显式使用内部主机名 `qdrant`；Compose 将 Qdrant HTTP/gRPC 端口只发布到主机回环地址，供主机 CLI 使用 `localhost`，不会监听外部网卡。

文档身份默认由配置的 `knowledge_base.data_dir` 内相对路径确定，因此在不同机器或挂载根目录下重复导入同一相对路径会得到相同 `doc_id`。SOURCE 必须位于该数据根目录下；外部路径必须显式指定稳定的 `--source-id`（例如稳定业务文档键），文件移动时继续使用同一 source ID。`source_path` 仍记录本次导入使用的绝对路径以便排查。

示例：

```bash
python3 run_offline.py ingest-text ./data/public-guide.txt --role-mask 0 --dept-mask 0 --epoch phase_1
python3 run_offline.py seal-epoch --epoch phase_1
```

默认配置中的当前 epoch 会在 ingestion service 启动时自动封存，禁止向正在提供查询服务的快照增加文档。新版本先把完整文档集都导入一个未封存的新 epoch，运行 `seal-epoch` 写入不可见于检索的 epoch manifest；核对该快照完整后，再将 `config.json` 的 `knowledge_version_epoch` 改为该 epoch 并重启在线服务。封存后该 epoch 不能新增或修改文档；更改内容、权限或删除文档都要构建并封存另一个 epoch。已封存快照可通过切回配置用于回滚。共享文件锁目录需供同时运行的写入进程可见；分离临时目录时使用私有共享挂载点设置 `OFFLINE_INGESTION_LOCK_DIR`。

该切片按 UTF-8 读取（接受 BOM），按字符窗口切块，并使用稳定 point ID 重复 upsert。为限制单文档内存用量，默认文件大小上限为 `knowledge_base.max_document_bytes`（131072 字节），最多 `knowledge_base.max_chunks`（256）个 chunk；超过任一限制会在 embedding 和 Qdrant 写入前拒绝。BGE 推理使用 `knowledge_base.embedding_batch_size`（默认 32）分批，query 与 document 都使用 attention-mask-aware mean pooling。每个 point 记录 `embedding_version`（`embedding.text.model_revision` 加 pooling contract）；模型权重或 embedding 算法变化时必须提升 revision。即使 revision 未变化，重复导入也会比较存储向量，发现同内容向量已变化时拒绝复用该 epoch 并要求新 epoch。物理 point ID 对 epoch 做版本化，因此同内容可在多个 epoch 并存。尚未封存的 staging epoch 支持同 epoch 文档替换：旧点先归档，新点写入成功后清除该文档/epoch 的过期点；空文件移除该文档在 staging epoch 的旧点，其他 epoch 不受影响。`seal-epoch` 后该 epoch 不可再变更。在线当前 epoch 会在 ingestion service 启动时自动封存，防止写入活动查询快照导致答案缓存继续返回旧内容或旧权限；所有新文档/内容/权限变化都先写入未封存 epoch，完成后封存并切换配置。旧 epoch points 保留以便回切，缓存 key 随 epoch 切换。每条新 payload 都包含 `role_mask`、`dept_mask`、`status=active` 和 `doc_version_epoch`；检索只匹配 active 状态和当前 epoch。为兼容升级前的旧文本点，缺少 epoch 字段的 active 点仅在当前 epoch 为 `default` 时作为 default 文档读取；切到其他 epoch 时这些旧点不会匹配，旧 CLIP image filter 继续独立保持兼容。RBAC 仍由在线授权路径检查。文档锁串行化相同文档/epoch 的替换；epoch 共享锁协调普通写入，封存使用排他锁等待在途写入完成。默认锁目录位于本机临时目录，容器或进程使用独立临时目录时，可用 `OFFLINE_INGESTION_LOCK_DIR` 指向共享的、权限为当前用户私有的挂载目录。锁不依赖可淘汰的 Redis cache。CI 集成测试使用本地内存 Qdrant 与确定性测试 embedder，不会下载 BGE。

## 配置来源

`config.json` 保存应用配置，包括 Qdrant、Elasticsearch、向量维度/集合和知识库目录等声明；实际连接地址和凭据需结合环境配置。修改前确认正在运行的服务拓扑与本地 `config.json` 一致。

## 使用现有外部知识库

要联调在线问答，可配置并连接已经由其他流程建立的 Qdrant/Elasticsearch 数据。建议核对：

1. 服务地址、集合名、索引名及向量维度。
2. 文档 payload 中的 `doc_id`、`role_mask`、`dept_mask` 和状态字段。
3. 管理员及不同权限用户对同一文档的访问边界。
4. 证据文件链接对应的对象存储配置及权限。

该操作不会由本仓库自动导入原始文档。

## 权限与媒体访问

应用配置中的角色/部门映射及 `config.json.permission_rules` 是权限规则的输入。`/api/media/{doc_id}` 路由对请求和文档元数据执行访问检查，并依赖对象存储配置生成访问地址。请以实际运行配置和接口行为验证权限，不要把 PRD 中的位运算示例当作数据迁移工具。

在线检索对 Qdrant 候选执行应用层角色/部门位掩码校验；Elasticsearch BM25 在查询中过滤相同权限条件，ES 降级召回复用该过滤。候选缺少有效的 `role_mask` 或 `dept_mask` 时会被丢弃，不能据此视为公开文档。请确保两类检索索引都写入这两个字段；旧记录未补齐权限元数据时会从检索结果中排除。缓存键仍按请求的角色和部门掩码隔离。

## 微调数据

`offline/finetune_data.json` 是 QLoRA 脚本的示例训练数据；`offline/requirements-finetune.txt` 是可选依赖。示例文件不代表企业知识库数据集，也不代表已经生成了模型 adapter。微调脚本不能替代文档 ingestion。

## 后续 ingestion 范围

文档解析、图像/OCR、BGE/CLIP embeddings、Qdrant/Elasticsearch writers、增量状态、调度、建索引/全量重建、反馈闭环及相应测试和评估均在 [独立 feature issue](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/2) 中跟踪。
