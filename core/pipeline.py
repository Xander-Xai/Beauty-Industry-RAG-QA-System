"""
在线 RAG 管线编排器（核心中枢）

对应 readme 4.1 节核心链路：
Query → 用户身份解析 → 二级缓存（L1/L2）
  MISS: 复杂度评估 → Query Rewrite → 权限前置绑定 → 双 Embedding 路由
  → 并行多路召回 → Union 合并去冗 → BiEncoder 宽保留 → Rerank Batch Aggregator
  → CrossEncoder Ensemble → Evidence Ensemble Gate → LLM 生成 → Answer Gate
"""

from __future__ import annotations

import json
import time
import logging
import hashlib
from typing import Optional

from common.audit import log_audit_event

with open("config.json", encoding="utf-8") as f:
    config = json.load(f)

logger = logging.getLogger(__name__)


class OnlineRAGPipeline:
    """
    在线 RAG 推理管线

    编排顺序（对应 readme 4.1）：
    1. 身份解析与权限绑定
    2. 缓存查询（L1/L2）
    3. KV 准入控制
    4. 复杂度评估（BERT 二分类）
    5. Query Rewrite（路由增强器）
    6. 双 Embedding 路由 + CLIP 判别
    7. 并行多路召回（Dense/BM25/CLIP/Rewrite变体）
    8. Union 合并去冗
    9. BiEncoder 宽保留（Top150）
    10. CrossEncoder Ensemble + GPU 批处理
    11. Evidence Ensemble Gate（投票机制）
    12. LLM 生成（按复杂度路由 4B/14B）
    13. Answer Gate（NLI 校验）
    """

    def __init__(self):
        """懒加载各子模块，避免启动时全部加载模型"""
        self._router = None
        self._cache = None
        self._admission = None
        self._complexity_evaluator = None
        self._query_rewriter = None
        self._embedding_service = None
        self._parallel_recall = None
        self._bi_encoder = None
        self._cross_encoder_ensemble = None
        self._evidence_gate = None
        self._llm_client = None
        self._answer_gate = None
        self._otel_tracer = None
        logger.info("OnlineRAGPipeline 初始化完成（子模块懒加载）")

    # ─── 懒加载属性 ────────────────────────────────────────

    @property
    def router(self):
        if self._router is None:
            from router.stateless_router import StatelessRouter
            self._router = StatelessRouter()
        return self._router

    @property
    def cache(self):
        if self._cache is None:
            from cache.redis_cache import RedisCache
            self._cache = RedisCache()
        return self._cache

    @property
    def admission(self):
        if self._admission is None:
            from admission.kv_admission import KVAdmissionControl
            self._admission = KVAdmissionControl()
        return self._admission

    @property
    def complexity_evaluator(self):
        if self._complexity_evaluator is None:
            from models.complexity_evaluator import ComplexityEvaluator
            self._complexity_evaluator = ComplexityEvaluator()
        return self._complexity_evaluator

    @property
    def query_rewriter(self):
        if self._query_rewriter is None:
            from rewrite.query_rewriter import QueryRewriter
            self._query_rewriter = QueryRewriter()
        return self._query_rewriter

    @property
    def embedding_service(self):
        if self._embedding_service is None:
            from models.embedding_service import EmbeddingService
            self._embedding_service = EmbeddingService()
        return self._embedding_service

    @property
    def parallel_recall(self):
        if self._parallel_recall is None:
            from retrieval.parallel_recall import ParallelRecallManager
            self._parallel_recall = ParallelRecallManager()
        return self._parallel_recall

    @property
    def bi_encoder(self):
        if self._bi_encoder is None:
            from retrieval.bi_encoder import BiEncoderReranker
            self._bi_encoder = BiEncoderReranker()
        return self._bi_encoder

    @property
    def cross_encoder_ensemble(self):
        if self._cross_encoder_ensemble is None:
            from retrieval.cross_encoder_ensemble import CrossEncoderEnsemble
            self._cross_encoder_ensemble = CrossEncoderEnsemble()
        return self._cross_encoder_ensemble

    @property
    def evidence_gate(self):
        if self._evidence_gate is None:
            from retrieval.evidence_gate import EvidenceEnsembleGate
            self._evidence_gate = EvidenceEnsembleGate()
        return self._evidence_gate

    @property
    def llm_client(self):
        if self._llm_client is None:
            from models.llm_client import LLMClient
            self._llm_client = LLMClient()
        return self._llm_client

    @property
    def answer_gate(self):
        if self._answer_gate is None:
            from retrieval.answer_gate import AnswerGate
            self._answer_gate = AnswerGate()
        return self._answer_gate

    # ─── 主处理入口 ────────────────────────────────────────

    def process(self, ctx) -> str:
        """
        在线推理主流程

        Args:
            ctx: RequestContext 实例

        Returns:
            最终回答文本
        """
        from core.pipeline_context import SessionState

        # 审计日志：记录查询接收事件（query 仅存哈希）
        log_audit_event(
            event_type="query_received",
            request_id=ctx.request_id,
            user_id=ctx.user_id,
            user_role_mask=ctx.user_role_mask,
            query=ctx.user_input,
        )

        t_total = time.time()
        try:
            # ① 身份解析（外部传入 ctx.user_role_mask / ctx.user_dept_mask）

            # ② 缓存查询（L1/L2）
            cache_key = self._build_cache_key(ctx)
            cached = self.cache.get(cache_key, ctx.user_role_mask, ctx.user_dept_mask)
            if cached:
                ctx.cache_hit_level = "L1" if (ctx.user_role_mask == 0 and ctx.user_dept_mask == 0) else "L2"
                ctx.final_response = cached.get("answer", "")
                ctx.record_timing("cache_hit", 0)
                return ctx.final_response
            ctx.cache_hit_level = "MISS"

            # ③ KV 准入控制
            t_admit = time.time()
            admitted, reason = self._check_admission(ctx)
            ctx.record_timing("admission_check", (time.time() - t_admit) * 1000)
            if not admitted:
                ctx.final_response = self._handle_rejection(ctx, reason)
                return ctx.final_response

            # ④ 复杂度评估（BERT 二分类 → 简单/复杂）
            t_complexity = time.time()
            is_complex = self.complexity_evaluator.evaluate(ctx.user_input)
            ctx.record_timing("complexity_eval", (time.time() - t_complexity) * 1000)

            # ⑤ Query Rewrite（路由增强器，失败降级不返回 503）
            t_rewrite = time.time()
            try:
                session_state = SessionState.get_or_create(ctx.session_id or "default")
                ctx.rewrite_result = self.query_rewriter.rewrite(
                    ctx.user_input,
                    recent_dialogs=session_state.get_recent_queries(),
                )
            except Exception as e:
                logger.warning(f"Query Rewrite 失败，降级为规则兜底: {e}")
                ctx.rewrite_result = self._fallback_rewrite(ctx.user_input)
                ctx.degraded = True
                ctx.fallback_reason = f"rewrite_fallback: {e}"
            ctx.record_timing("rewrite", (time.time() - t_rewrite) * 1000)

            # ⑥ 确定输出长度与模型路由
            business_type = ctx.rewrite_result.business_type
            ctx.max_output_tokens = config["gpu0"]["models"]["gen_14b"]["max_output_tokens"].get(
                business_type, 512
            )
            target_model = "qwen3-14b" if is_complex else "qwen3-4b"

            # ⑦ 双 Embedding 路由 + CLIP 判别（readme 4.5）
            t_embedding = time.time()
            query_embedding = self.embedding_service.encode_text(ctx.rewrite_result.rewritten_query)
            clip_should_sync = self._should_use_clip_sync(ctx.rewrite_result)
            ctx.record_timing("embedding_route", (time.time() - t_embedding) * 1000)

            # ⑧ 并行多路召回（readme 7.1）
            t_recall = time.time()
            ctx.recall_results = self.parallel_recall.execute(
                query=ctx.rewrite_result.rewritten_query,
                query_embedding=query_embedding,
                user_role_mask=ctx.user_role_mask,
                user_dept_mask=ctx.user_dept_mask,
                use_clip=clip_should_sync,
                top_k_per_path=config["retrieval"]["parallel_paths"],
            )
            ctx.record_timing("parallel_recall", (time.time() - t_recall) * 1000)

            # ⑨ Union 合并去冗
            ctx.union_recall_set = self._merge_and_dedup(ctx.recall_results)

            # ⑩ BiEncoder 宽保留（Top150）
            t_bi = time.time()
            ctx.rerank_results = self.bi_encoder.rerank(
                query=ctx.rewrite_result.rewritten_query,
                candidates=ctx.union_recall_set,
                top_k=150,
            )
            ctx.record_timing("bi_encoder", (time.time() - t_bi) * 1000)

            # ⑪ CrossEncoder Ensemble（GPU 批处理）
            t_ce = time.time()
            ctx.rerank_results = self.cross_encoder_ensemble.rerank(
                query=ctx.rewrite_result.rewritten_query,
                candidates=ctx.rerank_results,
                top_k=config["retrieval"]["cross_encoder"]["final_top_k"],
            )
            ctx.record_timing("cross_encoder_ensemble", (time.time() - t_ce) * 1000)

            # ⑫ Evidence Ensemble Gate（投票机制，readme 7.4）
            t_eg = time.time()
            ctx.evidence_result = self.evidence_gate.evaluate(
                query=ctx.rewrite_result.rewritten_query,
                rerank_results=ctx.rerank_results,
                retrieval_agreement_score=ctx.retrieval_agreement_score,
            )
            ctx.record_timing("evidence_gate", (time.time() - t_eg) * 1000)

            # ⑬ 根据 Evidence Gate 决策分支
            decision = ctx.evidence_result.decision
            if decision == "reject":
                ctx.final_response = "无法确认相关信息，请补充更多细节或换个方式提问。"
                return ctx.final_response

            # 证据锁定
            ctx.evidence_locked_doc_ids = [r.doc_id for r in ctx.evidence_result.top_docs[:3]]

            # ⑭ LLM 生成
            t_gen = time.time()
            ctx.generation_result = self.llm_client.generate(
                ctx=ctx,
                target_model=target_model,
                max_tokens=ctx.max_output_tokens,
            )
            ctx.record_timing("generation", (time.time() - t_gen) * 1000)

            # ⑮ Answer Gate（NLI 校验）
            t_ag = time.time()
            ctx.answer_gate_result = self.answer_gate.verify(
                answer=ctx.generation_result.answer,
                top_doc=ctx.rerank_results[0] if ctx.rerank_results else None,
                is_regulation=(business_type == "regulation"),
            )
            ctx.record_timing("answer_gate", (time.time() - t_ag) * 1000)

            if not ctx.answer_gate_result.passed:
                ctx.final_response = "无法确认相关信息，请补充更多细节或换个方式提问。"
                return ctx.final_response

            # ⑯ 输出
            ctx.final_response = ctx.generation_result.answer

            # ⑰ 写入缓存
            self._write_cache(ctx)

            # ⑱ 更新会话
            session = SessionState.get_or_create(ctx.session_id or "default")
            session.add_round(ctx.user_input, ctx.final_response, ctx.rewrite_result)
            session.lock_evidence(ctx.evidence_locked_doc_ids)

            return ctx.final_response

        except Exception as e:
            logger.error(f"管线异常: {e}", exc_info=True)
            ctx.degraded = True
            ctx.fallback_reason = str(e)
            ctx.final_response = "系统处理出现异常，请稍后重试。"
            return ctx.final_response

        finally:
            ctx.record_timing("total", (time.time() - t_total) * 1000)
            # KV 准入控制释放
            self.admission.release(ctx.request_id)
            logger.info(
                f"[{ctx.request_id}] 完成 | "
                f"总耗时={ctx.get_total_latency_ms():.0f}ms | "
                f"缓存={ctx.cache_hit_level} | "
                f"降级={ctx.degraded} | "
                f"各阶段={ctx.stage_timings}"
            )

    # ─── 内部辅助方法 ────────────────────────────────────────

    def _build_cache_key(self, ctx) -> str:
        """
        缓存 Key 统一构造（readme 10.2）

        cache_key = hash(normalized_query + embedding_version + knowledge_version_epoch
                         + prompt_version + schema_version + role_mask + dept_mask)
        """
        rewrite_q = ctx.rewrite_result.rewritten_query if ctx.rewrite_result else ctx.user_input
        key_data = {
            "q": rewrite_q,
            "ev": config["embedding"]["text"]["model_path"],  # embedding_version
            "ke": config.get("knowledge_version_epoch", "default"),
            "pv": config["generation"]["prompt_version"],
            "sv": config.get("schema_version", "1.0"),
            "rm": ctx.user_role_mask,
            "dm": ctx.user_dept_mask,
        }
        return hashlib.sha256(json.dumps(key_data, sort_keys=True).encode()).hexdigest()

    def _check_admission(self, ctx) -> tuple[bool, str]:
        """KV 准入控制检查"""
        input_tokens = len(ctx.user_input)  # 粗略估计，实际用 tokenizer
        admitted, reason = self.admission.admit(
            ctx.request_id, input_tokens, ctx.max_output_tokens,
            ctx.rewrite_result.business_type if ctx.rewrite_result else "general"
        )
        ctx.kv_pressure_at_entry = self.admission.get_pressure()
        return admitted, reason

    def _handle_rejection(self, ctx, reason: str) -> str:
        """处理准入拒绝 - 按优先级降级"""
        if reason == "critical":
            return "系统负载过高，请稍后重试。"
        elif reason == "soft_stop":
            return "系统繁忙，正在排队处理您的请求，请稍候。"
        else:
            return "系统当前无法处理此请求，请稍后重试。"

    def _fallback_rewrite(self, user_input: str):
        """Query Rewrite 失败降级（readme 4.4）- 增强规则分类"""
        from core.pipeline_context import QueryRewriteResult

        # 增强关键词规则分类
        business_type = "general"
        intent = "general"

        regulation_keywords = ["法规", "合规", "标准", "备案", "许可", "标准号", "GB", "禁用"]
        development_keywords = ["配方", "研发", "工艺", "制备", "合成"]
        ingredient_keywords = ["成分", "INCI", "功效", "浓度", "含量", "添加量"]

        regulation_score = sum(1 for kw in regulation_keywords if kw in user_input)
        development_score = sum(1 for kw in development_keywords if kw in user_input)
        ingredient_score = sum(1 for kw in ingredient_keywords if kw in user_input)

        if regulation_score >= 1:
            business_type = "regulation"
            intent = "compliance"
        elif development_score >= 1:
            business_type = "development"
            intent = "formulation"
        elif ingredient_score >= 1:
            business_type = "development"
            intent = "ingredient"

        return QueryRewriteResult(
            rewritten_query=user_input,
            business_type=business_type,
            intent=intent,
            requires_context=True,
            confidence=0.3,
            fallback=True,
        )

    def _should_use_clip_sync(self, rewrite_result) -> bool:
        """
        CLIP 判别式同步路由（readme 4.5）

        is_visual_relevant = max(keyword_rule_score, query_text_length_signal)
        ≥ 0.6 → 同步参与 | 0.3-0.6 → 低成本同步(TopK=20) | < 0.3 → 跳过
        """
        # 关键词规则评分
        visual_keywords = {
            "图片", "包装", "外观", "照片", "扫描", "标签",
            "成分表", "配方", "图像", "图像识别", "OCR",
            "说明书", "瓶身", "外盒",
        }
        query_text = rewrite_result.rewritten_query if rewrite_result else ""
        keyword_hits = sum(1 for kw in visual_keywords if kw in query_text)
        keyword_score = min(keyword_hits * 0.3, 1.0)

        # 查询长度信号（视觉类查询往往较短且包含实物描述）
        intent = rewrite_result.intent if rewrite_result else "general"
        intent_score = 0.3 if intent in ("formulation", "ingredient") else 0.0

        # 综合得分
        score = max(keyword_score, intent_score)
        logger.debug(f"CLIP 路由评分: keyword={keyword_score:.1f}, intent={intent_score:.1f}, total={score:.1f}")
        return score >= 0.3

    def _merge_and_dedup(self, recall_results: list) -> list:
        """Union 合并去冗（readme 7.1）"""
        seen_doc_ids = set()
        merged = []
        for r in recall_results:
            if r.doc_id not in seen_doc_ids:
                seen_doc_ids.add(r.doc_id)
                merged.append(r)
        return merged

    def _write_cache(self, ctx):
        """写入 L1/L2 缓存"""
        try:
            cache_key = self._build_cache_key(ctx)
            cache_value = {
                "answer": ctx.final_response,
                "doc_ids": ctx.evidence_locked_doc_ids,
                "business_type": ctx.rewrite_result.business_type if ctx.rewrite_result else "general",
            }
            self.cache.set(cache_key, cache_value, ctx.user_role_mask, ctx.user_dept_mask)
        except Exception as e:
            logger.warning(f"缓存写入失败: {e}")
