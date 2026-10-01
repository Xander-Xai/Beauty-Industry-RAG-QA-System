# 化妆品行业 RAG 问答系统数据管理手册

## 当前能力边界

在线检索代码会读取已存在的向量/搜索服务数据。仓库目前没有生产级原始文档导入管线：`offline/` 仅包含 QLoRA 微调脚本、样本数据和独立依赖；PDF/DOCX/XLSX/TXT 解析、OCR 导入、BGE/CLIP 向量写入、增量调度和反馈闭环尚未包含。完整后续范围见 [Issue #2](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/2)。

`run_offline.py --mode create-index|incremental|full|feedback` 会给出“Offline ingestion pipeline is not currently included in this repository.”并以非零状态退出。这些命令不是可用的数据管理操作。

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

## 未来 ingestion 验收范围

文档解析、图像/OCR、BGE/CLIP embeddings、Qdrant/Elasticsearch writers、增量状态、调度、建索引/全量重建、反馈闭环及相应测试和评估均在 [独立 feature issue](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/2) 中跟踪。
