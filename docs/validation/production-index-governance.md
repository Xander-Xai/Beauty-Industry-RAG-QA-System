# 生产索引与 epoch 治理

> 只读核查与方案设计。**本文档不执行任何生产变更**：没有删除、没有重建
> `cosmetics_docs`，没有切换生产 epoch，没有做别名切换，也没有把历史原料目录
> 设为现行监管依据。下列每一条都是可核验的当前事实，不是计划。

核查日期：2026-10-20（UTC） · ES `8.11.0`（单节点，本机回环）

---

## 1. 五个不能混为一谈的条件

本轮工作里反复出现一个滑动：从"来源已批准"一路推到"可以上线"。这五件事是
独立的，每一件都有各自的证据：

| # | 条件 | 当前状态 | 证据 |
|---|---|---|---|
| 1 | 数据来源已批准 | ✅ **是**（2/2） | TrustRegistry 两条 `APPROVED`，actor `Xander-Xai`；见 [`real-v1-approved-bm25-run.md`](real-v1-approved-bm25-run.md) §1 |
| 2 | 可进入经验证索引 | ✅ **是** | `cosmetics_docs_exp_v3_approved` 212/212 篇 `APPROVED_EXTERNAL`，逐篇核验通过 |
| 3 | 可用于历史版本评测 | ✅ **是** | 正式 BM25 重跑 `results_are_benchmark=true` |
| 4 | 可用于生产环境 | ❌ **否** | 见 §2、§3；无别名切换、无 epoch 切换、无回滚演练 |
| 5 | 可支持当前法规结论 | ❌ **否** | 见 §5 |

**第 1 条成立不代表第 5 条成立。** 审批授予的是"有人对这些字节负责"，不是
"这些字节是现行有效的监管依据"。

---

## 2. 只读核查：生产索引实际状况

### 2.1 `cosmetics_docs` 持有错误的 provenance 声明

| 项 | 实际值 | 期望值 | 判定 |
|---|---|---|---|
| 文档数 | 212 | 212 | ✅ |
| `doc_version_epoch` | `real-v1-20261019` | 同 | ✅ |
| `status` | 全部 `active` | 同 | ✅ |
| `role_mask` / `dept_mask` | 全部 `0` | 同 | ✅ |
| **`provenance.source_id`** | **209 个逐 chunk 的逻辑 id**，如 `nmpa/regulation-727/article-001` | 2 个**源文件**路径 | ❌ |
| **`provenance.source_trust`** | **`MANAGED_INTERNAL`**（212/212） | `UNTRUSTED` | ❌ |
| **`provenance.approval_status`** | **`NOT_REQUIRED`** | `APPROVED` | ❌ |
| `approval_actor` / `approved_content_hash` | 空 | 具名 + 字节哈希 | ❌ |

**内容是对的，声明是错的。** 逐 chunk 哈希比对：

```
cosmetics_docs              content identity = 2717bce960678ee869fb44a207bc0de6fa9fc1432fc5924750775d5ad1c525f4
cosmetics_docs_exp_v3_approved content identity = 2717bce960678ee869fb44a207bc0de6fa9fc1432fc5924750775d5ad1c525f4
identical chunk ids + content: True
```

两个索引的 `chunk_id`、`doc_id`、正文**逐条完全一致**。差别只在 provenance：
已审批索引写 2 个源文件路径、`UNTRUSTED` + `APPROVED`；生产索引写 209 个逐 chunk
的逻辑 id、`MANAGED_INTERNAL` + `NOT_REQUIRED`。

### 2.2 为什么这条声明是错的，而不是"更宽松的等价写法"

`offline/source_trust.py` 的契约把 `MANAGED_INTERNAL` 定义为
*"produced and curated inside the managed data root"*。`cosmetics_docs` 里的
212 篇是政府网站与 NMPA 公告附件的派生摘录，**从定义上就不可能是本项目受管
数据根目录内产生的内容**。

把外部文档声明成 `MANAGED_INTERNAL / NOT_REQUIRED`，效果是让它们**零审核**进入
可激活状态。这正是该声明被撤回的原因。`cosmetics_docs` 上仍保留着撤回前的声明，
因为撤回声明只改了脚本，没有重建索引。

核验器对这个形态的判定（`benchmarks/source_approval.py`）：

```
source_managed_internal_not_reviewable × 209
'nmpa/regulation-727/article-001' declares MANAGED_INTERNAL, whose only
evidence is this index document itself; an index cannot attest to its own
provenance, so the claim cannot be cross-checked against reviewed bytes
```

### 2.3 提交配置与索引版本对不上

| 项 | 提交值 | 索引实际 | 后果 |
|---|---|---|---|
| `elasticsearch.index` | `cosmetics_docs` | 存在 | — |
| `knowledge_version_epoch` | `default` | `real-v1-20261019` | ❌ **不匹配** |

`retrieval/bm25_retriever.py` 的 `_build_es_query` 会按
`knowledge_version_epoch` 过滤 `doc_version_epoch`。该组合下过滤器匹配不到任何
文档，`search()` 返回 `[]`——与"语料里没有相关内容"完全无法区分。

本轮已把这一事实变成可见的（commit `ad5cb18`，`probe_epoch_binding()`）。实测：

```
ERROR BM25 epoch binding failed, lexical path disabled: index 'cosmetics_docs'
holds epoch(s) ['real-v1-20261019'] but knowledge_version_epoch is 'default';
the retrieval filter would match none of its documents, so every query would
read as 'no results' instead of 'wrong corpus configured'
search() -> 0 hits; enabled = False
```

**因此提交配置下的 BM25 路径现在会拒绝查询，而不是返回空结果。** 这是本轮
唯一一个改变生产代码行为的修复，且方向是 fail-closed：以前是静默返回空，现在
是明确拒绝并说明原因。已通过 `tests/test_bm25_retriever.py::TestEpochBinding`
的 9 个用例覆盖。

---

## 3. 本轮未做的事（授权边界）

以下全部**没有执行**：

- ❌ 删除 `cosmetics_docs`
- ❌ 重建 `cosmetics_docs`
- ❌ 切换生产 `knowledge_version_epoch`
- ❌ 生产索引别名（alias）切换
- ❌ 把历史原料目录设为现行监管依据

已执行的写操作只有：新增 4 个 `cosmetics_docs_exp_*` 隔离实验索引，在一台
**新建的、与在用节点无关**的 ES 8.11.0 容器上安装 `analysis-smartcn` 与
`analysis-ik` 两个插件。在用节点 `beauty-rag-es` 未被安装插件、未被重启、未被
修改。

---

## 4. 生产蓝绿迁移方案（非破坏性，尚未执行）

### 4.1 为什么必须蓝绿而不是原地重建

原地重建 `--recreate --confirm-index cosmetics_docs` 会让生产词法路径在重建
窗口内返回空或报错，且旧内容不可回退。蓝绿让"旧版本仍在服务"成为默认状态，
回退是一次别名原子切换，秒级完成。

### 4.2 步骤

| 步 | 动作 | 破坏性 | 回滚 |
|---|---|---|---|
| 0 | 记录当前基线：`cosmetics_docs` 的 `content_sha256`、文档数、epoch 分布、`GET _cat/indices` | 只读 | — |
| 1 | 建 `cosmetics_docs_v2`：经 `TrustRegistry` 解析的 provenance，逐篇 `APPROVED_EXTERNAL` | 只写新索引 | 删除新索引 |
| 2 | 建别名 `cosmetics_docs_read` → `cosmetics_docs_v2`，**不动** `cosmetics_docs` 本体 | 只加别名 | 删别名 |
| 3 | 在别名上跑 `benchmarks/source_approval.verify_index_source_approval`，要求 `verified=true`、0 issues | 只读 | — |
| 4 | 在别名上重跑正式金标 BM25，要求 `results_are_benchmark=true` | 只读 | — |
| 5 | 影子流量：服务同时查旧索引与别名，比对 top-10 一致性与 p95 延迟 | 只读 | 停影子 |
| 6 | 切别名指向新索引（原子操作，`remove` + `add`），`cosmetics_docs` 保持不变待命 | 别名指针 | 再切一次指回旧索引 |
| 7 | 确认稳定后，另行决策是否删除旧索引 | 破坏性，单独授权 | 无法回滚 |

### 4.3 每一处必须失败关闭的检查

1. `plan_index_write()` 已有的破坏性默认防护：新索引非空且无显式动作时**拒绝写入**
   （`scripts/corpus/index_real_corpus_v1_es.py`）。
2. 来源审批：未审批来源在 `--profile production` 下被拒绝写入。
3. `probe_epoch_binding()`：别名指向的索引若不含配置的 epoch，BM25 路径拒绝查询。
4. `verify_index_source_approval()`：发布前逐篇核验，混合审批索引不可发布。
5. 别名切换前，`cosmetics_docs` 实体索引**不做任何写操作**——这是回滚的前提。

### 4.4 尚未就绪的部分

- 别名 `cosmetics_docs_read` **不存在**，方案从未执行。
- `config.json` 的 `elasticsearch.index` 仍是 `cosmetics_docs`，本轮**未修改**。
- `knowledge_version_epoch` 仍是 `default`。把它改成 `real-v1-20261019` 会让
  生产词法路径开始检索本语料，这是**产品决策**（换语料 = 换检索域），不是
  运维切换，需要单独授权。

---

## 5. 版本适用性：仍然缺失的控制

即使来源全部审批、生产索引 provenance 修正、epoch 对齐——**第 5 条
（可支持当前法规结论）依然不成立**。

本语料是 2021 快照。已知时效性问题（详见
[`corpus/real_v1/SOURCES.md`](../../corpus/real_v1/SOURCES.md) 的
"监管版本时效性核查"）：

- 《条例》第七十八条过渡期已于 **2025-12-31 届满**，五类原特殊用途化妆品
  自 2026-01-01 起禁产禁售；配套的 150 号公告**不在本语料内**。
- 《已使用化妆品原料目录》已于 2025 年转为动态管理（2025 年第 61 号公告），
  **"最高历史使用量"栏已被删除**。

因此当前**仅有** `SOURCES.md` 里的文字声明在限制用途。它对人有效，对程序无效：
没有任何机制阻止把"2021 年版最高历史使用量是多少"当作现行事实回答。

### 需要但本轮未建设的控制

**版本适用性控制（applicability guard）**，至少需要：

1. 每篇文档携带 `regulatory_valid_from` / `regulatory_valid_until`；
2. 每条问答携带其断言所依据的版本；
3. 检索时按当前日期过滤，命中过期文档则**降级作答**（给出历史版本并声明其
   状态），而不是直接返回原文；
4. 金标集标注每条样本的目标日期，使"过期文档是否应当被召回"成为可测量的量。

本轮**不要求**实现该规则引擎，此处只明确指出缺口。把它写成一个 TODO 而不实现，
比让它在文档里被默认为"已覆盖"要诚实。

---

## 6. 可复现的核查命令

```bash
# 逐篇 provenance 分布与审批核验（对任意索引）
python3 -c "
import json; from pathlib import Path
from elasticsearch import Elasticsearch
from benchmarks.source_approval import verify_index_source_approval, resolve_approval_store
r = verify_index_source_approval(
    Elasticsearch(hosts=['http://127.0.0.1:9200']),
    index_name='cosmetics_docs',
    expected_epoch='real-v1-20261019',
    ledger_path=resolve_approval_store(),
    repo_root=Path('.').resolve(),
).as_dict()
print(r['verified'], json.dumps(r['issues']['by_code'], ensure_ascii=False))
"

# epoch 绑定（提交配置下的真实结果）
python3 -c "
import json
from retrieval.bm25_retriever import BM25Retriever
print(json.dumps(BM25Retriever().probe_epoch_binding(), ensure_ascii=False, indent=2))
"

# 两索引内容是否一致
python3 -c "
import hashlib
from elasticsearch import Elasticsearch
es = Elasticsearch(hosts=['http://127.0.0.1:9200'])
def sig(i):
    r = es.search(index=i, body={'size':300,'query':{'match_all':{}},'_source':['chunk_id','doc_id','content']})
    return hashlib.sha256('\x1e'.join(sorted(f\"{h['_source']['chunk_id']}|{h['_source']['doc_id']}|{h['_source']['content']}\" for h in r['hits']['hits'])).encode()).hexdigest()
a, b = sig('cosmetics_docs'), sig('cosmetics_docs_exp_v3_approved')
print(a); print(b); print('identical:', a == b)
"
```
