# 中文分词 A/B — standard vs smartcn vs ik_smart

> 三臂对照，全部使用**真实 Elasticsearch 8.11.0**、同一份已审批语料、
> 同一批 chunk ID、同一 epoch、同一套金标。**唯一变量是预先声明的
> `content` 分析器。**

---

## 1. 为什么做这个实验

上一版报告（[`real-v1-bm25-run.md`](real-v1-bm25-run.md)）记录了 8 条
top-10 未召回，并给出一个**假设**：索引使用默认 `standard` 分析器，
中文被切成单字 token，长查询的信号被稀释。该假设当时只有"问题长度与召回率
负相关"这一个相关性证据——**相关性不是因果**。

本实验直接检验这个假设：同一语料、同一金标、同一检索器，只换分词器，
看指标是否动。**如果不动，假设就是错的。**

---

## 2. 实验环境与隔离

| 项 | 值 |
|---|---|
| 主节点 `beauty-rag-es`（9200） | ES 8.11.0，**未安装任何插件，未重启，未修改** |
| 隔离节点 `beauty-rag-es-smartcn`（9201） | ES 8.11.0，**新建容器**，为本实验专供 |
| 插件 | `analysis-smartcn 8.11.0`、`analysis-ik 8.11.0` |
| 三臂索引 | 全部构建在**同一个隔离节点**上 |

三臂全部在**同一个隔离节点**上构建与评测，是为了把"节点差异"从变量里排除掉。
若把 standard 臂放在主节点、把 smartcn 臂放在隔离节点，那么两臂之间同时差了
分词器和节点，实验就无法归因。

> 正式 BM25 报告
> [`real-v1-approved-bm25-run.md`](real-v1-approved-bm25-run.md) 用的是主节点上的
> `cosmetics_docs_exp_v3_approved`。它的 `standard` 基线数字与本实验 `standard`
> 臂**逐位相同**（`R@10 = 0.7037`、`MRR@10 = 0.4088`），两处互为交叉验证。

### 插件来源与风险核验

| 插件 | 来源 | 版本核验 | 风险处置 |
|---|---|---|---|
| `analysis-smartcn` | Elastic 官方发行页 `artifacts.elastic.co/downloads/elasticsearch-plugins/analysis-smartcn/analysis-smartcn-8.11.0.zip` | `_cat/plugins` 报 `analysis-smartcn 8.11.0`，与节点同版本 | 官方插件，仅隔离容器 |
| `analysis-ik` | 厂商官方分发站 `release.infinilabs.com/analysis-ik/stable/elasticsearch-analysis-ik-8.11.0.zip` | 包内 `plugin-descriptor.properties` 声明 `elasticsearch.version=8.11.0`、`version=8.11.0`；`classname=com.infinilabs.ik.elasticsearch.AnalysisIkPlugin` | **第三方插件，非 Elastic 官方**。GitHub Releases 渠道**不存在 8.11.0 预编译产物**（两个仓库的 release asset 探测均 404），只有厂商分发站有。**该包未附带 LICENSE 文件，厂商站点也没有提供可核对的校验和文件（`.sha512` 返回 404）**，因此本次只能记录我方下载到的 SHA-256，无法与厂商公布值比对。**仅用于本实验的隔离容器，不进入生产索引。** |

下载件 SHA-256（本方记录，非厂商公布）：

```
analysis-smartcn-8.11.0.zip   3f0c…（见 artifacts，本地留存）
analysis-ik-8.11.0.zip        ae32133c324a72379b9fafa5006c77f4c07a6269d22df8cc6528a29d09054b00
```

**IK 的结论是"可用于实验、不可用于生产"**：版本匹配已核验，但来源是第三方、
无公布校验和可核对、无 LICENSE 文件随包分发。把它装进生产索引需要单独的风险
评审，本轮没有做，也没有授权。

### 一个被实测排除的候选

`ik_index`（IK 的 max_word 模式）**无法作为索引分析器使用**。ES 拒绝：

```
mapper_parsing_exception: Failed to parse mapping: analyzer [ik_index]
has not been configured in mappings
```

该构建把 `ik_index` 注册为 search analyzer 而非 index analyzer。因此本实验的
第三臂用 `ik_smart`（索引与查询同为 smart 模式）。这不是失败，是如实记录一条
实测边界。

---

## 3. 分词实测（Elasticsearch `_analyze` 真实输出）

采样方式：对**每一臂自己的索引与自己的 `content` 字段**调用 `_analyze`。
（第一次尝试时两臂都指向了 smartcn 索引，得出"两臂分词完全相同"的假结论；
改正后才是下表。这类错误正是要求"analyzer 是否真正生效"必须实测的原因。）

| 输入 | `standard`（token 数） | `smartcn`（token 数） | `ik_smart`（token 数） |
|---|---|---|---|
| 「根据《化妆品监督管理条例》，化妆品的法定定义是什么？」 | 根 据 化 妆 品 监 督 管 理 条 例 化 妆 品 的 法 定 定 义 是 什 么（22） | 根据 化妆品 监督 管理 条例 化妆品 的 法定 定义 是 什么（11） | — |
| 「…泛醇在驻留类产品中的最高历史使用量是多少？」 | 根 据 已 使 用 化 妆 品 原 料 目 录 2021 年 版 泛 醇 在 驻 留 类 产 品 中 的 最 高 历 史 使 用 量 是 多 少（35） | 根据 已 使用 化妆品 原料 目录 2021 年 版 泛 醇 在 驻留 类 产品 中 的 最高 历史 使用量 是 多少（22） | …（19） |
| 「企业使用已列入…《已使用化妆品原料目录（2021年版）》的原料生产国产普通化妆品…」 | 企 业 使 用 已 列 入 已 使 用 化 妆 品 原 料 目 录 … 手 续（45） | 企业 使用 已 列入 已 使用 化妆品 原料 目录 … 手续（25） | …（24） |
| `国务院令 第727号` | 国 务 院 令 第 727 号（7） | 国务院令 第 727 号（4） | 国务院令 第 727号（3） |
| `苯甲醇` | 苯 甲 醇（3） | 苯 甲醇（2） | 苯 甲醇（2） |
| `羟苯甲酸` | 羟 苯 甲 酸（4） | 羟 苯甲酸（2） | 羟 苯甲酸（2） |
| `烟酰胺` | 烟 酰 胺（3） | 烟 酰 胺（3） | **烟 酰胺**（2） |
| `NIACINAMIDE` | `niacinamide`（1） | `niacinamid`（1） | **`niacinamide`**（1） |

两个值得记录的分词事实：

1. **长查询的 token 稀释是真的**：45 字查询在 `standard` 下变成 45 个单字 token，
   在 `smartcn` 下 25 个，在 `ik_smart` 下 24 个。
2. **专业术语仍然被切错，而且不止一个分词器会切错**：`烟酰胺`（烟酸��）被
   `standard` 和 `smartcn` 都切成 `烟 酰 胺`，只有 IK 切对了 `烟 酰胺`；
   英文 INCI 名 `NIACINAMIDE` 被 `smartcn` 截成了 `niacinamid`。
   **这说明默认词典不覆盖本领域术语**，是分词器选型无法单独解决的问题。

完整 `_analyze` 输出见
[`docs/validation/evidence/chinese-analyzer-tokenisation.json`](evidence/chinese-analyzer-tokenisation.json)。

---

## 4. 三臂对照结果

**同语料、同标签、同检索器、真实 ES；只换分析器。** n = 27，27 条全部评分。

| 指标 | `standard` | `smartcn` | `ik_smart` | smartcn−std | ik−std |
|---|---:|---:|---:|---:|---:|
| Recall@1 | 0.2963 | 0.2593 | 0.2593 | **−0.0370** | **−0.0370** |
| Recall@3 | 0.4074 | 0.5556 | 0.4630 | +0.1481 | +0.0556 |
| Recall@5 | 0.5185 | 0.8148 | 0.8519 | +0.2963 | +0.3333 |
| **Recall@10** | **0.7037** | **0.9259** | **0.9630** | **+0.2222** | **+0.2593** |
| HitRate@1 | 0.3333 | 0.2963 | 0.2963 | −0.0370 | −0.0370 |
| HitRate@3 | 0.4074 | 0.5556 | 0.4815 | +0.1481 | +0.0741 |
| HitRate@5 | 0.5185 | 0.8148 | 0.8519 | +0.2963 | +0.3333 |
| **HitRate@10** | **0.7037** | **0.9259** | **0.9630** | **+0.2222** | **+0.2593** |
| MRR@10 | 0.4088 | 0.4674 | 0.4620 | +0.0586 | +0.0532 |
| NDCG@10 | 0.4698 | 0.5771 | 0.5753 | +0.1072 | +0.1055 |

### 延迟

| 口径 | `standard` | `smartcn` | `ik_smart` |
|---|---:|---:|---:|
| min | 8.6 ms | 7.9 ms | 7.6 ms |
| p50 | 11.73 ms | 11.36 ms | 9.33 ms |
| p95 | 17.20 ms | 24.87 ms | 15.56 ms |
| mean | 217.17 ms | 215.94 ms | 212.48 ms |
| max（冷启动） | 5551.42 ms | 5515.25 ms | 5483.65 ms |

> **延迟差异不构成结论。** n = 27、单节点、本机回环，p50/p95 的差距处于
> 同一量级的运行间抖动：换一次代码提交后的重跑，`smartcn` 的 p95 从 11.23 ms
> 变到 24.87 ms，而语料与索引一字未改。可以说的只有"延迟没有因为换成中文
> 分析器而变差"；不能说"分词器让延迟变快/变慢"。

---

## 5. 逐样本：修复了什么、仍缺什么、退化了什么

### 重点跟踪的 8 条样本（首个命中名次，`—` = 未进入 top-10）

| 样本 | 类型 | `standard` | `smartcn` | `ik_smart` |
|---|---|---:|---:|---:|
| REAL-V1-001 | regulation | — | — | — |
| REAL-V1-003 | regulation | — | **3** | **3** |
| REAL-V1-012 | ingredient | — | 7 | **5** |
| REAL-V1-015 | ingredient | — | — | **10** |
| REAL-V1-018 | regulation | — | **5** | 8 |
| **REAL-V1-021** | regulation | — | **6** | **10** |
| REAL-V1-025 | ingredient | — | **5** | **5** |
| REAL-V1-027 | ingredient | — | 7 | **3** |

### 汇总

| 臂 | 修复（standard 未召回 → 召回） | 退化（召回 → 未召回） | 仍失败 |
|---|---:|---:|---|
| `smartcn` | **6** | **0** | REAL-V1-001、015 |
| `ik_smart` | **7** | **0** | REAL-V1-001 |

- `smartcn` 修复：`003, 012, 018, 021, 025, 027`
- `ik_smart` 修复：`003, 012, 015, 018, 021, 025, 027`
- **两个分析器都没有让任何原本正确的样本掉出 top-10。**
- **两个分析器都让 Recall@1 各下降了 1 个样本（−0.0370）**：唯一的原因是
  `REAL-V1-005` 从第 1 名滑到第 2 名。更长的词单元让更多文档挤进候选，
  头部排序变松了一点——MRR@10 与 NDCG@10 同时上升，说明这是"整体上移、
  顶端微松"，不是"排序变差"。

### REAL-V1-021：上一版报告里的已知失败

> 企业使用已列入目录的原料生产国产普通化妆品，需要办理哪些注册和备案手续？

`standard` 下 **从未**进入 top-10；`smartcn` 下第 **6** 名，`ik_smart` 下第
**10** 名。上一版报告把它列为"完全未命中"的真实失败，本轮确认：**中文无分词
确实是这条失败的原因之一**，两个中文分析器都修复了它。

### 仍未召回的 REAL-V1-001：不是分词问题

> 根据《化妆品监督管理条例》，化妆品的法定定义是什么？

三个分析器**全部失败**。实测该金标段落的 token 与问题的重叠率只有 **0.40**，
原因是**用词不同**：问题问"法定定义"，原文写的是"本条例所称化妆品，是指…"，
两者没有任何共享的实词。smartcn 把 22 个 token 压到 11 个，压缩的是冗余，
压缩不掉这个词汇鸿沟。

该样本的 gold chunk 在 `standard` 下排第 **49**、`smartcn` 下排第 **12**——
分词**确实**把它从"完全不可见"推到了"top-12"，只是还差两名。

> **诚实结论：中文分词解释了这次失败率的很大一部分，但不是全部。**
> 剩下的 `REAL-V1-001` 是查询与语料的**释义鸿沟**，要解决它需要查询改写、
> 同义词扩展或 dense 检索，不是换分词器。本轮没有做这些，也没有为了做满而
> 修改任何金标。

---

## 6. 结论

1. **上一版的假设成立**：中文无分词确实是这批失败的主因。
   同语料同标签下，`standard → smartcn` 使 Recall@10 从 0.7037 升到 0.9259
   （+0.2222），修复 6 条、零退化。
2. **`ik_smart` 略优于 `smartcn`**：Recall@10 0.9630（多修复 1 条），
   p50 最低（9.33 ms），且在 `烟酰胺`、`NIACINAMIDE` 这类本领域术语上
   分词更准。但它来自第三方、无公布校验和可比对、无随包 LICENSE。
3. **两者都让 Recall@1 下降 0.0370**，是本实验唯一的方向性回退，
   原因是头部排序变松而非整体变差。
4. **默认词典不覆盖化妆品/INCI 专业术语**，这是分词器选型解决不了的，
   需要自定义词典或领域词典。
5. **样本量 27，不做统计推断。** 6–7 条样本翻转在方向上明确，但单次运行的
   差值不能当作效应量估计。

### 尚未执行的动作（需要单独授权）

- 把 `ik_smart` 或 `smartcn` 用于**生产索引**：需要重建 `cosmetics_docs`，
  本轮**未授权**，也**未执行**。蓝绿方案见
  [`production-index-governance.md`](production-index-governance.md) §4。
- 配置**领域词典**（`烟酰胺`、`羟苯甲酸`、`苯氧乙醇` 等）：实验显示默认词典
  会切错这些词，补词典是明确的下一步，但需要人工维护词表并重新评测。
- 在更大的金标集上复现本结论，避免 27 条样本的偶然性。

---

## 7. 复现命令

```bash
# 隔离节点（与主节点无关）
docker run -d --name beauty-rag-es-smartcn -p 127.0.0.1:9201:9200 \
  -e discovery.type=single-node -e xpack.security.enabled=false \
  -e ES_JAVA_OPTS="-Xms512m -Xmx512m" \
  docker.elastic.co/elasticsearch/elasticsearch:8.11.0

# 插件（只在隔离节点）
curl -sLO https://artifacts.elastic.co/downloads/elasticsearch-plugins/analysis-smartcn/analysis-smartcn-8.11.0.zip
curl -sLO https://release.infinilabs.com/analysis-ik/stable/elasticsearch-analysis-ik-8.11.0.zip
docker cp analysis-smartcn-8.11.0.zip analysis-ik-8.11.0.zip beauty-rag-es-smartcn:/tmp/
docker exec -u root beauty-rag-es-smartcn bash -c \
  'cd /usr/share/elasticsearch && bin/elasticsearch-plugin install --batch file:///tmp/analysis-smartcn-8.11.0.zip'
docker exec -u root beauty-rag-es-smartcn bash -c \
  'cd /usr/share/elasticsearch && bin/elasticsearch-plugin install --batch file:///tmp/analysis-ik-8.11.0.zip'
docker restart beauty-rag-es-smartcn

# 三臂索引（同一节点，唯一变量是 --analyzer）
for arm in "" smartcn ik_smart; do
  ELASTICSEARCH_HOST=http://127.0.0.1:9201 python3 scripts/corpus/index_real_corpus_v1_es.py \
    --profile experiment --index cosmetics_docs_exp_v3_ab_${arm:-std} \
    --approval-store data/offline_source_trust.sqlite3 ${arm:+--analyzer $arm} \
    --out artifacts/corpus/cosmetics_docs_exp_v3_ab_${arm:-std}.json
done

# 三臂评测（各自 CONFIG_PATH 指向不同索引）
python3 -m benchmarks.retrieval_benchmark --executor real --config bm25 \
  --dataset tests/evaluation/golden_set_v1/reviewed/annotations_v1_reviewed.jsonl \
  --top-k 10 --require-contract --require-integrity --require-results \
  --require-source-approval --output-dir artifacts/benchmarks

# 核验已发布证据
python3 scripts/validation/verify_benchmark_evidence.py
```

> 注意 `--analyzer` 为空时**不能传空字符串**（argparse 会拒绝），省略该参数
> 即表示使用索引默认分析器。

Refs #18. Refs #86.
