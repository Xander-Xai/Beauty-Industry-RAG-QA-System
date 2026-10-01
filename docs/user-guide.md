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

- 使用账号密码登录
- 登录成功后，浏览器会保存后端签发的 Access Token / Refresh Token
- 页面会自动尝试刷新过期 Token

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
- `Stats`：调用 `GET /api/stats` 查看缓存命中率、KV 压力、阶段延迟（该接口需要登录；未登录/Token 过期时会返回 401 并提示重新登录）

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

## 7. 当前仍需注意

- `POST /api/continuation` 当前仍是占位接口，页面没有把它作为真实续写能力暴露。
- 如果系统没有可用知识库数据，问答接口仍可能返回低质量结果或空结果。
- 知识库由管理员通过离线 ingestion 管线（`run_offline.py`）构建并发布；终端用户无需运行这些命令。如果系统当前没有可用的知识库快照，问答接口仍可能返回低质量或空结果。
