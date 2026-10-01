# 化妆品行业 RAG 问答系统数据管理手册

## 当前能力边界

在线检索代码会读取 Qdrant 和搜索服务中的数据。当前 Phase 1 只支持 UTF-8 TXT → 确定性字符切块 → BGE 文本 embedding adapter → Qdrant text collection；真实 BGE 模型 smoke test 尚未验证。PDF/DOCX/XLSX、OCR、CLIP、Elasticsearch 写入、调度、完整重建和反馈闭环尚未实现。完整后续范围见 [Issue #2](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/2)。

`run_offline.py ingest-text SOURCE --role-mask N --dept-mask N --epoch EPOCH` 是当前唯一支持的文档 ingestion 命令。它要求显式给出权限掩码和知识版本 epoch；掩码必须是 uint32。公开内容使用两个掩码 `0`，受限内容使用对应的非零角色和部门掩码。命令使用 `config.json` 中的 BGE 模型路径、768 维度、`rag_text_768` collection 和 Qdrant 连接配置。

示例：

```bash
python3 run_offline.py ingest-text ./data/public-guide.txt --role-mask 0 --dept-mask 0 --epoch default
```

该切片按 UTF-8 读取（接受 BOM），按字符窗口切块，并使用稳定 point ID 重复 upsert。内容变化会改变内容摘要和 chunk ID；同一来源和 epoch 的替换由 Redis 锁串行化，旧点先标记为 archived，再写入新点并清除旧 ID，空文件会清除现存点。每条 payload 都包含 `role_mask`、`dept_mask`、`status=active` 和 `doc_version_epoch`；在线 Qdrant 过滤会匹配 active 状态和当前 epoch，RBAC 仍由在线授权路径检查。CI 集成测试使用本地内存 Qdrant 与确定性测试 embedder，不会下载 BGE。

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
