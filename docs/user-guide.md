# 化妆品行业 RAG 问答系统用户手册

## 1. 当前页面能做什么

前端当前已经和后端对齐的能力有：

- 用户登录
- Token 自动刷新
- 单轮查询
- 多轮问答
- 会话历史查看
- 系统统计查看
- 证据文档打开
- 管理员创建用户
- 管理员更新用户角色和部门

## 2. 访问系统

默认入口：

- 页面：`http://localhost:8000/`
- API 文档：`http://localhost:8000/docs`

如果前端单独开发运行：

```bash
cd frontend
npm install
npm run dev
```

开发代理默认把 `/api` 转发到 `http://localhost:8000`。

## 3. 登录与身份

### 生产模式

- 使用账号密码登录（v2.5.0 起使用 RS256 签名的 JWT Token）
- 登录成功后，浏览器会保存后端签发的 Access Token（15分钟） / Refresh Token（7天）
- 页面会在 Token 过期前自动尝试刷新
- `/api/stats` 和 `/api/metrics` 现在需要 JWT 认证，浏览器面板通过已登录 Token 自动授权

### 开发模式

当 `AUTH_DEV_MODE=true` 或 `config.json.auth.dev_mode=true` 时：

- 页面会显示角色选择器
- 浏览器通过 `X-User-ID / X-Role-Mask / X-Dept-Mask` 模拟身份
- 当前默认开发角色已降为 `public`，不会再直接以管理员身份进入页面

这只适合本地联调，不适合真实上线。

## 4. 提问方式

1. 在底部输入框输入问题
2. 点击 `Send` 或按 `Enter`
3. 选择模式：
   - `Multi-turn Chat` -> `POST /api/chat`
   - `Single Query` -> `POST /api/query`
4. 返回答案后，页面会显示：
   - 业务类型
   - 意图
   - 响应耗时
   - 是否命中缓存
   - 证据文档列表

浏览器会持久化 `session_id`，同一浏览器下刷新页面后，会继续使用同一个会话标识。

页面里的辅助面板：

- `Session`：调用 `GET /api/dialog_history` 查看当前会话轮次和锁定证据
- `Stats`：调用 `GET /api/stats` 查看缓存命中率、KV 压力、阶段延迟

## 5. 证据文档

回答下方的证据标签现在会先请求 `/api/media/{doc_id}`，再打开后端返回的预签名链接。

这意味着：

- 有权限的用户可以直接打开文档
- 无权限或文档已归档时，页面会显示错误
- 不再依赖未鉴权的裸链接跳转

## 6. 管理员功能

管理员面板现在支持：

- 列出用户
- 创建用户
- 为每个用户勾选角色
- 为每个用户勾选部门
- 保存角色/部门变更

管理员能力对应后端接口：

- `GET /api/auth/users`
- `POST /api/auth/users`
- `PUT /api/auth/users/{user_id}/roles`

## 7. 离线质量评估

系统已集成 RAGAS (Retrieval Augmented Generation Assessment) 框架，用于离线评估 RAG 管线的质量：

- **黄金数据集**：`tests/evaluation/golden_set.jsonl`（27 条，覆盖 5 种业务类型、3 个难度级别）
- **评估指标**：Faithfulness、Answer Relevancy、Context Precision、Context Recall
- **生成报告**：自动保存到 `data/eval/reports/`，支持基线对比和版本演进追踪

详细使用方法见 [`docs/ragas-evaluation-guide.md`](ragas-evaluation-guide.md)。

运行评估：
```bash
# 数据集验证
python -m tests.evaluation.validate_golden_set --dataset tests/evaluation/golden_set.jsonl

# 黄金数据集评估
python -m tests.evaluation.ragas_eval --dataset tests/evaluation/golden_set.jsonl --tag baseline

# 带 RAG 管线的端到端评估
python -m tests.evaluation.ragas_eval --pipeline --tag v1-review
```

## 8. 当前仍需注意

- `POST /api/continuation` 当前仍是占位接口，页面没有把它作为真实续写能力暴露。
- 如果系统没有可用知识库数据，问答接口仍可能返回低质量结果或空结果。
- 离线导入管线已实现（`offline/` 包），可通过 `python3 run_offline.py --mode incremental` 导入文档数据。运行前需先下载 PaddleOCR、CLIP、BGE 等模型权重。
- RAGAS 端到端评估需所有基础设施（vLLM、Qdrant、ES、Redis）就绪后方可运行。
