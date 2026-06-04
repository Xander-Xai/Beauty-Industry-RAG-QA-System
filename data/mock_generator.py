"""Mock data generator for cosmetics RAG system testing."""
import json
import os
import random

REGULATION_SYSTEMS = [
    {"code": "GB/T", "name": "国家标准", "count": 50},
    {"code": "QB/T", "name": "行业标准", "count": 30},
    {"code": "GB", "name": "国家强制标准", "count": 15},
    {"code": "HFJB", "name": "化妆品技术规范", "count": 10},
    {"code": "NMPA", "name": "药监局公告", "count": 10},
    {"code": "EU", "name": "欧盟法规", "count": 8},
    {"code": "FDA", "name": "美国FDA法规", "count": 5},
    {"code": "ASEAN", "name": "东盟化妆品指令", "count": 5},
]

INGREDIENT_CATEGORIES = [
    "保湿剂", "乳化剂", "防腐剂", "香精", "表面活性剂",
    "增稠剂", "抗氧化剂", "紫外线吸收剂", "着色剂", "调理剂",
    "美白成分", "抗衰老成分", "舒缓成分", "去角质成分", "收敛剂",
]

ROLE_MAP = {"admin": 0x01, "rd": 0x02, "quality": 0x04, "regulation": 0x08, "sales": 0x10}
DEPT_MAP = {"研发部": 0x01, "品质部": 0x02, "法规部": 0x04, "销售部": 0x08, "市场部": 0x10}

TYPICAL_INGREDIENTS = [
    {"inci": "Nicotinamide", "cn": "烟酰胺", "cas": "98-92-0", "limit": "≤5%", "safety": "安全"},
    {"inci": "Hyaluronic Acid", "cn": "透明质酸钠", "cas": "9067-32-7", "limit": "≤2%", "safety": "安全"},
    {"inci": "Salicylic Acid", "cn": "水杨酸", "cas": "69-72-7", "limit": "≤2%(驻留)", "safety": "限用"},
    {"inci": "Retinol", "cn": "视黄醇", "cas": "68-26-8", "limit": "≤0.3%", "safety": "限用"},
    {"inci": "Arbutin", "cn": "熊果苷", "cas": "497-76-7", "limit": "≤7%", "safety": "安全"},
    {"inci": "Kojic Acid", "cn": "曲酸", "cas": "501-30-4", "limit": "≤2%", "safety": "限用"},
    {"inci": "Triclosan", "cn": "三氯生", "cas": "3380-34-5", "limit": "禁用", "safety": "禁用"},
    {"inci": "Formaldehyde", "cn": "甲醛", "cas": "50-00-0", "limit": "禁用", "safety": "禁用"},
    {"inci": "Lead", "cn": "铅", "cas": "7439-92-1", "limit": "≤10mg/kg", "safety": "限用"},
    {"inci": "Mercury", "cn": "汞", "cas": "7439-97-6", "limit": "≤1mg/kg", "safety": "限用"},
]

TYPICAL_FORMULAS = [
    {"name": "烟酰胺亮肤精华液", "category": "护肤", "key_ingredients": ["烟酰胺", "透明质酸钠", "甘油"]},
    {"name": "水杨酸清透洁面乳", "category": "洗护", "key_ingredients": ["水杨酸", "椰油酰胺丙基甜菜碱"]},
    {"name": "视黄醇抗皱面霜", "category": "护肤", "key_ingredients": ["视黄醇", "角鲨烷", "神经酰胺"]},
    {"name": "熊果苷美白面膜", "category": "护肤", "key_ingredients": ["熊果苷", "烟酰胺", "甘草酸二钾"]},
    {"name": "持久控油粉底液", "category": "彩妆", "key_ingredients": ["环戊硅氧烷", "二氧化钛", "氧化铁"]},
    {"name": "温和卸妆水", "category": "洗护", "key_ingredients": ["PEG-6 辛酸/癸酸甘油酯", "甘油"]},
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
            code = f"{sys_info['code']} {random.randint(1000, 9999)}-{random.randint(2015, 2025)}"
            title = f"{sys_info['name']} - {code} 化妆品相关标准"
            content = self._generate_regulation_content(sys_info, code)
            filepath = os.path.join(out_dir, f"{doc_id}.txt")
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(f"# {title}\n\n{content}")
            self._metadata.append({
                "doc_id": doc_id, "doc_type": "regulation", "file_path": filepath,
                "title": title, "law_id": code, "system": sys_info["code"],
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
                    "inci_name": base["inci"], "cn_name": base["cn"],
                    "cas_number": base["cas"],
                    "category": random.choice(INGREDIENT_CATEGORIES),
                    "safety_info": {"level": base["safety"], "limit": base["limit"]},
                    "role_mask": _random_role_mask(), "dept_mask": _random_dept_mask(),
                }
                f.write(json.dumps(ingredient, ensure_ascii=False) + "\n")
                self._metadata.append({
                    "doc_id": ingredient["ingredient_id"], "doc_type": "ingredient",
                    "title": f"{ingredient['cn_name']} ({ingredient['inci_name']})",
                    "ingredient_id": ingredient["ingredient_id"],
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
                    "role_mask": _random_role_mask(), "dept_mask": _random_dept_mask(),
                }
                f.write(json.dumps(formula, ensure_ascii=False) + "\n")
                self._metadata.append({
                    "doc_id": formula["formula_id"], "doc_type": "formula",
                    "title": formula["name"], "category": formula["category"],
                    "role_mask": formula["role_mask"], "dept_mask": formula["dept_mask"],
                    "doc_version_epoch": "20260601_01", "status": "active",
                })

    def generate_metadata(self):
        metadata_path = os.path.join(self.output_dir, "metadata.jsonl")
        with open(metadata_path, "w", encoding="utf-8") as f:
            for entry in self._metadata:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

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
        }

    def _generate_regulation_content(self, sys_info: dict, code: str) -> str:
        sections = [
            f"1 范围\n本标准规定了{random.choice(['化妆品', '护肤品', '洗涤用品'])}的技术要求。",
            f"2 规范性引用文件\n下列文件对于本文件的应用是必不可少的。",
            f"3 术语和定义\n3.1 化妆品 cosmetics\n以涂擦、喷洒或者其他类似的方法，散布于人体表面的物品。",
            f"4 技术要求\n4.1 感官指标\n外观：{random.choice(['乳白色', '透明', '淡黄色'])}液体或膏体。",
            f"4.2 理化指标\npH值：{round(random.uniform(4.0, 8.5), 1)}-{round(random.uniform(5.0, 9.0), 1)}",
            f"4.3 微生物指标\n菌落总数：≤{random.choice([500, 1000, 2000])} CFU/g",
            f"4.4 有害物质限量\n铅(Pb)：≤10 mg/kg\n砷(As)：≤2 mg/kg\n汞(Hg)：≤1 mg/kg",
        ]
        return "\n\n".join(sections)


if __name__ == "__main__":
    import sys
    output = sys.argv[1] if len(sys.argv) > 1 else "data/mock_data"
    gen = MockDataGenerator(output_dir=output)
    result = gen.generate_all()
    print(f"Mock 数据生成完成: 法规 {result['regulations']} 份, 成分 {result['ingredients']} 条, 配方 {result['formulas']} 个")
