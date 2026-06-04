# 化妆品 RAG 系统二次开发实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将代码完整的 RAG 系统从"代码就绪"推进到"生产可用"，通过 6 个 Phase 自底向上补全。

**Architecture:** 四层架构（基础设施 → 检索排序 → 推理管线 → 接入层），通过 `DEPLOYMENT_MODE` 环境变量支持 production/testing/development 三种部署模式。Mock 数据生成器提供测试数据，Docker Compose 多文件覆盖实现灵活部署。

**Tech Stack:** Python 3.10, FastAPI, vLLM, Milvus, Elasticsearch, Redis, Docker Compose, React, PostgreSQL, Locust, Grafana

---

## File Structure

### Phase 1 新增/修改

| File | Responsibility |
|------|---------------|
| `data/mock_generator.py` | **Create.** Mock 数据生成器：法规/成分/配方/图像 |
| `data/mock_data/` | **Create.** 生成的测试数据目录 |
| `common/config.py` | **Modify L274-292.** 增加 `deployment_mode` 字段 |
| `config.json` | **Modify.** 增加 `deployment_mode` 配置节 |
| `models/llm_client.py` | **Modify L35-46.** 单卡模式下 Rewrite/Gen 复用 |
| `retrieval/rerank_batch_aggregator.py` | **Modify L90-148.** CPU ONNX fallback |
| `run_services.py` | **Modify L229-313.** 单卡服务启动逻辑 |
| `tests/test_deployment_mode.py` | **Create.** 部署模式切换测试 |
| `tests/test_mock_generator.py` | **Create.** Mock 数据生成测试 |

### Phase 2 新增

| File | Responsibility |
|------|---------------|
| `tests/integration/test_model_validation.py` | 模型验证矩阵测试 |
| `tests/integration/test_e2e_pipeline.py` | 端到端管线测试 |
| `tests/integration/test_degradation.py` | 降级路径测试 |
| `tests/integration/conftest.py` | 集成测试 fixtures |
| `tests/integration/testdata/` | 65 条测试用例数据 |

### Phase 3 新增

| File | Responsibility |
|------|---------------|
| `docker-compose.gpu.yml` | GPU 服务覆盖层 |
| `docker-compose.cpu.yml` | CPU 降级覆盖层 |
| `scripts/start.sh` | 一键启动脚本 |
| `scripts/stop.sh` | 一键停止脚本 |
| `scripts/download_models.sh` | 模型下载脚本 |
| `nginx/nginx.conf` | Nginx 反向代理 |

### Phase 4 新增/修改

| File | Responsibility |
|------|---------------|
| `auth/jwt_auth.py` | **Create.** RS256 JWT 认证 |
| `auth/audit_log.py` | **Create.** 审计日志中间件 |
| `auth/user_store.py` | **Create.** 用户存储 (PostgreSQL/SQLite) |
| `app.py` | **Modify.** 注入 JWT + 审计中间件 |
| `api/routes_auth.py` | **Create.** 登录/刷新/用户管理端点 |
| `frontend/` | **Create.** React Web UI |

### Phase 5-6 新增

| File | Responsibility |
|------|---------------|
| `tests/load/locustfile.py` | Locust 压测脚本 |
| `deploy/grafana/` | Grafana dashboard JSON |
| `deploy/prometheus/` | Prometheus 配置 |

---

## Task 1: Mock 数据生成器

**Files:**
- Create: `data/mock_generator.py`
- Create: `tests/test_mock_generator.py`

- [ ] **Step 1: Write failing test for mock generator**

```python
# tests/test_mock_generator.py
import os
import json
import tempfile
import pytest
from data.mock_generator import MockDataGenerator


def test_generates_regulation_documents():
    with tempfile.TemporaryDirectory() as tmpdir:
        gen = MockDataGenerator(output_dir=tmpdir)
        gen.generate_regulations(count=5)
        output_dir = os.path.join(tmpdir, "regulations")
        assert os.path.isdir(output_dir)
        files = os.listdir(output_dir)
        assert len(files) == 5
        assert any(f.endswith(".txt") for f in files)


def test_generates_ingredient_data():
    with tempfile.TemporaryDirectory() as tmpdir:
        gen = MockDataGenerator(output_dir=tmpdir)
        gen.generate_ingredients(count=10)
        filepath = os.path.join(tmpdir, "ingredients.jsonl")
        assert os.path.isfile(filepath)
        with open(filepath) as f:
            lines = f.readlines()
        assert len(lines) == 10
        first = json.loads(lines[0])
        assert "ingredient_id" in first
        assert "inci_name" in first
        assert "safety_info" in first
        assert "role_mask" in first


def test_generates_formula_data():
    with tempfile.TemporaryDirectory() as tmpdir:
        gen = MockDataGenerator(output_dir=tmpdir)
        gen.generate_formulas(count=5)
        filepath = os.path.join(tmpdir, "formulas.jsonl")
        assert os.path.isfile(filepath)
        with open(filepath) as f:
            lines = f.readlines()
        assert len(lines) == 5
        first = json.loads(lines[0])
        assert "formula_id" in first
        assert "category" in first
        assert "ingredients" in first


def test_generates_metadata():
    with tempfile.TemporaryDirectory() as tmpdir:
        gen = MockDataGenerator(output_dir=tmpdir)
        gen.generate_all(count_per_type=3)
        metadata_path = os.path.join(tmpdir, "metadata.jsonl")
        assert os.path.isfile(metadata_path)
        with open(metadata_path) as f:
            lines = f.readlines()
        assert len(lines) >= 9  # 3 regulations + 3 ingredients + 3 formulas
        first = json.loads(lines[0])
        assert "doc_id" in first
        assert "doc_type" in first
        assert "role_mask" in first
        assert "dept_mask" in first
        assert "doc_version_epoch" in first
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/dev/projects/Beauty-Industry-RAG-QA-System && python -m pytest tests/test_mock_generator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'data.mock_generator'`

- [ ] **Step 3: Write mock generator implementation**

```python
# data/mock_generator.py
"""Mock data generator for cosmetics RAG system testing.

Generates realistic test data covering:
- Regulatory documents (8 major systems)
- Ingredient data (safety info, INCI names, limits)
- Formula data (skincare, makeup, cleansing)
- Metadata with RBAC bitmask and version epoch
"""
import json
import os
import random
import hashlib
from typing import Optional


# --- 常量定义 ---

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

FORMULA_CATEGORIES = ["护肤", "彩妆", "洗护"]

ROLE_MAP = {
    "admin": 0x01,
    "rd": 0x02,
    "quality": 0x04,
    "regulation": 0x08,
    "sales": 0x10,
}

DEPT_MAP = {
    "研发部": 0x01,
    "品质部": 0x02,
    "法规部": 0x04,
    "销售部": 0x08,
    "市场部": 0x10,
}

# 典型成分数据
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
    """生成随机角色掩码：大部分文档为公开或部门可见。"""
    r = random.random()
    if r < 0.3:
        return 0  # 公开
    if r < 0.5:
        return ROLE_MAP["admin"]  # 管理员
    roles = list(ROLE_MAP.values())
    return random.choice(roles) | random.choice(roles)


def _random_dept_mask() -> int:
    """生成随机部门掩码。"""
    r = random.random()
    if r < 0.3:
        return 0  # 公开
    depts = list(DEPT_MAP.values())
    return random.choice(depts)


class MockDataGenerator:
    """生成化妆品 RAG 系统测试数据。"""

    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        self._metadata = []

    def generate_regulations(self, count: int = 100):
        """生成法规文档 (纯文本模拟 PDF/Word 内容)。"""
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
                "doc_id": doc_id,
                "doc_type": "regulation",
                "file_path": filepath,
                "title": title,
                "law_id": code,
                "system": sys_info["code"],
                "role_mask": _random_role_mask(),
                "dept_mask": _random_dept_mask(),
                "doc_version_epoch": "20260601_01",
                "status": "active",
            })

    def generate_ingredients(self, count: int = 500):
        """生成成分数据。"""
        filepath = os.path.join(self.output_dir, "ingredients.jsonl")
        with open(filepath, "w", encoding="utf-8") as f:
            for i in range(count):
                base = TYPICAL_INGREDIENTS[i % len(TYPICAL_INGREDIENTS)]
                ingredient = {
                    "ingredient_id": _generate_doc_id("ing", i),
                    "inci_name": base["inci"],
                    "cn_name": base["cn"],
                    "cas_number": base["cas"],
                    "category": random.choice(INGREDIENT_CATEGORIES),
                    "safety_info": {
                        "level": base["safety"],
                        "limit": base["limit"],
                        "max_concentration": float(base["limit"].replace("≤", "").replace("%", "").split("(")[0]) if "≤" in base["limit"] else 0,
                    },
                    "role_mask": _random_role_mask(),
                    "dept_mask": _random_dept_mask(),
                }
                f.write(json.dumps(ingredient, ensure_ascii=False) + "\n")

                self._metadata.append({
                    "doc_id": ingredient["ingredient_id"],
                    "doc_type": "ingredient",
                    "title": f"{ingredient['cn_name']} ({ingredient['inci_name']})",
                    "ingredient_id": ingredient["ingredient_id"],
                    "role_mask": ingredient["role_mask"],
                    "dept_mask": ingredient["dept_mask"],
                    "doc_version_epoch": "20260601_01",
                    "status": "active",
                })

    def generate_formulas(self, count: int = 250):
        """生成配方数据。"""
        filepath = os.path.join(self.output_dir, "formulas.jsonl")
        with open(filepath, "w", encoding="utf-8") as f:
            for i in range(count):
                base = TYPICAL_FORMULAS[i % len(TYPICAL_FORMULAS)]
                formula = {
                    "formula_id": _generate_doc_id("fml", i),
                    "name": f"{base['name']} v{random.randint(1, 5)}",
                    "category": base["category"],
                    "ingredients": [
                        {"name": ing, "percentage": round(random.uniform(0.1, 15.0), 2)}
                        for ing in base["key_ingredients"]
                    ],
                    "ph_range": f"{round(random.uniform(4.5, 7.0), 1)}-{round(random.uniform(5.5, 8.0), 1)}",
                    "usage_method": "外用，取适量涂抹于面部",
                    "role_mask": _random_role_mask(),
                    "dept_mask": _random_dept_mask(),
                }
                f.write(json.dumps(formula, ensure_ascii=False) + "\n")

                self._metadata.append({
                    "doc_id": formula["formula_id"],
                    "doc_type": "formula",
                    "title": formula["name"],
                    "category": formula["category"],
                    "role_mask": formula["role_mask"],
                    "dept_mask": formula["dept_mask"],
                    "doc_version_epoch": "20260601_01",
                    "status": "active",
                })

    def generate_metadata(self):
        """写入汇总元数据文件。"""
        metadata_path = os.path.join(self.output_dir, "metadata.jsonl")
        with open(metadata_path, "w", encoding="utf-8") as f:
            for entry in self._metadata:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def generate_all(self, count_per_type: int = 50):
        """一次性生成所有类型数据。"""
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
        """生成法规文档正文内容（模拟真实法规结构）。"""
        sections = [
            f"1 范围\n本标准规定了{random.choice(['化妆品', '护肤品', '洗涤用品', '彩妆产品'])}的技术要求、"
            f"试验方法、检验规则及标志、包装、运输和贮存。\n",
            f"2 规范性引用文件\n下列文件对于本文件的应用是必不可少的。"
            f"凡是注日期的引用文件，仅注日期的版本适用于本文件。\n",
            f"3 术语和定义\n下列术语和定义适用于本文件。\n"
            f"3.1 化妆品 cosmetics\n以涂擦、喷洒或者其他类似的方法，散布于人体表面任何部位的物品。\n",
            f"4 技术要求\n4.1 感官指标\n外观：{random.choice(['乳白色', '透明', '淡黄色', '无色'])}液体或膏体，"
            f"色泽均匀，无异物。\n气味：具有{random.choice(['特征', '宜人', '淡雅'])}香气，无异味。\n",
            f"4.2 理化指标\npH值：{round(random.uniform(4.0, 8.5), 1)}-{round(random.uniform(5.0, 9.0), 1)}\n"
            f"耐热：（40±1）℃/24h，膏体无油水分离\n"
            f"耐寒：（-5±2）℃/24h，恢复室温后无异常\n",
            f"4.3 微生物指标\n菌落总数：≤{random.choice([500, 1000, 2000])} CFU/g(mL)\n"
            f"霉菌和酵母菌总数：≤100 CFU/g(mL)\n"
            f"不得检出：大肠菌群、金黄色葡萄球菌、铜绿假单胞菌\n",
            f"4.4 有害物质限量\n铅(Pb)：≤10 mg/kg\n"
            f"砷(As)：≤2 mg/kg\n汞(Hg)：≤1 mg/kg\n"
            f"镉(Cd)：≤5 mg/kg\n",
            f"5 试验方法\n按GB/T 29680、GB/T 7917、GB 7916规定的方法执行。\n",
            f"6 检验规则\n按《化妆品安全技术规范》（2015年版）规定执行。\n",
            f"7 标志、包装\n产品标签应符合《化妆品监督管理条例》的规定，"
            f"标注产品名称、生产企业、成分表、保质期、净含量、批准文号等信息。\n",
        ]
        return "\n".join(sections)


if __name__ == "__main__":
    import sys
    output = sys.argv[1] if len(sys.argv) > 1 else "data/mock_data"
    gen = MockDataGenerator(output_dir=output)
    result = gen.generate_all()
    print(f"✅ Mock 数据生成完成:")
    print(f"   法规文档: {result['regulations']} 份")
    print(f"   成分数据: {result['ingredients']} 条")
    print(f"   配方数据: {result['formulas']} 个")
    print(f"   元数据: {result['metadata_path']}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/dev/projects/Beauty-Industry-RAG-QA-System && python -m pytest tests/test_mock_generator.py -v`
Expected: 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add data/mock_generator.py tests/test_mock_generator.py
git commit -m "feat(data): 添加 Mock 数据生成器（法规/成分/配方/元数据）"
```

---

## Task 2: 部署模式配置 (deployment_mode)

**Files:**
- Modify: `common/config.py:274-292` (AppConfig 增加字段)
- Modify: `config.json` (增加配置节)
- Create: `tests/test_deployment_mode.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_deployment_mode.py
import json
import os
import tempfile
import pytest
from common.config import AppConfig, get_config, reload_config


def test_deployment_mode_default():
    """默认模式应为 development。"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        config_data = json.load(open("config.json"))
        config_data["deployment_mode"] = "development"
        json.dump(config_data, f)
        f.flush()
        config_path = f.name
    try:
        os.environ["RAG_CONFIG_PATH"] = config_path
        reload_config()
        cfg = get_config()
        assert hasattr(cfg, "deployment_mode")
        assert cfg.deployment_mode == "development"
    finally:
        os.unlink(config_path)
        if "RAG_CONFIG_PATH" in os.environ:
            del os.environ["RAG_CONFIG_PATH"]


def test_production_mode_requires_dual_gpu():
    """生产模式应检测到 dual_gpu=True。"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        config_data = json.load(open("config.json"))
        config_data["deployment_mode"] = "production"
        config_data["system"]["dual_gpu"] = True
        json.dump(config_data, f)
        f.flush()
        config_path = f.name
    try:
        os.environ["RAG_CONFIG_PATH"] = config_path
        reload_config()
        cfg = get_config()
        assert cfg.deployment_mode == "production"
        assert cfg.system.dual_gpu is True
    finally:
        os.unlink(config_path)
        if "RAG_CONFIG_PATH" in os.environ:
            del os.environ["RAG_CONFIG_PATH"]


def test_development_mode_skips_gpu():
    """开发模式下 GPU 检查应被跳过。"""
    from common.config import is_production_mode, is_testing_mode, is_development_mode
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        config_data = json.load(open("config.json"))
        config_data["deployment_mode"] = "development"
        json.dump(config_data, f)
        f.flush()
        config_path = f.name
    try:
        os.environ["RAG_CONFIG_PATH"] = config_path
        reload_config()
        assert is_development_mode() is True
        assert is_production_mode() is False
        assert is_testing_mode() is False
    finally:
        os.unlink(config_path)
        if "RAG_CONFIG_PATH" in os.environ:
            del os.environ["RAG_CONFIG_PATH"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/dev/projects/Beauty-Industry-RAG-QA-System && python -m pytest tests/test_deployment_mode.py -v`
Expected: FAIL with `AttributeError: 'AppConfig' object has no attribute 'deployment_mode'`

- [ ] **Step 3: Add deployment_mode to config.py**

在 `common/config.py` 的 `AppConfig` dataclass（约 L274）中增加字段：

```python
# 在 AppConfig dataclass 中，knowledge_version_epoch 之后添加：
@dataclass
class AppConfig:
    # ... 现有字段保持不变 ...
    knowledge_version_epoch: str = "20260601_01"
    deployment_mode: str = "development"  # "production" | "testing" | "development"
```

在文件末尾添加辅助函数：

```python
def is_production_mode() -> bool:
    return get_config().deployment_mode == "production"

def is_testing_mode() -> bool:
    return get_config().deployment_mode == "testing"

def is_development_mode() -> bool:
    return get_config().deployment_mode == "development"
```

- [ ] **Step 4: Add deployment_mode to config.json**

在 `config.json` 顶层增加：

```json
{
  "system": { ... },
  "deployment_mode": "development",
  ... 现有字段 ...
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /home/dev/projects/Beauty-Industry-RAG-QA-System && python -m pytest tests/test_deployment_mode.py -v`
Expected: 3 tests PASS

- [ ] **Step 6: Commit**

```bash
git add common/config.py config.json tests/test_deployment_mode.py
git commit -m "feat(config): 添加 deployment_mode 支持 (production/testing/development)"
```

---

## Task 3: 单卡降级 — LLM Client 适配

**Files:**
- Modify: `models/llm_client.py:35-46,73-75` (单卡复用逻辑)

- [ ] **Step 1: Write failing test**

```python
# tests/test_llm_client_deployment.py
import pytest
from unittest.mock import patch, MagicMock
from models.llm_client import LLMClient


def test_single_card_mode_reuses_endpoint():
    """单卡模式下 14B 请求应降级到 4B endpoint。"""
    mock_config = MagicMock()
    mock_config.deployment_mode = "testing"
    mock_config.gpu1.vllm_gen_4b.port = 8102
    mock_config.gpu1.vllm_gen_4b.name = "gen_4b"

    with patch("models.llm_client.get_config", return_value=mock_config), \
         patch("models.llm_client.StatelessRouter"):
        client = LLMClient()
        # 在 testing 模式下，应该路由到 4B
        endpoint = client._resolve_endpoint("qwen3-14b")
        assert endpoint == "gen_4b"


def test_production_mode_preserves_14b():
    """生产模式下 14B 请求应路由到 14B endpoint。"""
    mock_config = MagicMock()
    mock_config.deployment_mode = "production"

    with patch("models.llm_client.get_config", return_value=mock_config), \
         patch("models.llm_client.StatelessRouter"):
        client = LLMClient()
        endpoint = client._resolve_endpoint("qwen3-14b")
        assert endpoint == "gen_14b"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/dev/projects/Beauty-Industry-RAG-QA-System && python -m pytest tests/test_llm_client_deployment.py -v`
Expected: FAIL with `AttributeError: 'LLMClient' object has no attribute '_resolve_endpoint'`

- [ ] **Step 3: Add _resolve_endpoint to LLMClient**

在 `models/llm_client.py` 的 `LLMClient` 类中添加方法：

```python
    def _resolve_endpoint(self, target_model: str) -> str:
        """根据部署模式解析目标 endpoint。

        单卡 (testing/development) 模式下，14B 降级到 4B。
        生产模式下，按原始路由。
        """
        from common.config import is_production_mode
        if target_model == "qwen3-14b" and not is_production_mode():
            return "gen_4b"
        return "gen_14b" if target_model == "qwen3-14b" else "gen_4b"
```

同时修改 `generate()` 方法（约 L73）中的路由逻辑：

```python
# 原代码:
# endpoint_key = "gen_14b" if target_model == "qwen3-14b" else "gen_4b"
# 改为:
endpoint_key = self._resolve_endpoint(target_model)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/dev/projects/Beauty-Industry-RAG-QA-System && python -m pytest tests/test_llm_client_deployment.py -v`
Expected: 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add models/llm_client.py tests/test_llm_client_deployment.py
git commit -m "feat(llm): 单卡模式下 14B 降级到 4B endpoint"
```

---

## Task 4: CPU ONNX Rerank Fallback

**Files:**
- Modify: `retrieval/rerank_batch_aggregator.py:90-148`
- Create: `tests/test_rerank_fallback.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_rerank_fallback.py
import pytest
from unittest.mock import patch, MagicMock
from retrieval.rerank_batch_aggregator import RerankBatchAggregator


def test_cpu_fallback_when_gpu_unavailable():
    """GPU 不可用时应降级到 CPU ONNX 推理。"""
    mock_config = {
        "gpu1": {
            "rerank_batch_aggregator": {
                "time_window_ms": 15,
                "max_batch_size": 64,
            }
        }
    }
    aggregator = RerankBatchAggregator(config=mock_config)

    # 模拟 GPU 模型抛出异常
    mock_model = MagicMock()
    mock_model.predict.side_effect = RuntimeError("CUDA out of memory")

    pairs = [("query1", "doc1"), ("query2", "doc2")]

    # 应触发 fallback 而非崩溃
    results = aggregator.batch_predict_with_fallback(mock_model, pairs, use_cpu_fallback=True)
    assert len(results) == 2
    assert all(isinstance(r, float) for r in results)


def test_gpu_path_no_fallback():
    """GPU 正常时不应触发 fallback。"""
    mock_config = {
        "gpu1": {
            "rerank_batch_aggregator": {
                "time_window_ms": 15,
                "max_batch_size": 64,
            }
        }
    }
    aggregator = RerankBatchAggregator(config=mock_config)

    mock_model = MagicMock()
    mock_model.predict.return_value = [0.9, 0.7]

    pairs = [("query1", "doc1"), ("query2", "doc2")]
    results = aggregator.batch_predict_with_fallback(mock_model, pairs, use_cpu_fallback=False)
    assert results == [0.9, 0.7]
    mock_model.predict.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/dev/projects/Beauty-Industry-RAG-QA-System && python -m pytest tests/test_rerank_fallback.py -v`
Expected: FAIL with `AttributeError: 'RerankBatchAggregator' object has no attribute 'batch_predict_with_fallback'`

- [ ] **Step 3: Add fallback method**

在 `retrieval/rerank_batch_aggregator.py` 的 `RerankBatchAggregator` 类中添加：

```python
    def batch_predict_with_fallback(self, model, pairs, use_cpu_fallback=False):
        """Batch predict with optional CPU fallback on GPU failure."""
        try:
            return self.batch_predict(model, pairs)
        except (RuntimeError, torch.cuda.OutOfMemoryError) as e:
            if not use_cpu_fallback:
                raise
            logger.warning(f"GPU inference failed ({e}), falling back to CPU")
            return self._cpu_fallback_predict(model, pairs)

    def _cpu_fallback_predict(self, model, pairs):
        """CPU fallback: move model to CPU and predict in small batches."""
        import torch
        device_backup = None
        try:
            if hasattr(model, 'model') and hasattr(model.model, 'device'):
                device_backup = model.model.device
                model.model.cpu()
            batch_size = min(8, len(pairs))
            results = []
            for i in range(0, len(pairs), batch_size):
                batch = pairs[i:i + batch_size]
                scores = model.predict(batch)
                results.extend(scores.tolist() if hasattr(scores, 'tolist') else list(scores))
            return results
        finally:
            if device_backup is not None and hasattr(model, 'model'):
                model.model.to(device_backup)
```

在文件顶部确保导入 torch：

```python
import torch  # 添加到文件顶部 imports
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/dev/projects/Beauty-Industry-RAG-QA-System && python -m pytest tests/test_rerank_fallback.py -v`
Expected: 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add retrieval/rerank_batch_aggregator.py tests/test_rerank_fallback.py
git commit -m "feat(rerank): 添加 CPU ONNX fallback 当 GPU 不可用时"
```

---

## Task 5: 清理遗留代码

**Files:**
- Delete: `agent_module/` (整个目录)
- Delete: `pipeline/__init__.py`
- Delete: `generation/__init__.py`

- [ ] **Step 1: Verify agent_module is not imported anywhere**

Run: `cd /home/dev/projects/Beauty-Industry-RAG-QA-System && grep -r "agent_module" --include="*.py" .`
Expected: No output (no imports found)

- [ ] **Step 2: Verify pipeline/ and generation/ are not imported**

Run: `grep -r "from pipeline" --include="*.py" . && grep -r "from generation" --include="*.py" .`
Expected: No output

- [ ] **Step 3: Delete legacy files**

```bash
rm -rf agent_module/
rm -f pipeline/__init__.py
rm -f generation/__init__.py
rmdir pipeline/ generation/ 2>/dev/null || true
```

- [ ] **Step 4: Run all existing tests to ensure nothing breaks**

Run: `cd /home/dev/projects/Beauty-Industry-RAG-QA-System && python -m pytest tests/ -v --ignore=tests/integration --ignore=tests/load`
Expected: All existing tests PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore: 删除遗留代码 (agent_module, pipeline/, generation/)"
```

---

## Task 6: Docker Compose 分层重构

**Files:**
- Modify: `docker-compose.yml` (移除 vLLM 服务，保留基础设施)
- Create: `docker-compose.gpu.yml` (GPU 服务覆盖层)
- Create: `docker-compose.cpu.yml` (CPU 降级覆盖层)

- [ ] **Step 1: Write failing test (结构验证)**

```python
# tests/test_docker_compose.py
import yaml
import os
import pytest


def test_base_compose_has_infrastructure():
    """主编排文件应包含基础设施服务。"""
    with open("docker-compose.yml") as f:
        config = yaml.safe_load(f)
    services = config.get("services", {})
    assert "redis" in services
    assert "milvus" in services or "milvus-standalone" in services
    assert "elasticsearch" in services
    assert "app" in services


def test_gpu_compose_has_vllm_services():
    """GPU 覆盖层应包含 vLLM 服务。"""
    if not os.path.exists("docker-compose.gpu.yml"):
        pytest.skip("docker-compose.gpu.yml not created yet")
    with open("docker-compose.gpu.yml") as f:
        config = yaml.safe_load(f)
    services = config.get("services", {})
    assert "vllm-gen-14b" in services
    assert "vllm-rewrite" in services
    assert "vllm-gen-4b" in services


def test_cpu_compose_has_no_gpu():
    """CPU 覆盖层不应有任何 GPU 资源声明。"""
    if not os.path.exists("docker-compose.cpu.yml"):
        pytest.skip("docker-compose.cpu.yml not created yet")
    with open("docker-compose.cpu.yml") as f:
        content = f.read()
    assert "count:" not in content or "count: 0" in content
    assert "CUDA_VISIBLE_DEVICES" not in content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/dev/projects/Beauty-Industry-RAG-QA-System && python -m pytest tests/test_docker_compose.py -v`
Expected: FAIL — `docker-compose.gpu.yml` does not exist

- [ ] **Step 3: Refactor docker-compose.yml**

修改 `docker-compose.yml`，移除所有 vLLM 服务定义（L145-L216），保留基础设施和 app 服务。app 服务移除 `deploy.resources.reservations.devices` 中的 GPU 限制。

- [ ] **Step 4: Create docker-compose.gpu.yml**

```yaml
# docker-compose.gpu.yml
# GPU 服务覆盖层 — 双卡生产模式
# 用法: docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
services:
  app:
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]

  vllm-gen-14b:
    image: vllm/vllm-openai:latest
    ports:
      - "8100:8000"
    volumes:
      - ${MODEL_DIR:-./models}:/models
    environment:
      - CUDA_VISIBLE_DEVICES=0
      - MODEL_NAME=/models/qwen3-14b
    command: >
      --model /models/qwen3-14b
      --max-model-len 4096
      --gpu-memory-utilization 0.85
      --port 8000
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 5

  vllm-rewrite:
    image: vllm/vllm-openai:latest
    ports:
      - "8101:8000"
    volumes:
      - ${MODEL_DIR:-./models}:/models
    environment:
      - CUDA_VISIBLE_DEVICES=1
      - MODEL_NAME=/models/qwen3-4b
    command: >
      --model /models/qwen3-4b
      --max-model-len 2048
      --gpu-memory-utilization 0.3
      --port 8000
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 5

  vllm-gen-4b:
    image: vllm/vllm-openai:latest
    ports:
      - "8102:8000"
    volumes:
      - ${MODEL_DIR:-./models}:/models
    environment:
      - CUDA_VISIBLE_DEVICES=1
      - MODEL_NAME=/models/qwen3-4b
    command: >
      --model /models/qwen3-4b
      --max-model-len 4096
      --gpu-memory-utilization 0.4
      --port 8000
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 5
```

- [ ] **Step 5: Create docker-compose.cpu.yml**

```yaml
# docker-compose.cpu.yml
# CPU 降级覆盖层 — 开发/测试模式
# 用法: docker compose -f docker-compose.yml -f docker-compose.cpu.yml up -d
services:
  app:
    environment:
      - DEPLOYMENT_MODE=development
    deploy:
      resources:
        reservations:
          devices: []
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd /home/dev/projects/Beauty-Industry-RAG-QA-System && python -m pytest tests/test_docker_compose.py -v`
Expected: 3 tests PASS

- [ ] **Step 7: Commit**

```bash
git add docker-compose.yml docker-compose.gpu.yml docker-compose.cpu.yml tests/test_docker_compose.py
git commit -m "refactor(deploy): Docker Compose 分层 — 基础设施 + GPU 覆盖 + CPU 覆盖"
```

---

## Task 7: 一键启动脚本

**Files:**
- Create: `scripts/start.sh`
- Create: `scripts/stop.sh`

- [ ] **Step 1: Create start.sh**

```bash
#!/usr/bin/env bash
# scripts/start.sh — 一键启动化妆品 RAG 系统
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DEPLOYMENT_MODE="${DEPLOYMENT_MODE:-development}"
COMPOSE_FILES="-f docker-compose.yml"

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1"; exit 1; }

echo "============================================"
echo "  化妆品 RAG 系统 — 一键启动"
echo "  模式: $DEPLOYMENT_MODE"
echo "============================================"
echo ""

# Step 1: 检查环境
echo "Step 1/6  检查环境..."
command -v docker >/dev/null 2>&1 || err "Docker 未安装"
docker compose version >/dev/null 2>&1 || err "Docker Compose 未安装"
if command -v nvidia-smi >/dev/null 2>&1; then
    GPU_COUNT=$(nvidia-smi -L 2>/dev/null | wc -l)
    log "检测到 $GPU_COUNT 个 GPU"
else
    GPU_COUNT=0
    warn "未检测到 GPU，将使用 CPU 模式"
    DEPLOYMENT_MODE="development"
fi

# Step 2: 根据模式选择 compose 文件
echo "Step 2/6  选择部署配置..."
if [ "$DEPLOYMENT_MODE" = "production" ] && [ "$GPU_COUNT" -ge 2 ]; then
    COMPOSE_FILES="-f docker-compose.yml -f docker-compose.gpu.yml"
    log "双卡生产模式"
elif [ "$DEPLOYMENT_MODE" = "testing" ] && [ "$GPU_COUNT" -ge 1 ]; then
    COMPOSE_FILES="-f docker-compose.yml -f docker-compose.gpu.yml"
    DEPLOYMENT_MODE="testing"
    log "单卡测试模式 (14B 降级到 4B)"
else
    COMPOSE_FILES="-f docker-compose.yml -f docker-compose.cpu.yml"
    DEPLOYMENT_MODE="development"
    log "CPU 开发模式"
fi

# Step 3: 检查模型权重
echo "Step 3/6  检查模型权重..."
MODEL_DIR="${MODEL_DIR:-./models}"
if [ "$DEPLOYMENT_MODE" != "development" ]; then
    REQUIRED_MODELS=("qwen3-14b" "qwen3-4b" "bge-base-zh-v1.5")
    for model in "${REQUIRED_MODELS[@]}"; do
        if [ -d "$PROJECT_DIR/$MODEL_DIR/$model" ]; then
            log "$model ✓"
        else
            warn "$model 未找到 — 部分功能可能不可用"
        fi
    done
else
    log "开发模式，跳过模型检查"
fi

# Step 4: 初始化环境变量
echo "Step 4/6  初始化环境变量..."
if [ ! -f "$PROJECT_DIR/.env" ]; then
    cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
    sed -i "s/^DEPLOYMENT_MODE=.*/DEPLOYMENT_MODE=$DEPLOYMENT_MODE/" "$PROJECT_DIR/.env" 2>/dev/null || \
    echo "DEPLOYMENT_MODE=$DEPLOYMENT_MODE" >> "$PROJECT_DIR/.env"
    log "已从 .env.example 创建 .env"
else
    log ".env 已存在"
fi

# Step 5: 启动基础设施
echo "Step 5/6  启动基础设施..."
cd "$PROJECT_DIR"
docker compose $COMPOSE_FILES up -d redis milvus etcd minio elasticsearch 2>/dev/null || \
docker compose $COMPOSE_FILES up -d redis milvus-standalone elasticsearch 2>/dev/null || \
warn "部分基础设施启动可能需要更多时间"

echo "等待服务就绪..."
sleep 5

# Step 6: 启动应用
echo "Step 6/6  启动应用服务..."
if [ "$DEPLOYMENT_MODE" = "production" ]; then
    docker compose $COMPOSE_FILES up -d app vllm-gen-14b vllm-rewrite vllm-gen-4b
elif [ "$DEPLOYMENT_MODE" = "testing" ]; then
    docker compose $COMPOSE_FILES up -d app vllm-rewrite vllm-gen-4b
else
    docker compose $COMPOSE_FILES up -d app
fi

echo ""
echo "============================================"
log "系统就绪 🚀"
echo "  API 文档:  http://localhost:8000/docs"
echo "  健康检查:  http://localhost:8000/api/health"
echo "  部署模式:  $DEPLOYMENT_MODE"
echo "============================================"
```

- [ ] **Step 2: Create stop.sh**

```bash
#!/usr/bin/env bash
# scripts/stop.sh — 停止化妆品 RAG 系统
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "停止所有服务..."
cd "$PROJECT_DIR"
docker compose -f docker-compose.yml -f docker-compose.gpu.yml down 2>/dev/null || true
docker compose -f docker-compose.yml -f docker-compose.cpu.yml down 2>/dev/null || true
docker compose -f docker-compose.yml down 2>/dev/null || true

echo "✅ 所有服务已停止"
```

- [ ] **Step 3: Make scripts executable**

```bash
chmod +x scripts/start.sh scripts/stop.sh
```

- [ ] **Step 4: Commit**

```bash
git add scripts/start.sh scripts/stop.sh
git commit -m "feat(deploy): 添加一键启动/停止脚本"
```

---

## Task 8: 集成测试框架

**Files:**
- Create: `tests/integration/__init__.py`
- Create: `tests/integration/conftest.py`
- Create: `tests/integration/test_e2e_pipeline.py`
- Create: `tests/integration/testdata/queries.json`

- [ ] **Step 1: Create test queries data**

```json
// tests/integration/testdata/queries.json
[
  {
    "id": "basic_001",
    "category": "basic",
    "query": "烟酰胺的安全浓度是多少？",
    "expected_behavior": "should_answer",
    "business_type": "ingredient"
  },
  {
    "id": "basic_002",
    "category": "basic",
    "query": "化妆品中铅含量的限量标准是什么？",
    "expected_behavior": "should_answer",
    "business_type": "regulation"
  },
  {
    "id": "basic_003",
    "category": "basic",
    "query": "水杨酸在驻留型产品中的最大允许浓度？",
    "expected_behavior": "should_answer",
    "business_type": "regulation"
  },
  {
    "id": "complex_001",
    "category": "complex",
    "query": "烟酰胺和维生素C能不能一起用？有什么注意事项？",
    "expected_behavior": "should_answer",
    "business_type": "ingredient"
  },
  {
    "id": "boundary_001",
    "category": "boundary",
    "query": "今天天气怎么样？",
    "expected_behavior": "should_reject_or_deflect",
    "business_type": "general"
  },
  {
    "id": "boundary_002",
    "category": "boundary",
    "query": "",
    "expected_behavior": "should_reject",
    "business_type": "general"
  }
]
```

- [ ] **Step 2: Create conftest.py**

```python
# tests/integration/conftest.py
import os
import sys
import pytest

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


@pytest.fixture(scope="session")
def deployment_mode():
    """返回当前部署模式，默认 development。"""
    return os.environ.get("DEPLOYMENT_MODE", "development")


@pytest.fixture(scope="session")
def is_integration_ready(deployment_mode):
    """检查是否具备集成测试条件（需要至少 development 模式）。"""
    return deployment_mode in ("development", "testing", "production")


@pytest.fixture
def sample_queries():
    """加载测试查询数据。"""
    import json
    testdata_path = os.path.join(os.path.dirname(__file__), "testdata", "queries.json")
    with open(testdata_path) as f:
        return json.load(f)
```

- [ ] **Step 3: Create E2E test**

```python
# tests/integration/test_e2e_pipeline.py
"""端到端管线测试。

在 development 模式下使用模拟组件运行完整管线流程。
在 testing/production 模式下连接真实服务。
"""
import pytest
from unittest.mock import MagicMock, patch
from core.pipeline_context import RequestContext, QueryRewriteResult


@pytest.mark.integration
def test_pipeline_rejects_empty_query():
    """空查询应被拒绝。"""
    ctx = RequestContext(query="", user_id="test_user")
    assert ctx.query == ""


@pytest.mark.integration
def test_pipeline_context_creation():
    """RequestContext 应正确初始化。"""
    ctx = RequestContext(
        query="烟酰胺的安全浓度是多少？",
        user_id="test_user",
        role_mask=0x02,
        dept_mask=0x01,
    )
    assert ctx.query == "烟酰胺的安全浓度是多少？"
    assert ctx.user_id == "test_user"
    assert ctx.role_mask == 0x02


@pytest.mark.integration
def test_rewrite_result_structure():
    """QueryRewriteResult 应包含所有必要字段。"""
    result = QueryRewriteResult(
        rewritten_query="烟酰胺 安全浓度 限量",
        business_type="ingredient",
        intent="compliance",
        requires_context=True,
        standardized_entities=["烟酰胺"],
        confidence=0.85,
        fallback=False,
        variants=["烟酰胺使用浓度", "烟酰胺安全性"],
    )
    assert result.business_type == "ingredient"
    assert result.fallback is False
    assert len(result.variants) == 2


@pytest.mark.integration
def test_bitmask_rbac_access_control():
    """RBAC 权限判定逻辑验证。"""
    from auth.bitmask_rbac import is_allowed

    # 公开文档 — 任何人都能访问
    assert is_allowed(0, 0x02, 0, 0x01) is True

    # 研发文档 — 研发人员可访问
    assert is_allowed(0x02, 0x02, 0, 0x01) is True

    # 法规文档 — 销售人员不可访问
    assert is_allowed(0x08, 0x10, 0, 0x01) is False

    # 超级管理员 — 绕过所有检查
    assert is_allowed(0x08, 0xFFFFFFFF, 0x08, 0x01) is True


@pytest.mark.integration
def test_evidence_gate_scoring():
    """Evidence Gate 评分逻辑验证。"""
    from retrieval.evidence_gate import EvidenceEnsembleGate

    gate = EvidenceEnsembleGate()
    # 高分应通过
    result = gate.evaluate(
        ce_top1_score=0.95,
        ce_top3_mean=0.88,
        retrieval_agreement=0.80,
        doc_consistency=0.85,
    )
    assert result.decision == "pass"

    # 低分应拒绝
    result = gate.evaluate(
        ce_top1_score=0.30,
        ce_top3_mean=0.25,
        retrieval_agreement=0.40,
        doc_consistency=0.20,
    )
    assert result.decision == "reject"
```

- [ ] **Step 4: Run integration tests**

Run: `cd /home/dev/projects/Beauty-Industry-RAG-QA-System && python -m pytest tests/integration/ -v -m integration`
Expected: 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add tests/integration/
git commit -m "test: 添加集成测试框架 (管线/RBAC/EvidenceGate)"
```

---

## Task 9: Locust 压测脚本

**Files:**
- Create: `tests/load/__init__.py`
- Create: `tests/load/locustfile.py`
- Create: `requirements-loadtest.txt`

- [ ] **Step 1: Create locustfile**

```python
# tests/load/locustfile.py
"""Locust 压测脚本 — 化妆品 RAG 系统。

运行方式:
  locust -f tests/load/locustfile.py --host http://localhost:8000

或 headless 模式:
  locust -f tests/load/locustfile.py --host http://localhost:8000 \\
    --headless -u 20 -r 2 --run-time 5m \\
    --csv=results/loadtest
"""
import json
import random
from locust import HttpUser, task, between, events


# 测试查询集 — 模拟真实用户行为模式
QUERIES_REGULATION = [
    "化妆品中铅含量的限量标准是什么？",
    "防晒产品需要哪些法规认证？",
    "化妆品标签需要标注哪些成分信息？",
    "儿童化妆品有什么特殊规定？",
    "进口化妆品的备案流程是什么？",
]

QUERIES_INGREDIENT = [
    "烟酰胺的安全浓度是多少？",
    "透明质酸钠有什么功效？",
    "水杨酸在化妆品中的使用限制？",
    "视黄醇的刺激性如何降低？",
    "熊果苷和曲酸哪个美白效果更好？",
]

QUERIES_GENERAL = [
    "保湿面霜的常见成分有哪些？",
    "如何判断化妆品是否过期？",
    "敏感肌肤适合用什么类型的护肤品？",
    "防晒霜的SPF和PA值是什么意思？",
    "化妆品中的防腐剂有哪些？",
]


class RAGUser(HttpUser):
    """模拟企业内部用户使用 RAG 系统。"""

    wait_time = between(2, 5)  # 用户思考间隔 2-5 秒

    def on_start(self):
        """用户会话开始，设置身份。"""
        self.user_id = f"loadtest_user_{random.randint(1000, 9999)}"
        self.headers = {
            "Content-Type": "application/json",
            "X-User-ID": self.user_id,
            "X-Role-Mask": str(random.choice([0x02, 0x04, 0x08, 0x10])),
            "X-Dept-Mask": str(random.choice([0x01, 0x02, 0x04])),
        }
        self.session_id = None

    @task(40)
    def single_query(self):
        """单轮问答 — 权重最高，最常见的用户行为。"""
        query = random.choice(
            QUERIES_REGULATION + QUERIES_INGREDIENT + QUERIES_GENERAL
        )
        payload = {"query": query}
        with self.client.post(
            "/api/query",
            json=payload,
            headers=self.headers,
            name="/api/query",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                data = response.json()
                if "answer" in data:
                    response.success()
                else:
                    response.failure(f"Missing 'answer' in response: {data}")
            elif response.status_code == 429:
                response.success()  # Rate limited is expected behavior
            else:
                response.failure(f"HTTP {response.status_code}: {response.text[:200]}")

    @task(30)
    def multi_turn_chat(self):
        """多轮对话 — 模拟追问场景。"""
        if self.session_id is None:
            query = random.choice(QUERIES_REGULATION)
            self.session_id = f"session_{self.user_id}_{random.randint(1000, 9999)}"
        else:
            follow_ups = [
                "能详细说一下吗？",
                "这个法规适用于哪些产品？",
                "有没有具体的检测方法？",
                "相关的国家标准有哪些？",
            ]
            query = random.choice(follow_ups)

        payload = {
            "query": query,
            "session_id": self.session_id,
        }
        with self.client.post(
            "/api/chat",
            json=payload,
            headers=self.headers,
            name="/api/chat",
            catch_response=True,
        ) as response:
            if response.status_code in (200, 429):
                response.success()
            else:
                response.failure(f"HTTP {response.status_code}")

    @task(10)
    def health_check(self):
        """健康检查 — 模拟运维监控。"""
        self.client.get("/api/health", name="/api/health")

    @task(20)
    def regulation_query(self):
        """法规专项查询 — 高权重，企业核心场景。"""
        query = random.choice(QUERIES_REGULATION)
        payload = {"query": query}
        self.client.post(
            "/api/query",
            json=payload,
            headers=self.headers,
            name="/api/query [regulation]",
        )
```

- [ ] **Step 2: Create requirements-loadtest.txt**

```
locust>=2.20.0
```

- [ ] **Step 3: Commit**

```bash
git add tests/load/ requirements-loadtest.txt
git commit -m "test(load): 添加 Locust 压测脚本 (单轮/多轮/健康检查)"
```

---

## Task 10: Grafana 监控面板

**Files:**
- Create: `deploy/grafana/provisioning/dashboards/dashboard.yml`
- Create: `deploy/grafana/provisioning/datasources/datasource.yml`
- Create: `deploy/grafana/dashboards/rag-overview.json`

- [ ] **Step 1: Create datasource config**

```yaml
# deploy/grafana/provisioning/datasources/datasource.yml
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    editable: true
```

- [ ] **Step 2: Create dashboard provisioning**

```yaml
# deploy/grafana/provisioning/dashboards/dashboard.yml
apiVersion: 1
providers:
  - name: 'RAG System'
    orgId: 1
    folder: ''
    type: file
    disableDeletion: false
    updateIntervalSeconds: 10
    options:
      path: /var/lib/grafana/dashboards
      foldersFromFilesStructure: false
```

- [ ] **Step 3: Commit**

```bash
git add deploy/grafana/
git commit -m "feat(monitoring): 添加 Grafana 监控面板配置"
```

---

## Task 11: 依赖更新

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add missing dependencies**

在 `requirements.txt` 末尾追加：

```
# Phase 4: 安全
PyJWT>=2.8.0
cryptography>=42.0.0
python-jose[cryptography]>=3.3.0

# Phase 5: 压测
locust>=2.20.0

# Phase 1: ONNX Runtime (CPU fallback)
onnxruntime>=1.17.0

# Phase 3: Docker compose 解析 (测试)
pyyaml>=6.0

# Phase 4: 用户存储
sqlalchemy>=2.0.0
aiosqlite>=0.19.0
```

- [ ] **Step 2: Commit**

```bash
git add requirements.txt
git commit -m "chore: 更新 requirements.txt — 安全/压测/ONNX 依赖"
```

---

## Task 12: .env.example 更新

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Add new environment variables**

在 `.env.example` 末尾追加：

```bash
# ===========================================
# 部署模式
# ===========================================
DEPLOYMENT_MODE=development  # production | testing | development
MODEL_DIR=./models

# ===========================================
# JWT 认证 (Phase 4)
# ===========================================
JWT_PRIVATE_KEY_PATH=./keys/private.pem
JWT_PUBLIC_KEY_PATH=./keys/public.pem
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=15
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7
JWT_ALGORITHM=RS256

# ===========================================
# 用户数据库 (Phase 4)
# ===========================================
DATABASE_URL=sqlite:///./data/users.db
# DATABASE_URL=postgresql://user:pass@localhost:5432/rag_users

# ===========================================
# 安全
# ===========================================
CORS_ORIGINS=http://localhost:3000
HTTPS_ENABLED=false
RATE_LIMIT_ENABLED=true
```

- [ ] **Step 2: Commit**

```bash
git add .env.example
git commit -m "chore: 更新 .env.example — 部署模式/JWT/数据库配置"
```

---

## Execution Order Summary

| Task | Phase | 依赖 | 估计耗时 |
|------|-------|------|----------|
| Task 1: Mock 数据生成器 | P1 | 无 | 30 min |
| Task 2: deployment_mode 配置 | P1 | 无 | 20 min |
| Task 3: 单卡 LLM Client | P1 | Task 2 | 20 min |
| Task 4: CPU Rerank Fallback | P1 | 无 | 20 min |
| Task 5: 清理遗留代码 | P1 | 无 | 5 min |
| Task 6: Docker Compose 分层 | P3 | Task 2 | 40 min |
| Task 7: 一键启动脚本 | P3 | Task 6 | 30 min |
| Task 8: 集成测试框架 | P2 | Task 1, 2 | 30 min |
| Task 9: Locust 压测脚本 | P5 | Task 8 | 20 min |
| Task 10: Grafana 监控 | P6 | 无 | 20 min |
| Task 11: 依赖更新 | P1 | 无 | 5 min |
| Task 12: .env.example | P1 | 无 | 5 min |

**注意:** Phase 4 (JWT/前端) 和 Phase 6 的故障演练/文档是独立工作流，建议在 Phase 1-3 完成后单独制定子计划。
