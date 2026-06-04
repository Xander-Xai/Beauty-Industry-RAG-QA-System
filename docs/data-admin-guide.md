# 化妆品 RAG 系统 — 数据管理员手册

## 1. 文档导入流程

### 支持格式

| 格式 | 扩展名 | 处理方式 |
|------|--------|----------|
| PDF | .pdf | PyMuPDF 文本提取 |
| Word | .docx | python-docx 解析 |
| Excel | .xlsx | pandas 读取 |
| 文本 | .txt | 直接读取 |
| 图片 | .jpg/.png | PaddleOCR / OpenCV |

### 导入步骤

```bash
# 1. 将文档放入 data/documents/ 目录
cp /path/to/docs/*.pdf data/documents/

# 2. 设置文档权限 (在 metadata 中配置)
# 每个文档需要指定: role_mask, dept_mask, doc_type

# 3. 运行离线处理
python3 -m offline.scheduler --mode incremental

# 4. 验证导入结果
curl http://localhost:8000/api/stats | python3 -m json.tool
```

### 文档分类

| doc_type | 说明 | 示例 |
|----------|------|------|
| regulation | 法规标准 | GB/T, QB/T, NMPA 公告 |
| ingredient | 成分数据 | INCI 名称、安全信息、限量 |
| formula | 配方数据 | 护肤/彩妆/洗护配方 |
| image | 图像数据 | 产品包装、标签 |

## 2. 权限配置

### 角色定义

| 角色 | bit | 可访问内容 |
|------|-----|-----------|
| admin | 0x01 | 所有文档 |
| rd | 0x02 | 配方、成分、部分法规 |
| quality | 0x04 | 检测标准、质量规范 |
| regulation | 0x08 | 法规、标准、公告 |
| sales | 0x10 | 产品信息、公开法规 |

### 部门定义

| 部门 | bit |
|------|-----|
| 研发部 | 0x01 |
| 品质部 | 0x02 |
| 法规部 | 0x04 |
| 销售部 | 0x08 |
| 市场部 | 0x10 |

### 权限示例

- 研发配方文档: `role_mask=0x02, dept_mask=0x01` (仅研发部)
- 公开法规文档: `role_mask=0, dept_mask=0` (所有人)
- 跨部门品质文档: `role_mask=0x04, dept_mask=0x06` (品质部+法规部)

## 3. 版本管理

### 文档版本 epoch

系统使用 `doc_version_epoch` 管理文档生命周期：

- 新导入文档: `doc_version_epoch = 当前日期_批次`
- 过期文档: 标记为 `status = "archived"`
- 版本切换: 更新全局 epoch，旧版本缓存自动失效

```bash
# 查看当前版本
grep knowledge_version_epoch config.json

# 手动切换版本 (触发缓存失效)
# 更新 config.json 中的 knowledge_version_epoch
```

### 文档更新流程

1. 将新版本文档放入 data/documents/
2. 运行增量导入: `python3 -m offline.scheduler --mode incremental`
3. 旧文档自动归档
4. 缓存通过版本 epoch 切换自动失效

## 4. 知识库维护

### 每日任务

- 检查导入日志是否有错误
- 验证新文档是否被正确索引
- 检查 Milvus/ES 索引大小

### 每周任务

- 审查低质量检索结果
- 根据用户反馈调整 RRF 权重
- 清理过期或无效文档

### 每月任务

- 全量重建索引 (可选)
- 评估检索质量指标
- 更新法规文档（新规发布时）
