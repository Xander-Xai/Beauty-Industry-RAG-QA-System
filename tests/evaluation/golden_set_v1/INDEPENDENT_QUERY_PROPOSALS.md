# Independent user-query proposals (not in the dataset)

> These are **proposals**, not samples. They are not in `candidates_v1.jsonl`, carry no
> `doc_id`/`chunk_id`, are not scorable, and must not be counted in any metric. A human
> decides whether any of them is worth adding as a future, independently written query.

Why they exist: all 27 current questions were drafted from the corpus. A small number of
queries written from user intent, before looking at a passage, is the cheapest check
against a benchmark that only measures how well the retriever finds text it already
quoted.

| proposal_id | question |
|---|---|
| `IQ-001` | 我买的进口面霜在国内卖，包装上必须有中文标签吗？ |
| `IQ-002` | 普通化妆品备案之后，配方改了还能直接卖吗？ |
| `IQ-003` | 成分表里写了烟酰胺，但没写浓度，这样合规吗？ |
| `IQ-004` | 原料目录里的最高历史使用量，和《化妆品安全技术规范》的限量是一回事吗？ |
| `IQ-005` | 儿童化妆品在标签和原料上有没有额外的要求？ |
| `IQ-006` | 同一个原料既有中文名又有 INCI 名，标签上应该用哪个？ |

---

## IQ-001

**Question:** 我买的进口面霜在国内卖，包装上必须有中文标签吗？

- user intent: consumer checking whether an imported cosmetic needs a Chinese label
- why independent: written from a consumer scenario, not from an article's wording
- `review_status`: `PROPOSED_UNREVIEWED`
- `in_dataset`: **False**
- a human must decide: is it answerable from this corpus; which passages support it; difficulty

| decision (`ADOPT_LATER` / `REJECT`) | reviewer | reviewed_at | notes |
|---|---|---|---|
|  |  |  |  |

---

## IQ-002

**Question:** 普通化妆品备案之后，配方改了还能直接卖吗？

- user intent: small brand asking about change control after filing
- why independent: asks about a workflow consequence rather than quoting a clause
- `review_status`: `PROPOSED_UNREVIEWED`
- `in_dataset`: **False**
- a human must decide: is it answerable from this corpus; which passages support it; difficulty

| decision (`ADOPT_LATER` / `REJECT`) | reviewer | reviewed_at | notes |
|---|---|---|---|
|  |  |  |  |

---

## IQ-003

**Question:** 成分表里写了烟酰胺，但没写浓度，这样合规吗？

- user intent: label-reading question about ingredient disclosure
- why independent: combines a real label observation with a compliance question
- `review_status`: `PROPOSED_UNREVIEWED`
- `in_dataset`: **False**
- a human must decide: is it answerable from this corpus; which passages support it; difficulty

| decision (`ADOPT_LATER` / `REJECT`) | reviewer | reviewed_at | notes |
|---|---|---|---|
|  |  |  |  |

---

## IQ-004

**Question:** 原料目录里的最高历史使用量，和《化妆品安全技术规范》的限量是一回事吗？

- user intent: formulator distinguishing two different regulatory concepts
- why independent: written as a conceptual confusion, not as a quote lookup
- `review_status`: `PROPOSED_UNREVIEWED`
- `in_dataset`: **False**
- a human must decide: is it answerable from this corpus; which passages support it; difficulty

| decision (`ADOPT_LATER` / `REJECT`) | reviewer | reviewed_at | notes |
|---|---|---|---|
|  |  |  |  |

---

## IQ-005

**Question:** 儿童化妆品在标签和原料上有没有额外的要求？

- user intent: parent or brand asking about children's cosmetics
- why independent: a category question a real user would ask without knowing the article number
- `review_status`: `PROPOSED_UNREVIEWED`
- `in_dataset`: **False**
- a human must decide: is it answerable from this corpus; which passages support it; difficulty

| decision (`ADOPT_LATER` / `REJECT`) | reviewer | reviewed_at | notes |
|---|---|---|---|
|  |  |  |  |

---

## IQ-006

**Question:** 同一个原料既有中文名又有 INCI 名，标签上应该用哪个？

- user intent: packaging/labeling decision
- why independent: asks for a naming decision rather than restating the catalogue note
- `review_status`: `PROPOSED_UNREVIEWED`
- `in_dataset`: **False**
- a human must decide: is it answerable from this corpus; which passages support it; difficulty

| decision (`ADOPT_LATER` / `REJECT`) | reviewer | reviewed_at | notes |
|---|---|---|---|
|  |  |  |  |

---

