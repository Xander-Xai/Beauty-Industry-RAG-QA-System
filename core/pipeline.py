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
import contextlib
import concurrent.futures
from typing import Optional

from common.audit import log_audit_event
from common.config import get_config_dict
from core.exceptions import InfrastructureError

config = get_config_dict()

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
        # M-5: 共享线程池，避免每请求创建 ThreadPoolExecutor
        self._rewrite_pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        self._clip_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
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
    def otel_tracer(self):
        if self._otel_tracer is None:
            try:
                from monitoring.otel_tracer import OpenTelemetryTracer
                self._otel_tracer = OpenTelemetryTracer()
            except Exception:
                self._otel_tracer = "unavailable"
        return None if self._otel_tracer == "unavailable" else self._otel_tracer

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

        # PRD §12: OpenTelemetry 全链路追踪
        tracer = self.otel_tracer
        trace_ctx = tracer.trace("pipeline.process", {"request_id": ctx.request_id}) if tracer else None
        if trace_ctx:
            trace_ctx.__enter__()
        try:
            # 审计日志：记录查询接收事件（query 仅存哈希）
            log_audit_event(
                event_type="query_received",
                request_id=ctx.request_id,
                user_id=ctx.user_id,
                user_role_mask=ctx.user_role_mask,
                query=ctx.user_input,
                business_type=ctx.rewrite_result.business_type if ctx.rewrite_result else "",
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

                # ③+④ 并行执行 Query Rewrite + 复杂度评估（两者无数据依赖）
                t_rewrite = time.time()
                session_state = SessionState.get_or_create(ctx.session_id or "default")
                recent_dialogs = session_state.get_recent_queries()

                # M-5: 使用共享线程池，避免每请求创建 ThreadPoolExecutor
                pool = self._rewrite_pool

                # ③ Query Rewrite（路由增强器，失败降级不返回 503）
                def _do_rewrite():
                    try:
                        with (tracer.trace("rewrite") if tracer else contextlib.nullcontext()):
                            return self.query_rewriter.rewrite(ctx.user_input, recent_dialogs=recent_dialogs)
                    except Exception as e:
                        logger.warning(f"Query Rewrite 失败，降级为规则兜底: {e}")
                        return self._fallback_rewrite(ctx.user_input)

                # ④ 复杂度评估（提前至 model routing 之前）
                def _do_complexity():
                    try:
                        with (tracer.trace("complexity_eval") if tracer else contextlib.nullcontext()):
                            return self.complexity_evaluator.evaluate(ctx.user_input), None
                    except Exception as e:
                        logger.warning(f"复杂度评估失败，降级为简单模型: {e}")
                        return False, str(e)

                f_rewrite = pool.submit(_do_rewrite)
                f_complexity = pool.submit(_do_complexity)

                ctx.rewrite_result = f_rewrite.result()
                is_complex, complexity_err = f_complexity.result()
                if complexity_err:
                    ctx.degraded = True
                    ctx.fallback_reason = f"complexity_eval_fallback: {complexity_err}"

                elapsed_rewrite = (time.time() - t_rewrite) * 1000
                ctx.record_timing("rewrite", elapsed_rewrite)
                ctx.record_timing("complexity_eval", 0)  # 已并入 rewrite 计时

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
                with (tracer.trace("admission") if tracer else contextlib.nullcontext()):
                    admitted, admission_reason, admission_priority = self._check_admission(ctx)
                ctx.record_timing("admission_check", (time.time() - t_admit) * 1000)
                if not admitted:
                    ctx.final_response = self._handle_rejection(ctx, admission_reason)
                    return ctx.final_response

                # ⑦ 双 Embedding 路由 + CLIP 三档判别（PRD §4.5）
                t_embedding = time.time()
                with (tracer.trace("embedding_route") if tracer else contextlib.nullcontext()):
                    query_embedding = self.embedding_service.encode_text(ctx.rewrite_result.rewritten_query)
                    clip_use, clip_top_k = self._should_use_clip_sync(ctx.rewrite_result)
                    ctx.clip_use = clip_use
                    ctx.clip_top_k = clip_top_k
                ctx.record_timing("embedding_route", (time.time() - t_embedding) * 1000)

                # ⑧ 并行多路召回（PRD §7.1）
                # PRD §4.3: 简单 query（60-70%）仅走 BGE+BM25 两路；复杂 query 走完整四路
                t_recall = time.time()
                with (tracer.trace("parallel_recall") if tracer else contextlib.nullcontext()):
                    recall_top_k = dict(config["retrieval"]["parallel_paths"])
                    if not is_complex:
                        recall_top_k = {
                            k: v for k, v in recall_top_k.items()
                            if k in ("dense_bge", "bm25_es")
                        }
                        logger.info("简单 query 路由: 仅 BGE+BM25 两路召回")
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

                # PRD §12: Track CLIP sync timeout
                ctx.clip_sync_timeout = (clip_use and not any(r.source == "clip_visual" for r in ctx.recall_results))

                # GAP-19: CLIP 异步补充召回（PRD §4.5）— 主链路返回后后台执行
                # 结果存入 session.async_clip_results，供下一轮多轮对话预热复用
                if clip_use:
                    try:
                        session_state_for_clip = SessionState.get_or_create(ctx.session_id or "default")
                        clip_filter_expr = self._build_clip_filter(ctx)
                        # M-5: 使用共享线程池
                        clip_pool = self._clip_pool
                        clip_future = clip_pool.submit(
                            self.parallel_recall.recall_async_clip,
                            query=ctx.rewrite_result.rewritten_query,
                            filter_expr=clip_filter_expr,
                            session=session_state_for_clip,
                        )
                        # 不阻塞主流程：设置超时后放弃
                        try:
                            async_clip_results = clip_future.result(timeout=0.5)
                            if async_clip_results:
                                ctx.clip_async_hit = True
                                logger.info(f"CLIP 异步补召回完成: {len(async_clip_results)} 条")
                        except (concurrent.futures.TimeoutError, Exception):
                            ctx.clip_async_hit = False
                            logger.debug("CLIP 异步补召回超时或失败（不影响主流程）")
                    except Exception as e:
                        logger.debug(f"CLIP 异步补召回启动失败: {e}")

                # ⑧-b BLIP 在线按需触发（PRD §6）
                # 当 CLIP 召回返回图像结果且查询涉及视觉内容时，生成图像描述增强检索结果
                t_blip = time.time()
                with (tracer.trace("blip_inference") if tracer else contextlib.nullcontext()):
                    try:
                        self._maybe_trigger_blip(ctx)
                    except Exception as e:
                        logger.debug(f"BLIP 触发跳过: {e}")
                ctx.record_timing("blip_inference", (time.time() - t_blip) * 1000)

                # ⑨ Union 合并去冗（PRD §4.5: 动态权重按 business_type + is_visual_relevant）
                ctx.union_recall_set = self._merge_and_dedup(
                    ctx.recall_results,
                    business_type=business_type,
                    is_visual_relevant=clip_use and clip_top_k > 20,
                )

                # ⑩ BiEncoder 宽保留（Top150）
                t_bi = time.time()
                with (tracer.trace("bi_encoder") if tracer else contextlib.nullcontext()):
                    ctx.rerank_results = self.bi_encoder.rerank(
                        query=ctx.rewrite_result.rewritten_query,
                        candidates=ctx.union_recall_set,
                        top_k=150,
                    )
                ctx.record_timing("bi_encoder", (time.time() - t_bi) * 1000)

                # ⑪ CrossEncoder Ensemble（GPU 批处理）
                t_ce = time.time()
                with (tracer.trace("cross_encoder_ensemble") if tracer else contextlib.nullcontext()):
                    ctx.rerank_results = self.cross_encoder_ensemble.rerank(
                        query=ctx.rewrite_result.rewritten_query,
                        candidates=ctx.rerank_results,
                        top_k=config["retrieval"]["cross_encoder"]["final_top_k"],
                    )
                ctx.record_timing("cross_encoder_ensemble", (time.time() - t_ce) * 1000)

                # GAP-20: 将 CLIP 贡献度从 Rerank 结果传递到 ctx，供 MetricsCollector 采集
                if ctx.rerank_results:
                    ctx.clip_contribution_ratio = getattr(
                        ctx.rerank_results[0], '_clip_contribution_ratio', 0.0
                    )

                # ⑫ Evidence Ensemble Gate（投票机制，readme 7.4）
                t_eg = time.time()
                with (tracer.trace("evidence_gate") if tracer else contextlib.nullcontext()):
                    # 应用 A/B 实验覆盖（权重 / 阈值）
                    evidence_weights = ab_overrides.get("evidence_gate_weights")
                    evidence_thresholds = ab_overrides.get("evidence_gate_thresholds")
                    # PRD §4.4: Rewrite 降级时 Evidence Gate 进入保守模式（阈值 ≥0.8）
                    ctx.evidence_result = self.evidence_gate.evaluate(
                        query=ctx.rewrite_result.rewritten_query,
                        rerank_results=ctx.rerank_results,
                        retrieval_agreement_score=ctx.retrieval_agreement_score,
                        weights_override=evidence_weights,
                        thresholds_override=evidence_thresholds,
                        conservative_mode=is_fallback,
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
                        business_type=ctx.rewrite_result.business_type if ctx.rewrite_result else "",
                    )
                    ctx.final_response = "无法确认相关信息，请补充更多细节或换个方式提问。"
                    return ctx.final_response

                # 证据锁定
                ctx.evidence_locked_doc_ids = [r.doc_id for r in ctx.evidence_result.top_docs[:3]]

                # ⑬ LLM 生成（含 KV 降级适配 + Prefix Caching 保护）
                t_gen = time.time()
                with (tracer.trace("generation") if tracer else contextlib.nullcontext()):
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
                with (tracer.trace("answer_gate") if tracer else contextlib.nullcontext()):
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
                        business_type=ctx.rewrite_result.business_type if ctx.rewrite_result else "",
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

            except InfrastructureError as e:
                # PRD §8: 基础设施故障 → HTTP 503（由上层 API 捕获并返回 503）
                logger.error(f"基础设施故障: {e.service} - {e}")
                ctx.degraded = True
                ctx.fallback_reason = f"infrastructure:{e.service}:{e}"
                ctx.final_response = "系统服务暂时不可用，请稍后重试。"
                raise  # 重新抛出，让 API 层返回 HTTP 503

            except Exception as e:
                # PRD §8: 业务逻辑异常 → HTTP 200 + 结构化拒答
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
        finally:
            if trace_ctx:
                trace_ctx.__exit__(None, None, None)

    # ─── 内部辅助方法 ────────────────────────────────────────

    def _build_cache_key(self, ctx) -> str:
        """
        缓存 Key 统一构造（readme 10.2）

        cache_key = hash(normalized_query + embedding_version + knowledge_version_epoch
                         + prompt_version + schema_fingerprint + role_mask + dept_mask)

        PRD §10.2: schema_version 与 API 响应结构强绑定。此处使用
        基于 API response model 字段的 SHA-256 fingerprint 替代静态
        schema_version，确保 API 结构变更时缓存自动隔离。
        """
        import hashlib as _hl
        from api.models import QueryResponse, ChatResponse
        rewrite_q = ctx.rewrite_result.rewritten_query if ctx.rewrite_result else ctx.user_input

        # PRD §10.2: schema fingerprint — 基于 response model 字段哈希
        schema_fields = sorted(QueryResponse.model_fields.keys()) + sorted(ChatResponse.model_fields.keys())
        schema_fingerprint = _hl.sha256("|".join(schema_fields).encode()).hexdigest()[:12]

        key_data = {
            "q": rewrite_q,
            "ev": config["embedding"]["text"]["model_path"],  # embedding_version
            "ke": config.get("knowledge_version_epoch", "default"),
            "pv": config["generation"]["prompt_version"],
            "sf": schema_fingerprint,  # PRD §10.2: schema fingerprint
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

    def _build_clip_filter(self, ctx) -> str:
        """构建 CLIP 异步召回的 Milvus 过滤表达式"""
        from auth.bitmask_rbac import build_milvus_filter
        active_epoch = config.get("knowledge_version_epoch", "default")
        return build_milvus_filter(ctx.user_role_mask, ctx.user_dept_mask, active_epoch)

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
                business_type=ctx.rewrite_result.business_type if ctx.rewrite_result else "",
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
            business_type=ctx.rewrite_result.business_type if ctx.rewrite_result else "",
        )
        return msg

    @staticmethod
    def _fallback_rewrite(user_input: str):
        """Query Rewrite 失败降级（readme 4.4）- BERT 意图分类 + 增强规则分类"""
        from core.pipeline_context import QueryRewriteResult

        business_type = "general"
        intent = "general"

        # PRD §4.4: 结构兜底先尝试 BERT 意图分类器
        bert_score = 0.0
        try:
            from models.complexity_evaluator import ComplexityEvaluator
            # 创建独立的 BERT 评估器实例做意图分类
            evaluator = ComplexityEvaluator()
            is_complex = evaluator.evaluate(user_input)
            if is_complex:
                # 复杂查询进一步用关键词细化
                regulation_keywords = ["法规", "合规", "标准", "备案", "许可", "标准号", "GB", "禁用"]
                if any(kw in user_input for kw in regulation_keywords):
                    business_type = "regulation"
                    intent = "compliance"
                else:
                    business_type = "development"
                    intent = "formulation"
                bert_score = 0.7
        except Exception:
            pass

        # PRD §4.4: BERT 分类未命中时降级为关键词规则
        if bert_score == 0:
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
            confidence=0.3 if bert_score == 0 else 0.5,
            fallback=True,
        )

    def _should_use_clip_sync(self, rewrite_result) -> tuple[bool, int]:
        """
        CLIP 判别式三档同步路由（PRD §4.5）

        三分量判别器：
        1. keyword_rule_score: 关键词规则评分
        2. bert_classifier_score: BERT 意图分类评分（或规则代理）
        3. centroid_similarity: Query 向量与图像库质心的相似度

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

        # PRD §4.5 第三分量: query_emb vs image_centroid_sim
        centroid_score = 0.0
        try:
            # 获取图像库质心（懒计算，从 embedding_service 获取）
            image_centroid = self.embedding_service.get_image_centroid()
            if image_centroid is not None:
                query_emb = self.embedding_service.encode_text(query_text)
                import numpy as np
                centroid_score = float(np.dot(query_emb, image_centroid) / (
                    np.linalg.norm(query_emb) * np.linalg.norm(image_centroid) + 1e-8
                ))
                centroid_score = max(centroid_score, 0.0)
        except Exception as e:
            logger.debug(f"图像质心相似度计算失败: {e}")

        score = max(keyword_score, intent_score, centroid_score)
        logger.debug(
            f"CLIP 路由评分: keyword={keyword_score:.1f}, intent={intent_score:.1f}, "
            f"centroid={centroid_score:.1f}, total={score:.1f}"
        )

        if score >= 0.6:
            return True, 50   # 全量同步
        elif score >= 0.3:
            return True, 20   # 低成本同步
        else:
            return False, 0   # 跳过

    def _merge_and_dedup(self, recall_results: list, business_type: str = "general",
                         is_visual_relevant: bool = False) -> list:
        """
        Union 合并去冗 + 动态加权 RRF 融合（PRD §7.1 / §4.5 / §6）

        RRF 公式: final_score = src_score / (k + rank)
        动态权重（PRD §4.5 §6）:
        - w_clip: 视觉相关查询时提升至 2.0，否则使用 config 默认值
        - w_text: 法规查询时提升至 1.5（PRD §6 法规查询 ES 权重 1.5）
        - 权重初始值由 config.json 设定，线上由 A/B 实验持续校准
        """
        rrf_cfg = config.get("retrieval", {}).get("rrf", {})
        k = rrf_cfg.get("k", 60)
        base_weights = rrf_cfg.get("weights", {"w_text": 1.0, "w_clip": 1.0, "w_ocr": 0.8})

        # PRD §4.5 §6: 动态权重映射
        weights = base_weights.copy()
        if is_visual_relevant:
            weights["w_clip"] = 2.0  # PRD §6: 视觉相关度高时 CLIP 权重提升至 2.0
        if business_type == "regulation":
            weights["w_text"] = 1.5  # PRD §6: 法规查询 ES 权重 1.5

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
        1. 首先生成结构化大纲（PRD: outline + answer 格式）
        2. 根据大纲定位已完成章节，模型仅补充剩余章节内容
        3. 通过 generate_continuation 填充
        """
        from core.pipeline_context import GenerationResult

        try:
            session_state = SessionState.get_or_create(ctx.session_id or "default")

            # PRD §4.6: 法规类强制启用 Answer Plan — 首次截断后生成大纲
            business_type = ctx.rewrite_result.business_type if ctx.rewrite_result else "general"
            if business_type == "regulation" and not ctx.generation_result.answer_outline:
                # 生成结构化大纲
                outline = self._generate_outline(ctx, target_model)
                if outline:
                    ctx.generation_result.answer_outline = outline
                    logger.info(f"Answer Plan 大纲生成: {outline}")

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
                answer_outline=ctx.generation_result.answer_outline,
            )
        except Exception as e:
            logger.warning(f"结构化续写失败，使用首段结果: {e}")
            return ctx.generation_result

    def _generate_outline(self, ctx, target_model: str) -> list[str]:
        """
        PRD §4.6: 生成结构化大纲

        要求模型输出 outline 列表，后续续写时按大纲定位未完成章节。
        """
        try:
            messages = self._build_messages(ctx)
            messages.append({
                "role": "user",
                "content": (
                    "请根据已提供的证据，列出回答此问题的结构化大纲（JSON 格式）。\n"
                    "格式: {\"outline\": [\"章节1\", \"章节2\", ...]}\n"
                    "仅输出 JSON，不要输出其他内容。"
                ),
            })

            endpoint_key = self.llm_client._resolve_endpoint(target_model)
            answer = self.llm_client.router.route_chat(
                endpoint_key,
                messages=messages,
                max_tokens=128,
                temperature=0.0,
            )

            # 解析 JSON 大纲
            import re
            json_match = re.search(r'\{[^{}]*"outline"\s*:\s*\[([^\]]*)\][^{}]*\}', answer)
            if json_match:
                items = re.findall(r'"([^"]+)"', json_match.group(1))
                if items:
                    return items
        except Exception as e:
            logger.debug(f"大纲生成失败: {e}")
        return []

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
