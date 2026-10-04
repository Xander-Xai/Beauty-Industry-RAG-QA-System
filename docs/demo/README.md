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
python3 docs/demo/capture_demo.py --no-font-download   # 完全离线：用系统 CJK 字体
```

依赖：

* `playwright` + Chromium（`playwright install chromium`）
* `node` / `npm`，且 `frontend/node_modules` 已安装（脚本用 Vite dev server 提供真实前端）
* `pillow`（可选，仅用于转 WebP；缺失时写出同名 `.png`，见下节）
* 网络：**只有默认模式需要**，且仅首次——用于取一份按本次字符集裁剪的 Noto Sans SC 子集，缓存在 `docs/demo/.build/`（已 gitignore）。

## 输出格式：committed 的是 WebP，本地可能是 PNG

| | 装了 `pillow` | 没装 `pillow` |
|---|---|---|
| 实际写出的文件 | `--out` 指定的 `.webp` | 同目录的同名 `.png`（`hero.webp` → `hero.png`） |
| 体积 | 约 150–250 KiB | 约 1 MB |
| 定位 | 仓库里 committed 的那一张 | 仅供本地核对，不提交 |

README 引用的 hero 图是 **WebP**，仓库里也只有这一张图。没有 `pillow` 时脚本**不做**转码，而是把 PNG 写到 `--out` 的兄弟路径上，并在 stderr 提示一次。它**不会**把 PNG 字节写进 `.webp` 文件名：那样的文件在 git、Markdown 渲染器和下一次复算眼里都是一张“正常”的图，只有真正去解码它的时候才会炸。

脚本按**实际写出的那个文件**回报——体积、路径和格式标签都取自它，而不是取自 `--out`。早期版本固定 `stat --out`，于是在没有 `pillow` 的机器上整轮渲染全部跑完，却在最后一步去 stat 一个从未生成的文件并以 `FileNotFoundError` 退出：一个可选依赖把一条本来能跑通的命令变成了必然失败。

这条契约由 `tests/test_demo_capture_pillow_fallback.py` 固定：它断言缺 `pillow` 时写出的是兄弟 `.png`、`.webp` 不存在、脚本按 PNG 回报，并且 PNG 字节没有被写进 `.webp` 文件名。

## 两种字体策略

| | 默认模式 | `--no-font-download` |
|---|---|---|
| 字体来源 | 下载并内联一份按字符集裁剪的 Noto Sans SC 子集 | 只用本机已安装的 CJK 字体 |
| 需要网络 | 首次需要，之后走缓存 | **完全不需要** |
| 需要 `.build/` 里的字体缓存 | 首次下载后生成 | **不需要**，也不读它 |
| 前置条件 | 无 | 本机装有 CJK 字体 |

`--no-font-download` 适用于两种情况：容器/机器没有外网，或者只想复算已经缓存过的那张图。它**不读 `docs/demo/.build/fonts/`**：那份缓存是 gitignore 的，因此在全新 clone 的仓库里该目录根本不存在——早期版本在这里要求缓存必须存在，于是这个 flag 在它唯一的存在理由（离线复算）上必然失败。现在它直接输出一套系统 CJK 字体栈交给 Chromium，按 Linux（`Noto Sans CJK SC`）、macOS（`PingFang SC`）、Windows（`Microsoft YaHei`）各列一个族，末尾保留 `sans-serif` 让 Chromium 自己做逐字回退。

该模式唯一的代价是**依赖本机字体**：机器上一个 CJK 字体都没有时，命令仍然成功，但图里的中文会变成豆腐块。这种情况下要么装 `fonts-noto-cjk`，要么用默认模式。

两条路径的分工由 `tests/test_demo_capture_offline_fonts.py` 固定：它断言离线分支在空的构建目录下即可完成整轮运行、期间零网络请求，且默认模式仍然下载并复用缓存。两者只影响字体，不影响图里的文字——同一份语料、同一套标注，换字体不会改变成图内容。

脚本会自己拉起 `docs/demo/mock_api.py` 与 Vite dev server，结束后一并关闭；中间产物（HTML、PNG、字体子集、日志）都留在 `docs/demo/.build/`，不进版本库。仓库里只保留最终那一张 WebP。

## 为什么用 mock 而不是真后端

真链路需要 Qdrant + Elasticsearch + Redis + vLLM 权重，而这些**不在本仓库**（见 README 的证据等级说明）。用 mock 渲染真实前端，可以诚实地声称"界面是真的、数据是合成的"，而不会像生产抓屏那样暗示这套依赖已经在这里跑通。

`docs/demo/mock_api.py` 只实现演示需要的端点（`/api/auth/metadata`、`/api/query`、`/api/chat`、`/api/dialog_history`、`/api/stats`、`/api/media/{doc_id}`），响应结构对齐 `api/models.py` 里的 `QueryResponse` / `ChatResponse` / `StatsResponse`。它是演示夹具，**不属于请求路径，任何部署都不应启动它**。

其中 `/api/query` 的证据集按 `common.auth.is_allowed` 的语义**逐份文档**过滤（`authorized_doc_ids()`），而不是按角色整体放行或整体拒绝——一个身份如果只能读其中一份，就必须只拿到那一份。该谓词在这个文件里是重写的（夹具要保持纯标准库、不依赖服务依赖），由上面那个测试与真实实现逐对核对，因此不会各自漂移。
