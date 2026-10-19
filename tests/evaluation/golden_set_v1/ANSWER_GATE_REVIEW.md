# Real Corpus V1 — Answer Gate review (3 candidates)

> **These samples are not retrieval ground truth and are excluded from the Retrieval Recall
> denominator.** They exist to test whether the system refuses when this corpus cannot
> support an answer. All three remain `DRAFT_UNVERIFIED` and `scorable: false`.

## Why keyword absence is not proof of unanswerability

The draft flagged these questions because no chunk contained the searched token pairs.
That is evidence of *lexical* absence only. A corpus can answer a question without using
the same words (for example, a passage may state a rule that implies the answer), and a
question can be unanswerable for reasons unrelated to the corpus. **A human must judge
semantic answerability**; the `near_miss_passages` below are there to make that judgement
possible rather than to pre-decide it.

## Review table

| sample_id | question | draft matched chunks | decision |
|---|---|---:|---|
| `REAL-V1-NOANSWER-001` | 《化妆品监督管理条例》规定的化妆品中铅的限量是多少 mg/kg？ | 0 | ☐ KEEP ☐ REWRITE ☐ REJECT |
| `REAL-V1-NOANSWER-002` | 驻留类化妆品中烟酰胺的最高允许浓度是多少？ | 0 | ☐ KEEP ☐ REWRITE ☐ REJECT |
| `REAL-V1-NOANSWER-003` | 《化妆品监督管理条例》是否规定了化妆品中甲醇的限量？ | 0 | ☐ KEEP ☐ REWRITE ☐ REJECT |

---

## REAL-V1-NOANSWER-001

**Question:** 《化妆品监督管理条例》规定的化妆品中铅的限量是多少 mg/kg？

- `evaluation_target`: `answer_gate`
- excluded from Retrieval Recall: **True**
- `corpus_version`: `real-v1-20261019`
- `corpus_sha256`: `38849be7fe8ce077ea5a5e61fa1fa3909143b8a9088dd55d372b3d1c64711a1c`
- draft `matched_chunk_ids`: `[]`
- searched pairs: `[['铅', '限量'], ['铅', 'mg/kg']]`

**Near-miss passages (contain a searched substance term, not the requested value)**

_No near-miss passage contains even the substance term._

**Reviewer must answer**

1. Does any corpus passage answer this question in different wording?
2. Is the question outside this corpus's scope rather than unanswerable in general?
3. Should it stay as an answer-gate sample, be rewritten, or be rejected?

**Decision (fill by hand)**

| field | value |
|---|---|
| decision (`KEEP` / `REWRITE` / `REJECT`) |  |
| confirmed unanswerable from this corpus (`YES` / `NO`) |  |
| reviewer |  |
| reviewed_at (ISO date) |  |
| review notes |  |

---

## REAL-V1-NOANSWER-002

**Question:** 驻留类化妆品中烟酰胺的最高允许浓度是多少？

- `evaluation_target`: `answer_gate`
- excluded from Retrieval Recall: **True**
- `corpus_version`: `real-v1-20261019`
- `corpus_sha256`: `38849be7fe8ce077ea5a5e61fa1fa3909143b8a9088dd55d372b3d1c64711a1c`
- draft `matched_chunk_ids`: `[]`
- searched pairs: `[['烟酰胺', '最高允许浓度'], ['烟酰胺', '最大允许浓度'], ['烟酰胺', '限量']]`

**Near-miss passages (contain a searched substance term, not the requested value)**

- `nmpa/used-ingredients-2021/row-007359` / `b7770f9a-97d0-562f-9194-50db75996c4b` — contains a searched substance term but not the requested limit/value

```text
序号：07359
中文名称：烟酰胺
INCI名称/英文名称：NIACINAMIDE
淋洗类产品最高历史使用量（%）：未提供
驻留类产品最高历史使用量（%）：未提供
备注：未提供
```

**Reviewer must answer**

1. Does any corpus passage answer this question in different wording?
2. Is the question outside this corpus's scope rather than unanswerable in general?
3. Should it stay as an answer-gate sample, be rewritten, or be rejected?

**Decision (fill by hand)**

| field | value |
|---|---|
| decision (`KEEP` / `REWRITE` / `REJECT`) |  |
| confirmed unanswerable from this corpus (`YES` / `NO`) |  |
| reviewer |  |
| reviewed_at (ISO date) |  |
| review notes |  |

---

## REAL-V1-NOANSWER-003

**Question:** 《化妆品监督管理条例》是否规定了化妆品中甲醇的限量？

- `evaluation_target`: `answer_gate`
- excluded from Retrieval Recall: **True**
- `corpus_version`: `real-v1-20261019`
- `corpus_sha256`: `38849be7fe8ce077ea5a5e61fa1fa3909143b8a9088dd55d372b3d1c64711a1c`
- draft `matched_chunk_ids`: `[]`
- searched pairs: `[['甲醇', '限量'], ['甲醇', 'mg/kg']]`

**Near-miss passages (contain a searched substance term, not the requested value)**

- `nmpa/used-ingredients-2021/row-001279` / `e3a21916-f82b-5ae0-a05b-155087569200` — term only occurs inside a longer substance name; not evidence about the searched substance

```text
序号：01279
中文名称：苯甲醇
INCI名称/英文名称：BENZYL ALCOHOL
淋洗类产品最高历史使用量（%）：/
驻留类产品最高历史使用量（%）：/
备注：按照《化妆品安全技术规范》要求使用
```

**Reviewer must answer**

1. Does any corpus passage answer this question in different wording?
2. Is the question outside this corpus's scope rather than unanswerable in general?
3. Should it stay as an answer-gate sample, be rewritten, or be rejected?

**Decision (fill by hand)**

| field | value |
|---|---|
| decision (`KEEP` / `REWRITE` / `REJECT`) |  |
| confirmed unanswerable from this corpus (`YES` / `NO`) |  |
| reviewer |  |
| reviewed_at (ISO date) |  |
| review notes |  |

---

