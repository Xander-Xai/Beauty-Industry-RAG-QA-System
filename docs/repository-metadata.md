# 期望的 GitHub 仓库 About 配置（Metadata Packet）

这份文档是**配置意图的记录**，不是产品能力声明。它回答一个问题：如果有人只看到 GitHub 仓库卡片（不打开 README），他应该得到什么信息？

配置通过 GitHub **Settings 或 API** 手工应用，没有代码路径会自动写入它，因此本文档必须能独立说明目标状态。

**两条硬规则**

1. **不设置不存在的公网 Demo。** 本仓库没有公网部署、没有演示站点、没有演示视频。任何指向不存在地址的 homepage 都是伪造证据。
2. **不把未验证的东西写进元数据。** 元数据是别人**不点进来就会读**的唯一文本，所以它必须比 README 更保守，而不是更漂亮。

---

## 1 · Repository description

GitHub 允许最多 350 字符，超过会在 UI 里被截断；仓库卡片、搜索结果、`gh repo view` 与社交分享都显示这一行。**它是唯一保证被读到的一句话**。

### 推荐值

```text
Multimodal RAG QA for cosmetics regulation: hybrid BM25 + vector recall, two-stage reranking, evidence gating that refuses rather than guesses, bitmask RBAC. FastAPI + Qdrant/Elasticsearch/Redis. Every capability is graded by evidence level.
```

239 字符，留足余量。

### 为什么这样写

| 决策 | 理由 |
|---|---|
| **先说领域与形态，再报技术栈** | 读者第一眼要判断的是"这和我的岗位有没有关系"，不是"用了什么框架"。旧值以 `Enterprise multimodal RAG QA for cosmetics knowledge:` 开头，技术枚举占了 140 字符，后半段基本是名词列表 |
| **写 `refuses rather than guesses`** | 这是本项目与"套模板 RAG"最可区分的一点，也是法务/合规类岗位真正在意的产品行为。旧值里的 `evidence gating` 只有术语没有行为 |
| **写 `graded by evidence level`** | 主动把"实现了 ≠ 验证过"摆到最前面。对招聘方这是加分信号，对技术面试官是筛掉幻觉型简历的成本最低方式 |
| **删掉 `Qwen/vLLM`** | 旧值以 `Qwen/vLLM` 结尾。当前生成拓扑是单一共享 4B 端点、复杂请求走 14B，**且该 GPU 拓扑在本仓库从未执行过**（权重缺失、`vllm` 未安装）。把模型名放进元数据会被读成"跑过这个栈"，这是本仓库最容易被误读的一处。模型名属于 README 与代码，不属于仓库卡片 |
| **不写 `enterprise` 作为卖点前缀** | "enterprise" 单独出现容易被读成营销词。真正说明企业级的是 `bitmask RBAC` 这个可验证的具体机制，所以保留机制、去掉形容词 |
| **不写任何数字** | QPS、文档数、延迟都不进 description。唯一有意义的规模信息是 `HISTORICAL_PRODUCTION` 且无法复现，写进卡片等于把它变成仓库 benchmark |

### 备选（更强调证据治理，用于你认为读者是面试官时）

```text
Enterprise multimodal RAG QA for cosmetics regulation knowledge: hybrid BM25 + vector recall, two-stage reranking, evidence gating that refuses unsupported answers, uint32 bitmask RBAC, and graded evidence levels (implemented != production-validated).
```

251 字符。**同一时刻只能有一个 description**，选一个，另一个作为文案备选留档。

---

## 2 · Topics（上限 20）

GitHub 最多 20 个 topic；只能用小写字母、数字和连字符，单个不超过 35 字符。Topic 是**唯一影响可检索性**的字段，比 description 更接近"招聘方搜什么"的答案。

### 推荐清单（正好 20）

| # | topic | 选它的理由 |
|---|---|---|
| 1 | `rag` | 最高信噪比的主类目 |
| 2 | `retrieval-augmented-generation` | 长尾搜索词，全称命中率高于缩写 |
| 3 | `llm` | 高流量入口词 |
| 4 | `multimodal-rag` | 本项目真实能力（文本 + 图像 + OCR 路由） |
| 5 | `hybrid-search` | 核心机制：dense + BM25 + 视觉路融合 |
| 6 | `bm25` | 同上，算法层入口 |
| 7 | `reranking` | 两级重排是本项目实质技术点之一 |
| 8 | `evidence-gating` | 最有区分度的主题词，指向 Evidence/Answer Gate |
| 9 | `hallucination-detection` | 同一件事的行业通用叫法，便于被搜到 |
| 10 | `rbac` | 安全类岗位的直达词 |
| 11 | `fastapi` | 后端框架类岗位的直达词 |
| 12 | `qdrant` | 向量存储 |
| 13 | `elasticsearch` | 检索存储 |
| 14 | `redis` | 会话 / 缓存 / 限流 / 审计流 |
| 15 | `vector-search` | 比 `vector-database` 更贴近检索侧岗位 |
| 16 | `llm-evaluation` | 评测能力，可检索性高且真实存在 |
| 17 | `llmops` | 生产化 / 运维侧岗位入口 |
| 18 | `observability` | 指标、告警、审计、SLO runbook |
| 19 | `docker` | 部署形态（canonical） |
| 20 | `kubernetes` | 第二部署形态的清单契约 |

### 相对现状的取舍

现状 20 个里有 7 个建议换掉，理由逐条如下：

| 现状 topic | 处置 | 理由 |
|---|---|---|
| `bge` | **移除** | 模型实现细节，不是能力标签；且真实 BGE 权重不在仓库，容易被读成已接入真实模型 |
| `clip` | **移除** | 同上 |
| `qwen` | **移除** | 与 description 删 `Qwen/vLLM` 同理：模型名会被当成运行时证据 |
| `vllm` | **移除** | 同上，且该拓扑明确为 `PENDING` |
| `llm` | **保留** | 高流量入口词，不是实现细节 |
| `ai-engineering` | **移除** | 与 `rag` / `llmops` 语义重叠，重复占位 |
| `enterprise-ai` | **移除** | 营销向、可信度低，替换为可验证的 `rbac` |
| — | **新增** `open-telemetry` 的替代是**不**加 | OTLP exporter 已实现但**默认关闭**，运行期闭环为 `PENDING`。挂 `open-telemetry` topic 会被读成"tracing 已闭环"，而这是本仓库明确否认的表述 |

### 关于 `kubernetes` 的诚实提醒

保留 `kubernetes` 是因为 `deploy/k8s/` 的清单与 26 项静态检查确实存在且为 `REPO_VERIFIED`。但该 topic 存在被过度解读的风险：**本仓库没有集群，真实部署为 `PENDING`**。README 的 [Kubernetes 部署契约](deployment-guide-k8s.md) 与本页都是边界声明；任何时候都不得把这个 topic 表述为"已在 K8s 上运行"。

### 应用方式

```bash
# 一次性写入 20 个 topic（GitHub API 会整体替换，不是追加）
gh api -X PUT repos/Xander-Xai/Beauty-Industry-RAG-QA-System/topics \
  -H "Accept: application/vnd.github+json" \
  -f 'names[]=rag' -f 'names[]=retrieval-augmented-generation' \
  -f 'names[]=llm' -f 'names[]=multimodal-rag' \
  -f 'names[]=hybrid-search' -f 'names[]=bm25' \
  -f 'names[]=reranking' -f 'names[]=evidence-gating' \
  -f 'names[]=hallucination-detection' -f 'names[]=rbac' \
  -f 'names[]=fastapi' -f 'names[]=qdrant' \
  -f 'names[]=elasticsearch' -f 'names[]=redis' \
  -f 'names[]=vector-search' -f 'names[]=llm-evaluation' \
  -f 'names[]=llmops' -f 'names[]=observability' \
  -f 'names[]=docker' -f 'names[]=kubernetes'

# 回读确认写入结果与数量
gh api repos/Xander-Xai/Beauty-Industry-RAG-QA-System/topics --jq '.names | length'
```

---

## 3 · Homepage policy

### 决策：**保持为空**

现状 `homepage` 是空字符串，这应当**维持**。

Homepage 会出现在仓库卡片右侧、README 顶部右侧和社交分享里，是一个"点进去就是产品"的承诺。本仓库目前没有可指向的东西：

| 候选目标 | 为什么不设 |
|---|---|
| 线上 Demo 站点 | 不存在。设置即伪造 |
| 前端 GitHub Pages | `frontend/` 没有配置 Pages 构建，也没有可发布的静态产物；即使配了，页面需要真实后端才有意义，会变成打不开的空壳 |
| localhost / 内网地址 | 对外部读者无意义，且泄露部署形态 |
| README 内的演示图 | 那是一张合成数据截图，不是站点。把它当 homepage 会把 `SYNTHETIC DEMO` 伪装成产品 |
| 外部文档站（如 Notion / Vercel 临时页） | 引入仓库之外的内容源，与本仓库"文档真源唯一"的纪律冲突 |

### 什么时候可以设

满足以下**任一**条件后，才写入 homepage，并在同一提交里更新本节：

1. 仓库内有一套可复现部署、且确实部署到了一个可公开访问的地址；
2. `frontend/` 接上真实后端并通过 Pages 公开，且有一个稳定的公开数据源；
3. 建立了一个从本仓库文档构建的文档站，且它的 canonical 来源是本仓库而非副本。

### 设置方式（届时）

```bash
gh repo edit Xander-Xai/Beauty-Industry-RAG-QA-System --homepage "https://<real-url>"
```

---

## 4 · Social preview

现状：未设置，GitHub 会退回到仓库首字母占位图——在一屏列表里等于不可见。

### 规格

| 项 | 值 |
|---|---|
| 上传位置 | Settings → General → Social preview |
| 尺寸 | **1280 × 640**（2:1） |
| 格式 | PNG 或 JPG；GIF 可用但不建议 |
| 大小上限 | 1 MB |
| API | **没有 API**，只能手工上传。这一步无法自动化，是本文档存在的原因之一 |

### 为什么不能直接复用现有演示图

`docs/assets/demo-request-evidence-flow.webp` 是 **2690 × 2188**，接近 5:4 的竖向 UI 截图。GitHub 会把它裁进 2:1 的横幅，裁掉的部分正是右侧那两张"链路说明"卡片——也就是这张图信息量最高的部分。所以它需要重新构图，不是缩放。

### 推荐构图

从左到右三块，与 README 第一屏保持同一套措辞：

1. **左（约 55%）**：一行 positioning + 一行技术栈摘要。措辞与本文档第 1 节的推荐 description **保持一致**，避免卡片与 README 说法不同。
2. **中（约 30%）**：一条 8 节点的横向链路示意（身份 → 缓存 → 2–4 路召回 → RRF → 两级重排 → Evidence Gate → 生成 → Answer Gate），箭头 + 极短标签，不带任何分数或延迟数字。
3. **右（约 15%）**：一个三色证据图例条，只放三个词：`HISTORICAL_PRODUCTION` / `REPO_VERIFIED` / `PENDING`。

### 内容红线

社交预览是**最容易被单独转发**的一张图，所以它的红线比 README 更严：

- **不得**出现 QPS、延迟、文档数、用户数、准确率、Recall、NDCG 等任何数字，除非紧邻 `HISTORICAL_PRODUCTION` 或 `PENDING` 标注；
- **不得**出现 `vLLM` / `Qwen` / GPU 型号；
- **不得**出现任何"生产验证通过""已上线"的措辞；
- **不得**使用第三方商标 logo（模型、向量库、云的 logo 都不要）；
- 必须能看到本仓库名，否则被转发后无法归因；
- 若图中出现界面元素，只能用 `docs/demo/synthetic_corpus.json` 的合成内容，并保留 `SYNTHETIC DEMO` 标记——`docs/demo/capture_demo.py` 会拒绝没有该标记的输入，删掉标记等于伪造。

### 源文件放哪

上传的 PNG 是二进制产物，但**它的可复现源必须留在仓库里**，否则它就是又一张不可验证的图。放在 `docs/assets/social-preview.*`（脚本或模板形式），生成命令写进 `docs/demo/README.md`，并在本节记录复现命令。这样"社交预览说了什么"和"README 说了什么"就仍然可对照。

---

## 5 · 一致性检查清单

应用配置后逐条核对。任何一条不成立就是配置漂移，应当回滚配置而不是改文档。

| # | 检查项 | 通过标准 |
|---|---|---|
| 1 | description 长度 | ≤ 350 字符，且未被 UI 截断 |
| 2 | description 无模型名 | 不含 `Qwen` / `vLLM` / GPU 型号 |
| 3 | description 无数字 | 不含任何规模或性能数字 |
| 4 | topics 数量 | 恰好 20，全部为小写字母 / 数字 / 连字符 |
| 5 | topics 无 `open-telemetry` | 该能力闭环为 `PENDING`，不挂 |
| 6 | homepage 为空 | 除非满足第 3 节的任一条件 |
| 7 | social preview 尺寸 | 1280 × 640，≤ 1 MB |
| 8 | social preview 无未标注数字 | 逐个数字回溯到证据等级 |
| 9 | 社交预览源可复现 | `docs/assets/` 下有源文件与复现命令 |
| 10 | 三处口径一致 | 卡片 description、README 第一屏、本文档说的是同一件事 |
| 11 | 证据守卫通过 | `python3 scripts/check_repo_consistency.py` 仍返回成功 |

**复核命令**

```bash
python3 scripts/check_repo_consistency.py
gh api repos/Xander-Xai/Beauty-Industry-RAG-QA-System \
  --jq '{description, homepage, topics: (.topics | length)}'
```

第 11 条是关键：元数据是仓库状态的一部分，改它不能让证据一致性守卫失败。