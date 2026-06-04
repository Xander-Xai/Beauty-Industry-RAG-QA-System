# 化妆品 RAG 系统 — 上线前行动计划

> 本文档汇总上线前所有待办事项，按优先级和依赖关系排列。

---

## 第一阶段：硬件与环境（第 1 周）

### 1.1 GPU 服务器就绪

- [ ] 确定 GPU 方案（双卡推荐 A30/A10，单卡可用 4090/3090）
- [ ] 安装 NVIDIA 驱动（>= 535）
- [ ] 安装 Docker + Docker Compose
- [ ] 安装 nvidia-container-toolkit
- [ ] 验证：`docker run --gpus all nvidia/cuda:12.0-base nvidia-smi`

### 1.2 模型下载

- [ ] 下载 Qwen3-14B-Instruct → `models/qwen3-14b/`
- [ ] 下载 Qwen3-4B-Instruct → `models/qwen3-4b/`
- [ ] 下载 bge-base-zh-v1.5 → `models/bge-base-zh-v1.5/`
- [ ] 下载 clip-vit-base-patch16 → `models/clip-vit-base-patch16/`
- [ ] 下载 BLIP image captioning → `models/blip-image-captioning-large/`
- [ ] 下载 TinyBERT (复杂度评估) → `models/tinybert/`
- [ ] 下载 ms-marco-TinyBERT (Rerank) → `models/ms-marco-TinyBERT-L-2-v2/`
- [ ] 验证：`ls models/` 确认所有目录存在

### 1.3 环境配置

- [ ] `cp .env.example .env`
- [ ] 编辑 `.env`：设置 `MODEL_DIR`、`DEPLOYMENT_MODE`
- [ ] 生成 JWT 密钥：`mkdir -p keys && python3 -c "from auth.jwt_auth import generate_keypair; generate_keypair('./keys')"`
- [ ] 验证：`python3 -c "from app import create_app; app = create_app(); print('OK')"`

---

## 第二阶段：系统启动与验证（第 1-2 周）

### 2.1 启动全栈

- [ ] 双卡模式：`DEPLOYMENT_MODE=production ./scripts/start.sh`
- [ ] 或单卡模式：`DEPLOYMENT_MODE=testing ./scripts/start.sh`
- [ ] 验证健康检查：`curl http://localhost:8000/api/health`
- [ ] 验证 API 文档：打开 `http://localhost:8000/docs`
- [ ] 验证 Web 界面：打开 `http://localhost:8000`

### 2.2 知识库初始化

- [ ] 生成 Mock 数据：`python3 -m data.mock_generator data/mock_data`
- [ ] 或导入真实数据：将文档放入 `data/documents/` 目录
- [ ] 运行离线处理：`python3 -m offline.scheduler --mode incremental`
- [ ] 验证向量库：检查 Milvus 中 `rag_text_768` 和 `rag_image_512` collection 有数据

### 2.3 基础功能验证

- [ ] 单轮问答：`curl -X POST http://localhost:8000/api/query -H "Content-Type: application/json" -d '{"query": "烟酰胺的安全浓度是多少？"}'`
- [ ] 多轮对话：使用 Web 界面进行多轮追问
- [ ] 权限控制：切换不同角色验证文档访问范围
- [ ] 缓存命中：重复查询验证 L2 缓存命中

---

## 第三阶段：数据导入（第 2 周）

### 3.1 真实业务数据

- [ ] 收集企业内部文档（PDF/Word/Excel）
- [ ] 整理成分数据（INCI 名称、CAS 号、安全信息）
- [ ] 整理配方数据（配方名称、成分列表、百分比）
- [ ] 收集法规文档（GB/T、QB/T、NMPA 公告等）
- [ ] 收集产品包装图片

### 3.2 数据处理

- [ ] 为每个文档配置权限（role_mask, dept_mask）
- [ ] 运行增量导入：`python3 -m offline.scheduler --mode incremental`
- [ ] 验证导入结果：`curl http://localhost:8000/api/stats`
- [ ] 抽样检查检索质量：手动查询 10 个典型问题

### 3.3 数据质量评估

- [ ] 准备 50 条标注测试集（覆盖成分/法规/配方/图像场景）
- [ ] 逐条评估回答质量（正确性、完整性、引用准确性）
- [ ] 记录低质量回答，分析原因
- [ ] 根据评估结果调整 RRF 权重或 Evidence Gate 阈值

---

## 第四阶段：压测与调优（第 3 周）

### 4.1 Locust 压测

- [ ] 安装：`pip install locust`
- [ ] 常态负载：`locust -f tests/load/locustfile.py --host http://localhost:8000 --headless -u 20 -r 2 --run-time 5m`
- [ ] 峰值压力：`locust -f tests/load/locustfile.py --host http://localhost:8000 --headless -u 40 -r 5 --run-time 3m`
- [ ] 记录 QPS、P95 延迟、错误率
- [ ] 目标：双卡 QPS >= 12, P95 <= 2s

### 4.2 故障演练

- [ ] `./scripts/fault-injection.sh redis-down`
- [ ] `./scripts/fault-injection.sh milvus-down`
- [ ] `./scripts/fault-injection.sh es-down`
- [ ] `./scripts/fault-injection.sh query-test`
- [ ] 或一键全部：`./scripts/fault-injection.sh all`
- [ ] 验证所有降级路径正常工作

### 4.3 性能调优

- [ ] KV Cache：调整 `gpu_memory_utilization`（0.80-0.90）
- [ ] KV Cache：调整 `safety_factor`（0.65-0.75）
- [ ] RRF 权重：`w_text` ∈ {0.5, 0.7, 1.0}, `w_clip` ∈ {0.3, 0.5, 1.0, 2.0}
- [ ] Evidence Gate：调整 `w1-w4` 权重和阈值（0.55/0.75）
- [ ] Rerank Batch：调整窗口时间（10-30ms）和 batch size（32-64）
- [ ] 记录调优前后的性能对比

---

## 第五阶段：生产化（第 4 周）

### 5.1 监控与告警

- [ ] 部署 Prometheus：配置 scrape 目标
- [ ] 导入 Grafana 面板：`deploy/grafana/dashboards/rag-overview.json`
- [ ] 配置告警规则：KV Pressure > 0.9, P95 > 4s, 错误率 > 5%
- [ ] 对接通知渠道：钉钉/飞书/邮件
- [ ] 验证告警触发和恢复

### 5.2 安全部署

- [ ] 生成 TLS 证书（Let's Encrypt 或内部 CA）
- [ ] 配置 Nginx：`nginx/nginx.conf`
- [ ] 验证 HTTPS 访问
- [ ] 验证 CORS、安全头、限流生效
- [ ] 验证 JWT 认证流程（登录→获取 token→使用 token 访问 API）

### 5.3 用户管理

- [ ] 创建管理员账号
- [ ] 为各部门创建用户账号
- [ ] 分配角色和部门权限
- [ ] 验证不同角色的文档访问范围

### 5.4 文档与培训

- [ ] 审阅部署手册：`docs/deployment-guide.md`
- [ ] 审阅用户手册：`docs/user-guide.md`
- [ ] 审阅运维手册：`docs/operations-guide.md`
- [ ] 审阅数据管理员手册：`docs/data-admin-guide.md`
- [ ] 组织用户培训（演示 Web 界面使用）
- [ ] 组织运维培训（监控、告警、故障处理）

---

## 第六阶段：上线（第 4 周末）

### 6.1 上线 Checklist

- [ ] 压测通过（双卡 QPS >= 12, P95 <= 2s）
- [ ] 所有降级路径验证通过
- [ ] 故障演练完成，无 P0 遗留
- [ ] 安全审计通过（JWT/审计/脱敏/HTTPS）
- [ ] 数据导入完成，知识库覆盖核心场景
- [ ] 监控面板就绪，告警通知对接
- [ ] 回滚方案文档化，可在 5 分钟内回滚
- [ ] 操作手册就绪，运维人员培训完成
- [ ] 用户验收测试通过（至少 5 名目标用户）
- [ ] 备份策略就绪（Milvus/Redis/MinIO 定时备份）

### 6.2 回滚方案

```bash
# 快速回滚：停止新版本，启动旧版本
./scripts/stop.sh
git checkout <上一个稳定 tag>
DEPLOYMENT_MODE=production ./scripts/start.sh
```

### 6.3 上线后监控

- [ ] 上线后 1 小时内持续监控 QPS、延迟、错误率
- [ ] 上线后 24 小时内收集用户反馈
- [ ] 上线后 1 周内完成首轮性能调优
