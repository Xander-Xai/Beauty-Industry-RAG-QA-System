"""Mock data generator for cosmetics RAG system testing.

Everything this module writes is **synthetic**. The generated files are shaped
like standard documents so that chunking, retrieval, RBAC filtering and the
Evidence / Answer gates have a realistic input, but no generated identifier,
standard number, ingredient code or limit value corresponds to a real
regulation, a real CAS entry or a real product.

Why the identifiers are fictional rather than plausible
------------------------------------------------------
An earlier revision emitted fictional ``GB/T <n>-<year>`` and ``QB/T`` headings,
none of them a real instrument, together with real INCI names carrying real CAS
numbers and fictional heavy-metal limits written in the layout of a real
technical standard. Each generated ``.txt`` was therefore individually mistakable
for a genuine regulatory conclusion, which is precisely the hybrid the
data-provenance statement has to rule out. Replacing the values with
authoritative ones would have required a source and a licence; inventing more
of them would have widened the problem. The identifiers are now drawn from one
invented series (``DEMO-STD-*`` / ``DEMO-ACT-*`` / ``000-00-0``) that is also
used by ``docs/demo/synthetic_corpus.json``, and every generated file opens
with :data:`SYNTHETIC_DATA_NOTICE`.

Boundary and CI
---------------
``scripts/check_public_repository_boundary.py`` fails CI if a real regulatory
identifier appears in this module at all, and
``tests/test_mock_generator.py`` fixes the disclosure contract on the output.
See ``DATA_PROVENANCE.md`` §4.
"""
import json
import os
import random

#: Written as the first line of every generated file and embedded in every
#: generated record. It has to survive being skimmed: a reader who opens one
#: generated ``.txt`` must not have to read the repository to learn it is fake.
SYNTHETIC_DATA_NOTICE = (
    "SYNTHETIC DEMO DATA — 合成演示数据，非真实法规、标准号或限量值。"
    "本文档中的标准号、原料编号、含量与限量均为虚构占位值，"
    "不构成任何合规判定依据。"
)

#: Invented instrument series. ``count`` is kept for shape parity with the
#: earlier table so the generated mix of document types does not change.
REGULATION_SYSTEMS = [
    {"code": "DEMO-STD", "name": "示例标准", "count": 50},
    {"code": "DEMO-GRP", "name": "示例团体标准", "count": 30},
    {"code": "DEMO-REQ", "name": "示例技术要求", "count": 15},
    {"code": "DEMO-NOT", "name": "示例技术通告", "count": 10},
    {"code": "DEMO-ANN", "name": "示例公告", "count": 10},
    {"code": "DEMO-REG-A", "name": "示例辖区法规 A", "count": 8},
    {"code": "DEMO-REG-B", "name": "示例辖区法规 B", "count": 5},
    {"code": "DEMO-REG-C", "name": "示例辖区法规 C", "count": 5},
]

#: Stand-in for the "术语和定义" clause of a technical standard. A real
#: definition attributed to a real instrument is the easiest part of a fixture
#: to be quoted out of context, so this one is explicitly fictional.
DEMO_DEFINITION = "本术语为本仓库虚构的示例占位定义，仅用于生成形状真实的检索输入。"

#: Generic chemistry taxonomy. These are product categories, not regulatory
#: classifications, so they carry no limit and no authority.
INGREDIENT_CATEGORIES = [
    "保湿剂", "乳化剂", "防腐剂", "香精", "表面活性剂",
    "增稠剂", "抗氧化剂", "紫外线吸收剂", "着色剂", "调理剂",
    "美白成分", "抗衰老成分", "舒缓成分", "去角质成分", "收敛剂",
]

ROLE_MAP = {"admin": 0x01, "rd": 0x02, "quality": 0x04, "regulation": 0x08, "sales": 0x10}
DEPT_MAP = {"研发部": 0x01, "品质部": 0x02, "法规部": 0x04, "销售部": 0x08, "市场部": 0x10}

#: Invented actives. ``cas`` is the reserved placeholder ``000-00-0`` rather than
#: a real registry number, and every ``limit`` is a fictional value expressed in
#: the invented ``DEMO-STD`` series. Nothing here maps onto a real substance or
#: a real maximum-use concentration.
TYPICAL_INGREDIENTS = [
    {"code": "DEMO-ACT-01", "cn": "示例保湿剂 A", "cas": "000-00-0", "limit": "≤0.90%（w/w）", "safety": "示例安全"},
    {"code": "DEMO-ACT-02", "cn": "示例保湿剂 B", "cas": "000-00-0", "limit": "≤1.20%（w/w）", "safety": "示例安全"},
    {"code": "DEMO-ACT-03", "cn": "示例去角质剂 C", "cas": "000-00-0", "limit": "≤2.00%（w/w，驻留）", "safety": "示例限用"},
    {"code": "DEMO-ACT-04", "cn": "示例维生素类 D", "cas": "000-00-0", "limit": "≤0.30%（w/w）", "safety": "示例限用"},
    {"code": "DEMO-ACT-05", "cn": "示例提亮剂 E", "cas": "000-00-0", "limit": "≤1.50%（w/w）", "safety": "示例安全"},
    {"code": "DEMO-ACT-06", "cn": "示例提亮剂 F", "cas": "000-00-0", "limit": "≤2.00%（w/w）", "safety": "示例限用"},
    {"code": "DEMO-ACT-07", "cn": "示例抗菌剂 G", "cas": "000-00-0", "limit": "禁用（示例）", "safety": "示例禁用"},
    {"code": "DEMO-ACT-08", "cn": "示例交联剂 H", "cas": "000-00-0", "limit": "禁用（示例）", "safety": "示例禁用"},
    {"code": "DEMO-ACT-09", "cn": "示例重金属示踪 I", "cas": "000-00-0", "limit": "≤10 mg/kg（示例）", "safety": "示例限用"},
    {"code": "DEMO-ACT-10", "cn": "示例重金属示踪 J", "cas": "000-00-0", "limit": "≤1 mg/kg（示例）", "safety": "示例限用"},
]

#: Invented product archetypes. ``key_ingredients`` reference the
#: ``DEMO-ACT-*`` codes above rather than real INCI names.
TYPICAL_FORMULAS = [
    {"name": "示例亮肤精华液", "category": "护肤", "key_ingredients": ["DEMO-ACT-01", "DEMO-ACT-05", "DEMO-BASE-01"]},
    {"name": "示例清透洁面乳", "category": "洗护", "key_ingredients": ["DEMO-ACT-03", "DEMO-BASE-02"]},
    {"name": "示例抗皱面霜", "category": "护肤", "key_ingredients": ["DEMO-ACT-04", "DEMO-BASE-03", "DEMO-ACT-01"]},
    {"name": "示例提亮面膜", "category": "护肤", "key_ingredients": ["DEMO-ACT-05", "DEMO-ACT-02", "DEMO-BASE-04"]},
    {"name": "示例控油粉底液", "category": "彩妆", "key_ingredients": ["DEMO-BASE-05", "DEMO-PIG-01", "DEMO-PIG-02"]},
    {"name": "示例卸妆水", "category": "洗护", "key_ingredients": ["DEMO-BASE-06", "DEMO-BASE-01"]},
]


def _generate_doc_id(prefix: str, index: int) -> str:
    return f"{prefix}_{index:04d}"


def _random_role_mask() -> int:
    r = random.random()
    if r < 0.3:
        return 0
    if r < 0.5:
        return ROLE_MAP["admin"]
    roles = list(ROLE_MAP.values())
    return random.choice(roles) | random.choice(roles)


def _random_dept_mask() -> int:
    r = random.random()
    if r < 0.3:
        return 0
    depts = list(DEPT_MAP.values())
    return random.choice(depts)


class MockDataGenerator:
    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        self._metadata = []

    def generate_regulations(self, count: int = 100):
        out_dir = os.path.join(self.output_dir, "regulations")
        os.makedirs(out_dir, exist_ok=True)
        for i in range(count):
            sys_info = random.choice(REGULATION_SYSTEMS)
            doc_id = _generate_doc_id("reg", i)
            code = f"{sys_info['code']}-{random.randint(1000, 9999)}-{random.randint(2015, 2025)}"
            title = f"{sys_info['name']} — {code}（虚构示例）"
            content = self._generate_regulation_content(sys_info, code)
            filepath = os.path.join(out_dir, f"{doc_id}.txt")
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(f"{SYNTHETIC_DATA_NOTICE}\n\n# {title}\n\n{content}")
            self._metadata.append({
                "doc_id": doc_id, "doc_type": "regulation", "file_path": filepath,
                "title": title, "law_id": code, "system": sys_info["code"],
                "data_class": "SYNTHETIC", "disclaimer": SYNTHETIC_DATA_NOTICE,
                "carries_real_regulatory_claims": False,
                "role_mask": _random_role_mask(), "dept_mask": _random_dept_mask(),
                "doc_version_epoch": "20260601_01", "status": "active",
            })

    def generate_ingredients(self, count: int = 500):
        filepath = os.path.join(self.output_dir, "ingredients.jsonl")
        with open(filepath, "w", encoding="utf-8") as f:
            for i in range(count):
                base = TYPICAL_INGREDIENTS[i % len(TYPICAL_INGREDIENTS)]
                ingredient = {
                    "ingredient_id": _generate_doc_id("ing", i),
                    "act_code": base["code"], "cn_name": base["cn"],
                    "cas_number": base["cas"],
                    "category": random.choice(INGREDIENT_CATEGORIES),
                    "safety_info": {"level": base["safety"], "limit": base["limit"]},
                    "data_class": "SYNTHETIC", "disclaimer": SYNTHETIC_DATA_NOTICE,
                    "carries_real_regulatory_claims": False,
                    "role_mask": _random_role_mask(), "dept_mask": _random_dept_mask(),
                }
                f.write(json.dumps(ingredient, ensure_ascii=False) + "\n")
                self._metadata.append({
                    "doc_id": ingredient["ingredient_id"], "doc_type": "ingredient",
                    "title": f"{ingredient['cn_name']} ({ingredient['act_code']})",
                    "ingredient_id": ingredient["ingredient_id"],
                    "data_class": "SYNTHETIC", "carries_real_regulatory_claims": False,
                    "role_mask": ingredient["role_mask"], "dept_mask": ingredient["dept_mask"],
                    "doc_version_epoch": "20260601_01", "status": "active",
                })

    def generate_formulas(self, count: int = 250):
        filepath = os.path.join(self.output_dir, "formulas.jsonl")
        with open(filepath, "w", encoding="utf-8") as f:
            for i in range(count):
                base = TYPICAL_FORMULAS[i % len(TYPICAL_FORMULAS)]
                formula = {
                    "formula_id": _generate_doc_id("fml", i),
                    "name": f"{base['name']} v{random.randint(1, 5)}",
                    "category": base["category"],
                    "ingredients": [{"name": ing, "percentage": round(random.uniform(0.1, 15.0), 2)} for ing in base["key_ingredients"]],
                    "data_class": "SYNTHETIC", "carries_real_regulatory_claims": False,
                    "role_mask": _random_role_mask(), "dept_mask": _random_dept_mask(),
                }
                f.write(json.dumps(formula, ensure_ascii=False) + "\n")
                self._metadata.append({
                    "doc_id": formula["formula_id"], "doc_type": "formula",
                    "title": formula["name"], "category": formula["category"],
                    "data_class": "SYNTHETIC", "carries_real_regulatory_claims": False,
                    "role_mask": formula["role_mask"], "dept_mask": formula["dept_mask"],
                    "doc_version_epoch": "20260601_01", "status": "active",
                })

    def generate_metadata(self):
        metadata_path = os.path.join(self.output_dir, "metadata.jsonl")
        with open(metadata_path, "w", encoding="utf-8") as f:
            for entry in self._metadata:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        notice_path = os.path.join(self.output_dir, "DATA_PROVENANCE.md")
        with open(notice_path, "w", encoding="utf-8") as f:
            f.write(
                "# 生成数据说明 — 合成夹具\n\n"
                f"{SYNTHETIC_DATA_NOTICE}\n\n"
                "本目录由 `data/mock_generator.py` 生成，其中的标准号（`DEMO-STD-*` / "
                "`DEMO-GRP-*` / `DEMO-REQ-*` 等）、原料编号（`DEMO-ACT-*`）、CAS 占位"
                "符（`000-00-0`）、含量与限量均为**虚构占位值**，与任何真实法规、标准或"
                "化学物质无关，未经任何监管机构或第三方标准原文校对。\n\n"
                "唯一目的：给切分、检索、RBAC 过滤与双 Gate 提供形状真实的输入。\n"
                "**不得**用于配方开发、合规判定、成分限用判断或任何面向消费者的答复。\n"
            )

    def generate_all(self, count_per_type: int = 50):
        self._metadata = []
        self.generate_regulations(count=min(count_per_type, 100))
        self.generate_ingredients(count=max(count_per_type * 10, 500))
        self.generate_formulas(count=max(count_per_type * 5, 250))
        self.generate_metadata()
        return {
            "regulations": len([m for m in self._metadata if m["doc_type"] == "regulation"]),
            "ingredients": len([m for m in self._metadata if m["doc_type"] == "ingredient"]),
            "formulas": len([m for m in self._metadata if m["doc_type"] == "formula"]),
            "metadata_path": os.path.join(self.output_dir, "metadata.jsonl"),
            "provenance_path": os.path.join(self.output_dir, "DATA_PROVENANCE.md"),
            "data_class": "SYNTHETIC",
            "carries_real_regulatory_claims": False,
        }

    def _generate_regulation_content(self, sys_info: dict, code: str) -> str:
        sections = [
            f"1 范围\n本示例文本规定了{random.choice(['化妆品', '护肤品', '洗涤用品'])}的示例技术要求。",
            "2 规范性引用文件\n本示例文本不引用任何真实标准文件。",
            f"3 术语和定义\n3.1 示例术语 demo-term\n{DEMO_DEFINITION}",
            f"4 技术要求\n4.1 感官指标\n外观：{random.choice(['乳白色', '透明', '淡黄色'])}液体或膏体。",
            f"4.2 理化指标\npH值：{round(random.uniform(4.0, 8.5), 1)}-{round(random.uniform(5.0, 9.0), 1)}",
            f"4.3 微生物指标\n菌落总数：≤{random.choice([500, 1000, 2000])} CFU/g（示例值）",
            "4.4 示例示踪物限量\nDEMO-TRACE-PB：≤10 mg/kg（虚构示例值）\n"
            "DEMO-TRACE-AS：≤2 mg/kg（虚构示例值）\n"
            "DEMO-TRACE-HG：≤1 mg/kg（虚构示例值）",
        ]
        return "\n\n".join(sections)


if __name__ == "__main__":
    import sys
    output = sys.argv[1] if len(sys.argv) > 1 else "data/mock_data"
    gen = MockDataGenerator(output_dir=output)
    result = gen.generate_all()
    print(f"Mock 数据生成完成: 法规 {result['regulations']} 份, 成分 {result['ingredients']} 条, 配方 {result['formulas']} 个")
    print(SYNTHETIC_DATA_NOTICE)
