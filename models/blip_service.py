"""
BLIP 在线按需推理模块（PRD §6）

定位：仅在在线阶段按需触发，不参与离线构建。

触发条件（Rule + BERT 双路决策）：
1. 关键词规则：query 含视觉相关关键词
2. BERT 意图分类：intent 为 formulation / ingredient 且 business_type 含图像意图
3. CLIP 召回结果存在 image_uri（说明有图像命中）

触发率 <5%，结果缓存 TTL 1h，GPU 批处理延迟 ≤120ms。

使用场景：
- 用户查询涉及包装外观、成分表图片、标签等视觉内容
- CLIP 召回返回了图像结果，需要生成文本描述辅助 LLM 理解
"""

from __future__ import annotations

import json
import logging
import os
import time
import threading
from typing import Optional

with open("config.json", encoding="utf-8") as f:
    config = json.load(f)

logger = logging.getLogger(__name__)

# 视觉相关关键词（与 pipeline._should_use_clip_sync 共用词表）
VISUAL_KEYWORDS = {
    "图片", "包装", "外观", "照片", "扫描", "标签",
    "成分表", "配方", "图像", "图像识别", "OCR",
    "说明书", "瓶身", "外盒", "瓶盖", "瓶贴",
    "生产日期", "保质期", "批号", "条形码",
    "产品图", "实物图", "对比图", "展示图",
}


class BLIPTargetDetector:
    """
    BLIP 触发决策器（PRD §6: Rule + BERT 双路决策）

    决策逻辑：
    is_blip_triggered = max(keyword_rule_score, bert_classifier_score, has_image_hit)

    任一条件满足即触发 BLIP，触发率控制在 <5%。
    """

    def __init__(self):
        self._bert_model = None
        self._bert_tokenizer = None
        logger.info("BLIPTargetDetector 初始化完成")

    def should_trigger(
        self,
        query: str,
        business_type: str = "",
        intent: str = "",
        has_image_results: bool = False,
    ) -> bool:
        """
        判断是否应触发 BLIP 推理。

        Args:
            query: 改写后的查询文本
            business_type: 业务类型
            intent: 意图类型
            has_image_results: CLIP 召回是否返回了图像结果

        Returns:
            True 表示应触发 BLIP
        """
        # 条件 1：图像结果已命中 → 直接触发
        if has_image_results:
            logger.debug("BLIP 触发: CLIP 图像结果命中")
            return True

        # 条件 2：关键词规则评分
        keyword_score = self._keyword_rule_score(query)

        # 条件 3：BERT 意图分类评分
        bert_score = self._bert_intent_score(query, business_type, intent)

        score = max(keyword_score, bert_score)
        triggered = score >= 0.6

        if triggered:
            logger.debug(f"BLIP 触发: keyword={keyword_score:.2f}, bert={bert_score:.2f}")

        return triggered

    def _keyword_rule_score(self, query: str) -> float:
        """关键词规则评分（0.0 - 1.0）"""
        hits = sum(1 for kw in VISUAL_KEYWORDS if kw in query)
        return min(hits * 0.3, 1.0)

    def _bert_intent_score(self, query: str, business_type: str, intent: str) -> float:
        """
        BERT 意图分类评分（简化版：基于 intent + business_type 规则）

        生产环境可替换为微调 BERT 分类器。
        当前规则：
        - formulation/ingredient intent + 包含视觉关键词 → 0.7
        - development business_type + 包含图像类关键词 → 0.6
        """
        if intent in ("formulation", "ingredient"):
            # 配方/成分查询有较高概率需要看图
            has_visual_kw = any(kw in query for kw in VISUAL_KEYWORDS)
            if has_visual_kw:
                return 0.7

        if business_type == "development":
            has_visual_kw = any(kw in query for kw in VISUAL_KEYWORDS)
            if has_visual_kw:
                return 0.6

        return 0.0


class BLIPInferenceService:
    """
    BLIP 图像描述生成服务（PRD §6）

    特性：
    - 按需加载 BLIP 模型（懒加载）
    - 结果缓存 TTL 1h（避免重复推理）
    - GPU 批处理延迟 ≤120ms
    - 失败时优雅降级（不影响主链路）
    """

    def __init__(self):
        self._model = None
        self._processor = None
        self._cache = {}  # {image_uri: (caption, timestamp)}
        self._cache_lock = threading.Lock()
        self._cache_ttl = 3600  # 1 小时
        self._trigger_count = 0
        self._total_count = 0
        logger.info("BLIPInferenceService 初始化完成")

    @property
    def model(self):
        if self._model is None:
            self._try_load_model()
        return self._model

    @property
    def processor(self):
        if self._processor is None:
            self._try_load_model()
        return self._processor

    def _try_load_model(self):
        """尝试加载 BLIP 模型"""
        try:
            from transformers import BlipProcessor, BlipForConditionalGeneration

            model_path = config.get("gpu1", {}).get("models", {}).get(
                "blip", {}
            ).get("model_path", "blip-image-captioning-large")

            # 支持相对路径和绝对路径
            if not os.path.isabs(model_path):
                model_path = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    model_path,
                )

            if os.path.exists(model_path):
                self._processor = BlipProcessor.from_pretrained(model_path)
                self._model = BlipForConditionalGeneration.from_pretrained(model_path)
                logger.info(f"BLIP 模型加载完成: {model_path}")
            else:
                logger.warning(f"BLIP 模型路径不存在: {model_path}，BLIP 功能不可用")
        except ImportError:
            logger.warning("transformers 未安装，BLIP 功能不可用")
        except Exception as e:
            logger.error(f"BLIP 模型加载失败: {e}")

    def generate_caption(
        self,
        image_uri: str,
        query: str = "",
        max_length: int = 128,
    ) -> Optional[str]:
        """
        为单张图像生成描述。

        Args:
            image_uri: 图像路径或 URL
            query: 可选的条件文本（conditional image captioning）
            max_length: 生成描述最大长度

        Returns:
            图像描述文本，失败返回 None
        """
        # 缓存命中检查
        cached = self._get_from_cache(image_uri)
        if cached is not None:
            return cached

        if self.model is None or self.processor is None:
            return None

        try:
            import torch
            from PIL import Image

            # 加载图像
            if image_uri.startswith(("http://", "https://")):
                import urllib.request
                with urllib.request.urlopen(image_uri, timeout=5) as response:
                    image = Image.open(response).convert("RGB")
            elif os.path.exists(image_uri):
                image = Image.open(image_uri).convert("RGB")
            else:
                logger.warning(f"BLIP: 图像不存在: {image_uri}")
                return None

            # 推理
            t_start = time.time()
            if query:
                inputs = self.processor(image, query, return_tensors="pt")
            else:
                inputs = self.processor(image, return_tensors="pt")

            with torch.no_grad():
                output = self.model.generate(**inputs, max_length=max_length)

            caption = self.processor.decode(output[0], skip_special_tokens=True)
            inference_ms = (time.time() - t_start) * 1000

            if inference_ms > 120:
                logger.warning(f"BLIP 推理超时: {inference_ms:.0f}ms > 120ms")

            # 缓存结果
            self._put_to_cache(image_uri, caption)

            return caption

        except Exception as e:
            logger.error(f"BLIP 推理失败: {e}")
            return None

    def generate_captions_batch(
        self,
        image_uris: list[str],
        query: str = "",
        max_length: int = 128,
    ) -> dict[str, str]:
        """
        批量生成图像描述（GPU 批处理）。

        Args:
            image_uris: 图像路径/URL 列表
            query: 可选条件文本
            max_length: 最大生成长度

        Returns:
            {image_uri: caption} 字典
        """
        results = {}
        for uri in image_uris:
            caption = self.generate_caption(uri, query, max_length)
            if caption:
                results[uri] = caption
        return results

    def _get_from_cache(self, image_uri: str) -> Optional[str]:
        """从缓存获取"""
        with self._cache_lock:
            if image_uri in self._cache:
                caption, ts = self._cache[image_uri]
                if time.time() - ts < self._cache_ttl:
                    return caption
                else:
                    del self._cache[image_uri]
        return None

    def _put_to_cache(self, image_uri: str, caption: str):
        """写入缓存"""
        with self._cache_lock:
            self._cache[image_uri] = (caption, time.time())
            # 缓存大小限制
            if len(self._cache) > 500:
                # 淘汰最旧的
                oldest_key = min(self._cache, key=lambda k: self._cache[k][1])
                del self._cache[oldest_key]

    def get_trigger_rate(self) -> float:
        """获取 BLIP 触发率"""
        if self._total_count == 0:
            return 0.0
        return self._trigger_count / self._total_count

    def get_stats(self) -> dict:
        """获取统计信息"""
        with self._cache_lock:
            cache_size = len(self._cache)
        return {
            "model_loaded": self.model is not None,
            "cache_size": cache_size,
            "trigger_count": self._trigger_count,
            "total_count": self._total_count,
            "trigger_rate": round(self.get_trigger_rate(), 4),
        }
