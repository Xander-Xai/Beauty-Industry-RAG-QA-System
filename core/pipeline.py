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
        self._ab_platform = None  # A/B 实验平台
        self._blip_service = None  # BLIP 在线按需推理
        self._blip_detector = None  # BLIP 触发决策器
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

    @property
    def ab_platform(self):
        if self._ab_platform is None:
            from common.ab_testing import ABExperimentPlatform
            self._ab_platform = ABExperimentPlatform()
        return self._ab_platform

    @property
    def blip_service(self):
        if self._blip_service is None:
            from models.blip_service import BLIPInferenceService
            self._blip_service = BLIPInferenceService()
        return self._blip_service

    @property
    def blip_detector(self):
        if self._blip_detector is None:
            from models.blip_service import BLIPTargetDetector
            self._blip_detector = BLIPTargetDetector()
        return self._blip_detector

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

            # ①-b A/B 实验分流（PRD §12.2）
            ab_overrides = {}
            try:
                running_exps = self.ab_platform.get_running_experiments()
                for exp in running_exps:
                    variant = self.ab_platform.assign_variant(ctx.user_id, exp.experiment_id)
                    if variant:
                        overrides = self.ab_platform.get_variant_config(ctx.user_id, exp.experiment_id)
                        if overrides:
                            ab_overrides.update(overrides)
                            ctx.ab_experiment = exp.experiment_id
                            ctx.ab_variant = variant.name
                            logger.debug(f"A/B 分流: exp={exp.experiment_id}, variant={variant.name}")
            except Exception as e:
                logger.debug(f"A/B 实验分流跳过: {e}")

            # ② 缓存查询（L1/L2）— PRD §10.6: requires_context=true 仅限原会话
            # PRD §4.4: Rewrite 降级时禁用缓存
            requires_ctx = (
                ctx.rewrite_result.requires_context
                if ctx.rewrite_result
                else False
            )
            cache_key = self._build_cache_key(ctx)
            if requires_ctx:
                # PRD §10.6: 上下文依赖型查询的缓存仅限同一 session 复用
                cache_key = self._scope_cache_key_to_session(cache_key, ctx.session_id)
                cached = self.cache.get(cache_key, ctx.user_role_mask, ctx.user_dept_mask)
                if cached:
                    ctx.cache_hit_level = "L2_SESSION"
                    ctx.final_response = cached.get("answer", "")
                    ctx.record_timing("cache_hit", 0)
                    return ctx.final_response
            else:
                cached = self.cache.get(cache_key, ctx.user_role_mask, ctx.user_dept_mask)
                if cached:
                    ctx.cache_hit_level = "L1" if (ctx.user_role_mask == 0 and ctx.user_dept_mask == 0) else "L2"
                    ctx.final_response = cached.get("answer", "")
                    ctx.record_timing("cache_hit", 0)
                    return ctx.final_response
            ctx.cache_hit_level = "MISS"

            # ③ Query Rewrite（路由增强器，失败降级不返回 503）— 先于准入控制以获取 business_type
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

            # ④ 复杂度评估（提前至 model routing 之前，防止 NameError）
            t_complexity = time.time()
            try:
                is_complex = self.complexity_evaluator.evaluate(ctx.user_input)
            except Exception as e:
                logger.warning(f"复杂度评估失败，降级为简单模型: {e}")
                is_complex = False
                ctx.degraded = True
                ctx.fallback_reason = f"complexity_eval_fallback: {e}"
            ctx.record_timing("complexity_eval", (time.time() - t_complexity) * 1000)

            # ⑤ 确定输出长度与模型路由
            business_type = ctx.rewrite_result.business_type
            ctx.max_output_tokens = config["gpu0"]["models"]["gen_14b"]["max_output_tokens"].get(
                business_type, 512
            )
            target_model = "qwen3-14b" if is_complex else "qwen3-4b"

            # PRD §4.4 保守执行策略：Rewrite 降级时强制提升检索量、禁用缓存、收缩输出
            is_fallback = ctx.rewrite_result.fallback
            if is_fallback:
                ctx.max_output_tokens = max(ctx.max_output_tokens // 2, 256)  # 输出长度收缩
                ctx.cache_hit_level = "DISABLED"  # 禁用缓存命中
                logger.info("Rewrite 降级模式：TopK→300, 缓存禁用, 输出收缩")

            # ⑥ KV 准入控制（在 rewrite 之后以获取正确的 business_type 用于 P0/P1/P2 优先级）
            t_admit = time.time()
            admitted, admission_reason, admission_priority = self._check_admission(ctx)
            ctx.record_timing("admission_check", (time.time() - t_admit) * 1000)
            if not admitted:
                ctx.final_response = self._handle_rejection(ctx, admission_reason)
                return ctx.final_response

            # ⑦ 双 Embedding 路由 + CLIP 三档判别（PRD §4.5）
            t_embedding = time.time()
            query_embedding = self.embedding_service.encode_text(ctx.rewrite_result.rewritten_query)
            clip_use, clip_top_k = self._should_use_clip_sync(ctx.rewrite_result)
            ctx.record_timing("embedding_route", (time.time() - t_embedding) * 1000)

            # ⑧ 并行多路召回（PRD §7.1）
            t_recall = time.time()
            recall_top_k = dict(config["retrieval"]["parallel_paths"])
            # PRD §4.4 保守执行：降级时 TopK 100→300
            if is_fallback:
                for path_key in recall_top_k:
                    recall_top_k[path_key] = {**recall_top_k[path_key], "top_k": 300}
            if clip_use and "clip_visual" in recall_top_k:
                recall_top_k["clip_visual"] = {**recall_top_k["clip_visual"], "top_k": clip_top_k}
            ctx.recall_results, ctx.retrieval_agreement_score = self.parallel_recall.execute(
                query=ctx.rewrite_result.rewritten_query,
                query_embedding=query_embedding,
                user_role_mask=ctx.user_role_mask,
                user_dept_mask=ctx.user_dept_mask,
                use_clip=clip_use,
                clip_top_k=clip_top_k,
                top_k_per_path=recall_top_k,
            )
            ctx.record_timing("parallel_recall", (time.time() - t_recall) * 1000)

            # ⑧-b BLIP 在线按需触发（PRD §6）
            # 当 CLIP 召回返回图像结果且查询涉及视觉内容时，生成图像描述增强检索结果
            t_blip = time.time()
            try:
                self._maybe_trigger_blip(ctx)
            except Exception as e:
                logger.debug(f"BLIP 触发跳过: {e}")
            ctx.record_timing("blip_inference", (time.time() - t_blip) * 1000)

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
            # 应用 A/B 实验覆盖（权重 / 阈值）
            evidence_weights = ab_overrides.get("evidence_gate_weights")
            evidence_thresholds = ab_overrides.get("evidence_gate_thresholds")
            ctx.evidence_result = self.evidence_gate.evaluate(
                query=ctx.rewrite_result.rewritten_query,
                rerank_results=ctx.rerank_results,
                retrieval_agreement_score=ctx.retrieval_agreement_score,
                weights_override=evidence_weights,
                thresholds_override=evidence_thresholds,
            )
            ctx.record_timing("evidence_gate", (time.time() - t_eg) * 1000)

            # 记录 A/B 实验指标
            if ctx.ab_experiment:
                try:
                    self.ab_platform.record_metric(
                        experiment_id=ctx.ab_experiment,
                        user_id=ctx.user_id,
                        metric_name="evidence_score",
                        value=ctx.evidence_result.evidence_score,
                    )
                    self.ab_platform.record_metric(
                        experiment_id=ctx.ab_experiment,
                        user_id=ctx.user_id,
                        metric_name="latency_ms",
                        value=ctx.get_total_latency_ms(),
                    )
                except Exception:
                    pass

            # ⑬ 根据 Evidence Gate 决策分支
            decision = ctx.evidence_result.decision
            if decision == "reject":
                log_audit_event(
                    event_type="request_rejected",
                    request_id=ctx.request_id,
                    user_id=ctx.user_id,
                    user_role_mask=ctx.user_role_mask,
                    reject_reason=f"evidence_gate_{decision}_score={ctx.evidence_result.evidence_score:.3f}",
                    query=ctx.user_input,
                )
                ctx.final_response = "无法确认相关信息，请补充更多细节或换个方式提问。"
                return ctx.final_response

            # 证据锁定
            ctx.evidence_locked_doc_ids = [r.doc_id for r in ctx.evidence_result.top_docs[:3]]

            # ⑬ LLM 生成（含 KV 降级适配 + Prefix Caching 保护）
            t_gen = time.time()
            # PRD §9 Prefix Caching 保护: max_tokens 不变，由流式截断控制输出长度
            effective_max_tokens = self.admission.get_effective_max_tokens(
                ctx.max_output_tokens, admission_reason
            )
            truncation_tokens = self.admission.get_truncation_tokens(
                ctx.max_output_tokens, admission_reason
            )
            force_downgrade = self.admission.should_force_downgrade(admission_reason)
            effective_model = "qwen3-4b" if force_downgrade else target_model

            ctx.generation_result = self.llm_client.generate(
                ctx=ctx,
                target_model=effective_model,
                max_tokens=effective_max_tokens,
            )

            # PRD §9: 应用层流式截断（不修改 vLLM max_tokens）
            if truncation_tokens and ctx.generation_result.answer:
                answer = ctx.generation_result.answer
                if len(answer) > truncation_tokens:
                    ctx.generation_result.answer = answer[:truncation_tokens] + "…"
                    ctx.generation_result.has_more = True
            ctx.record_timing("generation", (time.time() - t_gen) * 1000)

            # ⑭ Answer Plan 结构化增强（PRD §4.6）
            # 若生成内容被截断，尝试二阶段：先生成大纲，再续写填充
            if ctx.generation_result.has_more:
                ctx.generation_result = self._try_structured_continuation(
                    ctx, effective_model, ctx.max_output_tokens
                )

            # ⑮ Answer Gate（NLI 校验）
            t_ag = time.time()
            ctx.answer_gate_result = self.answer_gate.verify(
                answer=ctx.generation_result.answer,
                top_doc=ctx.rerank_results[0] if ctx.rerank_results else None,
                is_regulation=(business_type == "regulation"),
            )
            ctx.record_timing("answer_gate", (time.time() - t_ag) * 1000)

            if not ctx.answer_gate_result.passed:
                log_audit_event(
                    event_type="request_rejected",
                    request_id=ctx.request_id,
                    user_id=ctx.user_id,
                    user_role_mask=ctx.user_role_mask,
                    reject_reason=f"answer_gate_nli_contradiction={ctx.answer_gate_result.nli_contradiction_score:.3f}",
                    query=ctx.user_input,
                )
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

    @staticmethod
    def _scope_cache_key_to_session(cache_key: str, session_id: str | None) -> str:
        """
        PRD §10.6: requires_context=true 的缓存仅限原会话复用。

        在 cache_key 基础上叠加 session_id，确保不同 session 即使
        查询完全相同也不会命中彼此的缓存条目。
        """
        if not session_id:
            return cache_key
        scoped = {"base": cache_key, "sid": session_id}
        return hashlib.sha256(json.dumps(scoped, sort_keys=True).encode()).hexdigest()

    def _check_admission(self, ctx) -> tuple[bool, str, str]:
        """KV 准入控制检查 — 返回 (admitted, reason, priority)"""
        input_tokens = len(ctx.user_input)
        business_type = ctx.rewrite_result.business_type if ctx.rewrite_result else "general"
        admitted, reason, priority = self.admission.admit(
            ctx.request_id, input_tokens, ctx.max_output_tokens, business_type
        )
        ctx.kv_pressure_at_entry = self.admission.get_pressure()
        return admitted, reason, priority

    def _handle_rejection(self, ctx, reason: str) -> str:
        """处理准入拒绝 - 按优先级降级（PRD §5.2.5 / §9 + 审计拦截原因记录）"""
        # PRD §9: P2 极端过载返回 503
        if reason in ("critical_p2_rejected",):
            from fastapi.responses import JSONResponse
            ctx.final_response = JSONResponse(
                status_code=503,
                content={
                    "error": "SERVICE_OVERLOADED",
                    "detail": "系统负载过高，请稍后重试。",
                    "retry_after": 30,
                },
            )
            log_audit_event(
                event_type="request_rejected_503",
                request_id=ctx.request_id,
                user_id=ctx.user_id,
                user_role_mask=ctx.user_role_mask,
                reject_reason=reason,
                query=ctx.user_input,
            )
            return ctx.final_response

        if reason == "critical":
            msg = "系统负载过高，已降级模型以保证服务可用。"
        elif reason == "critical_p1_queued":
            msg = "系统繁忙，请求已加入队列，请稍候。"
        elif reason == "soft_stop":
            msg = "系统繁忙，正在排队处理您的请求，请稍候。"
        elif reason == "downgrade_to_4b":
            msg = "系统负载较高，已切换至轻量模型处理。"
        else:
            msg = "系统当前无法处理此请求，请稍后重试。"

        # 审计日志：记录拦截原因（PRD §11）
        log_audit_event(
            event_type="request_rejected",
            request_id=ctx.request_id,
            user_id=ctx.user_id,
            user_role_mask=ctx.user_role_mask,
            reject_reason=f"admission_{reason}",
            query=ctx.user_input,
        )
        return msg

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

    def _should_use_clip_sync(self, rewrite_result) -> tuple[bool, int]:
        """
        CLIP 判别式三档同步路由（PRD §4.5）

        Returns:
            (use_clip, top_k): 是否启用 CLIP + 对应 top_k
            score >= 0.6 → (True, 50)  全量同步
            0.3 <= score < 0.6 → (True, 20)  低成本同步
            score < 0.3 → (False, 0)  跳过
        """
        visual_keywords = {
            "图片", "包装", "外观", "照片", "扫描", "标签",
            "成分表", "配方", "图像", "图像识别", "OCR",
            "说明书", "瓶身", "外盒",
        }
        query_text = rewrite_result.rewritten_query if rewrite_result else ""
        keyword_hits = sum(1 for kw in visual_keywords if kw in query_text)
        keyword_score = min(keyword_hits * 0.3, 1.0)

        intent = rewrite_result.intent if rewrite_result else "general"
        intent_score = 0.3 if intent in ("formulation", "ingredient") else 0.0

        score = max(keyword_score, intent_score)
        logger.debug(f"CLIP 路由评分: keyword={keyword_score:.1f}, intent={intent_score:.1f}, total={score:.1f}")

        if score >= 0.6:
            return True, 50   # 全量同步
        elif score >= 0.3:
            return True, 20   # 低成本同步
        else:
            return False, 0   # 跳过

    def _merge_and_dedup(self, recall_results: list) -> list:
        """
        Union 合并去冗 + 加权 RRF 融合（PRD §7.1 / §4.5）

        RRF 公式: final_score = w_text * text_score + w_clip * clip_score + w_ocr * ocr_score
        权重来源: config.json → retrieval.rrf.weights（初始值由离线日志分析得出，
        线上由 A/B 实验持续校准）
        """
        rrf_cfg = config.get("retrieval", {}).get("rrf", {})
        k = rrf_cfg.get("k", 60)
        weights = rrf_cfg.get("weights", {"w_text": 1.0, "w_clip": 1.0, "w_ocr": 0.8})

        seen_doc_ids: dict[str, dict] = {}  # doc_id → {result, best_score}
        for r in recall_results:
            # 按来源类型分配基础分数（归一化到 [0, 1] 区间用于 RRF）
            embedding_type = getattr(r, "embedding_type", "") or ""
            if "clip" in embedding_type:
                src_score = weights.get("w_clip", 1.0)
            elif "ocr" in embedding_type:
                src_score = weights.get("w_ocr", 0.8)
            else:
                src_score = weights.get("w_text", 1.0)

            # RRF: 1 / (k + rank)，rank 由首次出现顺序隐式决定
            rrf_score = src_score / (k + len(seen_doc_ids))

            if r.doc_id not in seen_doc_ids:
                seen_doc_ids[r.doc_id] = {"result": r, "best_score": rrf_score}
            else:
                # 保留更高分数的版本
                if rrf_score > seen_doc_ids[r.doc_id]["best_score"]:
                    seen_doc_ids[r.doc_id]["result"] = r
                    seen_doc_ids[r.doc_id]["best_score"] = rrf_score

        # 按 RRF 分数降序排列
        merged = [
            v["result"]
            for v in sorted(seen_doc_ids.values(), key=lambda x: x["best_score"], reverse=True)
        ]
        return merged

    def _maybe_trigger_blip(self, ctx):
        """
        BLIP 在线按需触发（PRD §6）

        触发条件（Rule + BERT 双路决策）：
        1. CLIP 召回返回了带 image_uri 的图像结果 → 直接触发
        2. 关键词规则评分 >= 0.6 → 触发
        3. BERT 意图分类评分 >= 0.6 → 触发

        触发后：
        - 对 top 图像生成 BLIP 描述（≤120ms）
        - 将描述追加到对应 RecallResult.content 中
        - 为 LLM 提供图像内容的文字理解
        """
        # 检查是否有 CLIP 图像召回结果
        clip_results = [r for r in ctx.recall_results if r.source == "clip_visual"]
        has_image_results = bool(clip_results)

        # 检查视觉相关关键词
        query_text = ctx.rewrite_result.rewritten_query if ctx.rewrite_result else ctx.user_input
        business_type = ctx.rewrite_result.business_type if ctx.rewrite_result else "general"
        intent = ctx.rewrite_result.intent if ctx.rewrite_result else "general"

        # BLIP 触发决策
        should_trigger = self.blip_detector.should_trigger(
            query=query_text,
            business_type=business_type,
            intent=intent,
            has_image_results=has_image_results,
        )

        # 统计触发率
        self.blip_service._total_count += 1
        if not should_trigger:
            return

        self.blip_service._trigger_count += 1
        ctx.blip_triggered = True
        logger.info(f"BLIP 触发: query={query_text[:50]}..., image_results={len(clip_results)}")

        # 收集需要生成描述的图像 URI
        image_uris = []
        for r in clip_results:
            uri = r.metadata.get("image_uri", "")
            if uri:
                image_uris.append(uri)

        if not image_uris:
            return

        # 限制处理数量（控制延迟）
        max_images = config.get("blip", {}).get("max_images_per_request", 5)
        image_uris = image_uris[:max_images]

        # 生成 BLIP 描述
        captions = self.blip_service.generate_captions_batch(
            image_uris=image_uris,
            query=query_text,
        )

        # 将描述追加到对应的 RecallResult.content
        for r in clip_results:
            uri = r.metadata.get("image_uri", "")
            if uri and uri in captions:
                caption = captions[uri]
                r.content = f"{r.content}\n[BLIP图像描述] {caption}" if r.content else f"[BLIP图像描述] {caption}"
                logger.debug(f"BLIP 描述追加: doc_id={r.doc_id}, caption={caption[:80]}...")

    def _try_structured_continuation(self, ctx, target_model: str, max_tokens: int):
        """
        Answer Plan 结构化增强（PRD §4.6）

        当首段生成被截断时，二阶段续写：
        1. 构建结构化指令，要求输出大纲 + 剩余内容
        2. 通过 generate_continuation 填充
        """
        from core.pipeline_context import GenerationResult

        try:
            session_state = SessionState.get_or_create(ctx.session_id or "default")
            continuation = self.llm_client.generate_continuation(
                ctx=ctx,
                session_state=session_state,
                already_generated=ctx.generation_result.answer,
                target_model=target_model,
                max_tokens=max_tokens,
            )
            # 合并两段内容
            merged_answer = ctx.generation_result.answer + continuation.answer
            return GenerationResult(
                answer=merged_answer,
                model_used=ctx.generation_result.model_used,
                max_tokens=max_tokens,
                has_more=continuation.has_more,
                session_id=ctx.session_id,
            )
        except Exception as e:
            logger.warning(f"结构化续写失败，使用首段结果: {e}")
            return ctx.generation_result

    def _write_cache(self, ctx):
        """写入 L1/L2 缓存 — PRD §10.6 / §4.4"""
        try:
            # PRD §4.4: Rewrite 降级时禁止缓存写入
            if ctx.rewrite_result and ctx.rewrite_result.fallback:
                return
            cache_key = self._build_cache_key(ctx)
            requires_ctx = (
                ctx.rewrite_result.requires_context
                if ctx.rewrite_result
                else False
            )
            # PRD §10.6: 上下文依赖型查询的缓存仅限同一 session
            if requires_ctx:
                cache_key = self._scope_cache_key_to_session(cache_key, ctx.session_id)
            cache_value = {
                "answer": ctx.final_response,
                "doc_ids": ctx.evidence_locked_doc_ids,
                "business_type": ctx.rewrite_result.business_type if ctx.rewrite_result else "general",
            }
            self.cache.set(cache_key, cache_value, ctx.user_role_mask, ctx.user_dept_mask)
        except Exception as e:
            logger.warning(f"缓存写入失败: {e}")
