# README 演示图 · 生成说明

`docs/assets/demo-request-evidence-flow.webp` 是 README 里那一张演示图的来源。它由脚本渲染，**不是**手工画的图，也不是任何生产环境的抓屏。

## 这张图里有什么

| 区域 | 内容 | 性质 |
|---|---|---|
| 左侧 | 用户提问 → 带〔证据N〕引用的回答 → 可点击的引用证据标签 | **真实渲染**：Chromium 打开 `frontend/src/App.jsx`，真实输入、真实点击 |
| 左侧第二问 | 切换身份后同一问题 → 权限过滤后证据为空 → 拒答 | **真实渲染**：走前端 dev-mode 的 `X-Role-Mask` / `X-Dept-Mask` 请求头 |
| 右上 ④ | 命中的来源文档与被引用条款（高亮行），含各文档的 `role_mask` / `dept_mask` | 演示标注，数据来自合成语料 |
| 右下 ⑤ | 身份解析 / 存储侧下推 / 二次过滤 / L2 隔离 / 双 Gate / 审计 / 证据信任边界 | 演示标注，每行标注仓库中真实实现位置 |

## 演示身份的权限对照

演示只有两个身份，都是前端 `role_options` 里的真实条目：

| 身份 | role_mask | dept_mask | 两份文档（均为 4/4） |
|---|---|---|---|
| `Regulatory Affairs`（第一问，UI 默认） | `0x04` | `0x04` | 全部可读 → 带引用回答 |
| `Commercial Team`（第二问，截图结束时所选） | `0x08` | `0x08` | 全部不可读 → 拒答 |

这张表由 `tests/test_demo_corpus_rbac_consistency.py` 强制：语料里每份文档的掩码都用**真实的 `common.auth.is_allowed`** 校验，`mock_api.py` 的逐文档过滤结果也按同一谓词核对。若有人把某份文档的掩码改成第一问身份读不到，这张图连同测试一起会失败——图与权限语义不允许各说各话。

注意这只是**演示数据与仓库权限语义的一致性检查**。它不证明 RBAC 在真实 Qdrant / Elasticsearch / Redis 部署上生效；那需要另外的运行时验证，README 的证据等级表里也没有把演示图算作运行时证据。

## 数据边界（重要）

* 所有内容来自 [`synthetic_corpus.json`](synthetic_corpus.json)：**虚构文档号**（`demo_reg_014`）、**占位 CAS**（`000-00-0`）、**杜撰标准名**（`DEMO-STD-001`）。
* 不含真实法规结论、上一家公司语料、生产日志、监控数据、凭据、密钥或流量数字。
* 图中延迟（812ms / 215ms）、Evidence Gate 得分（0.812）、缓存与掩码值都是**合成示例**，用于说明链路，**不是**测量结果，也不能用来评价系统性能。掩码值虽然与 `config.json` 的 `rbac` 一致（测试会核对），但它们描述的是**合成文档**的权限，不是任何真实语料的权限。
* 右上角 `SYNTHETIC DEMO` 标记、前端副标题里的 `DEMO · 合成数据演示环境 · 非生产`、以及回答正文里的 `(DEMO：…)` 都会出现在成图里，删掉标记等于伪造，脚本不接受这种输入。

## 生成方式

```bash
python3 docs/demo/capture_demo.py                 # 写出默认路径
python3 docs/demo/capture_demo.py --quality 68    # 更小体积
python3 docs/demo/capture_demo.py --no-font-download   # 用系统 CJK 字体
```

依赖：

* `playwright` + Chromium（`playwright install chromium`）
* `node` / `npm`，且 `frontend/node_modules` 已安装（脚本用 Vite dev server 提供真实前端）
* `pillow`（可选，用于转 WebP；缺失时退化为 PNG 并给出提示）
* 网络：仅首次需要，用于取一份**按本次字符集裁剪**的 Noto Sans SC 子集，缓存在 `docs/demo/.build/`（已 gitignore）。若容器内已装 CJK 字体，可用 `--no-font-download`。

脚本会自己拉起 `docs/demo/mock_api.py` 与 Vite dev server，结束后一并关闭；中间产物（HTML、PNG、字体子集、日志）都留在 `docs/demo/.build/`，不进版本库。仓库里只保留最终那一张 WebP。

## 为什么用 mock 而不是真后端

真链路需要 Qdrant + Elasticsearch + Redis + vLLM 权重，而这些**不在本仓库**（见 README 的证据等级说明）。用 mock 渲染真实前端，可以诚实地声称"界面是真的、数据是合成的"，而不会像生产抓屏那样暗示这套依赖已经在这里跑通。

`docs/demo/mock_api.py` 只实现演示需要的端点（`/api/auth/metadata`、`/api/query`、`/api/chat`、`/api/dialog_history`、`/api/stats`、`/api/media/{doc_id}`），响应结构对齐 `api/models.py` 里的 `QueryResponse` / `ChatResponse` / `StatsResponse`。它是演示夹具，**不属于请求路径，任何部署都不应启动它**。

其中 `/api/query` 的证据集按 `common.auth.is_allowed` 的语义**逐份文档**过滤（`authorized_doc_ids()`），而不是按角色整体放行或整体拒绝——一个身份如果只能读其中一份，就必须只拿到那一份。该谓词在这个文件里是重写的（夹具要保持纯标准库、不依赖服务依赖），由上面那个测试与真实实现逐对核对，因此不会各自漂移。
