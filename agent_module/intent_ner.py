"""
意图识别与实体提取模块（agent_module.py 扩展）

为旧版 Agent 类提供基于规则的意图分类和实体提取能力。

意图分类（6类）：
- 0: 汽车/产品相关问题 → 使用 RAG 检索
- 1: 法规/合规问题 → 使用 ES 精确匹配
- 2: 价格/规格查询 → 简单回答
- 3: 图片/视觉分析 → 启用多模态
- 4: 对比类问题 → 多文档综合
- 5: 闲聊/问候 → 直接回答

实体提取：基于正则匹配汽车领域实体
"""

from __future__ import annotations

import re
from typing import Optional


class IntentClassifier:
    """
    基于规则的意图分类器

    当预训练的意图识别模型不可用时，使用关键词规则进行分类。
    覆盖汽车领域常见查询模式。
    """

    # 各类别关键词映射
    INTENT_PATTERNS = {
        0: [  # 汽车/产品相关
            "汽车", "车辆", "车型", "品牌", "发动机", "变速箱",
            "油耗", "续航", "电池", "充电", "配置", "内饰", "外观",
        ],
        1: [  # 法规/合规
            "法规", "政策", "标准", "规定", "合规", "认证",
            "备案", "许可", "年检", "保险", "排放", "安全标准",
        ],
        2: [  # 价格/规格
            "价格", "多少钱", "报价", "售价", "配置参数", "规格",
            "尺寸", "马力", "功率", "扭矩", "加速",
        ],
        3: [  # 图片/视觉
            "图片", "外观", "颜色", "看看", "长什么样", "照片",
            "图", "车型图",
        ],
        4: [  # 对比分析
            "对比", "比较", "区别", "差异", "哪个好", "推荐",
            "选择", "测评", "评测",
        ],
        5: [  # 闲聊
            "你好", "hi", "hello", "谢谢", "帮忙", "请问",
            "问一下", "问一下",
        ],
    }

    def predict(self, text: str) -> int:
        """
        预测意图类别

        Args:
            text: 用户查询文本

        Returns:
            意图类别 (0-5)
        """
        text_lower = text.lower()

        scores = {}
        for intent_id, keywords in self.INTENT_PATTERNS.items():
            score = sum(1 for kw in keywords if kw in text or kw in text_lower)
            scores[intent_id] = score

        # 返回得分最高的类别
        if max(scores.values()) > 0:
            return max(scores, key=scores.get)

        return 0  # 默认：汽车相关

    def get_intent_name(self, intent_id: int) -> str:
        names = {
            0: "product", 1: "regulation", 2: "price_spec",
            3: "visual", 4: "comparison", 5: "chat",
        }
        return names.get(intent_id, "unknown")


class EntityExtractor:
    """
    基于正则的命名实体提取器

    提取汽车领域常见实体：
    - 品牌名（法拉利、兰博基尼、保时捷、宝马、奔驰等）
    - 车型名（Model S、911、Cayenne 等）
    - 零部件（发动机、轮胎、电池等）
    - 数值+单位（100km/h、500km、2.0T）
    """

    # 品牌名正则
    BRAND_PATTERNS = [
        r"法拉利", r"兰博基尼", r"保时捷", r"奔驰", r"宝马", r"奥迪",
        r"特斯拉", r"比亚迪", r"蔚来", r"小鹏", r"理想", r"小米",
        r"丰田", r"本田", r"大众", r"福特", r"通用", r"路虎",
        r"玛莎拉蒂", r"宾利", r"劳斯莱斯", r"迈凯伦", r"阿斯顿·?马丁",
        r"兰博基尼", r"雷克萨斯", r"英菲尼迪", r"凯迪拉克", r"捷豹",
        r"Ferrari", r"Lamborghini", r"Porsche", r"BMW", r"Benz",
        r"Tesla", r"Mercedes", r"Audi", r"Lexus",
    ]

    # 零部件正则
    COMPONENT_PATTERNS = [
        r"发动机", r"变速箱", r"轮胎", r"轮毂", r"电池", r"电机",
        r"底盘", r"悬挂", r"制动", r"方向盘", r"座椅",
        r"大灯", r"尾灯", r"天窗", r"空调", r"显示屏",
    ]

    # 数值+单位正则
    VALUE_UNIT_PATTERNS = [
        (r"(\d+(?:\.\d+)?)\s*(?:km|h|kW|Nm|马力|功率|扭矩|升|排量)", "numeric"),
        (r"(\d+)\s*(?:km|kwh|kWh|公里|小时)", "numeric"),
    ]

    def extract(self, text: str) -> dict:
        """
        提取所有实体

        Args:
            text: 用户查询文本

        Returns:
            {"brands": [...], "components": [...], "values": [...], "all": [...]}
        """
        brands = set()
        components = set()
        values = []

        # 提取品牌
        for pattern in self.BRAND_PATTERNS:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                brands.add(match.group())

        # 提取零部件
        for pattern in self.COMPONENT_PATTERNS:
            for match in re.finditer(pattern, text):
                components.add(match.group())

        # 提取数值
        for pattern, ptype in self.VALUE_UNIT_PATTERNS:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                values.append({"value": match.group(), "type": ptype})

        return {
            "brands": list(brands),
            "components": list(components),
            "values": values,
            "all": list(brands) + list(components),
        }

    def extract_as_text(self, text: str) -> str:
        """
        提取实体并拼接为搜索查询文本

        Args:
            text: 用户查询文本

        Returns:
            拼接的实体文本（如 "法拉利 发动机 500km"）
        """
        entities = self.extract(text)
        parts = entities["brands"] + entities["components"]
        return " ".join(parts) if parts else text
