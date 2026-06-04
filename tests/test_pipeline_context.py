"""
Pipeline Context 核心数据结构测试 (core/pipeline_context.py)

覆盖核心数据结构的生命周期与行为：
- RequestContext 创建、字段默认值、计时记录
- SessionState 多轮对话管理
- FIFO 6 轮限制
- Evidence Locking
- SessionState.get_or_create 单例管理
- SessionState.cleanup_expired 过期清理
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import time
from core.pipeline_context import (
    RequestContext,
    SessionState,
    QueryRewriteResult,
    RecallResult,
    RerankResult,
    EvidenceGateResult,
    GenerationResult,
    AnswerGateResult,
)


# ── RequestContext 测试 ──

class TestRequestContext:
    """测试 RequestContext 的创建和生命周期"""

    def test_default_values(self):
        """默认字段值正确"""
        ctx = RequestContext()
        assert ctx.user_input == ""
        assert ctx.image_path is None
        assert ctx.session_id is None
        assert ctx.user_id == ""
        assert ctx.user_role_mask == 0
        assert ctx.user_dept_mask == 0
        assert ctx.degraded is False
        assert ctx.fallback_reason is None
        assert ctx.kv_pressure_at_entry == 0.0

    def test_unique_request_id(self):
        """每次创建生成唯一的 request_id"""
        ctx1 = RequestContext()
        ctx2 = RequestContext()
        assert ctx1.request_id != ctx2.request_id

    def test_request_id_length(self):
        """request_id 长度为 12（UUID 前 12 位）"""
        ctx = RequestContext()
        assert len(ctx.request_id) == 12

    def test_timestamp_auto_set(self):
        """timestamp 自动设置为当前时间"""
        before = time.time()
        ctx = RequestContext()
        after = time.time()
        assert before <= ctx.timestamp <= after

    def test_custom_user_input(self):
        """可以自定义 user_input"""
        ctx = RequestContext(user_input="测试输入", user_id="user_001")
        assert ctx.user_input == "测试输入"
        assert ctx.user_id == "user_001"

    def test_record_timing(self):
        """record_timing 记录各阶段延迟"""
        ctx = RequestContext()
        ctx.record_timing("rewrite", 10.5)
        ctx.record_timing("retrieval", 50.2)
        assert ctx.stage_timings["rewrite"] == 10.5
        assert ctx.stage_timings["retrieval"] == 50.2

    def test_get_total_latency_ms(self):
        """get_total_latency_ms 计算各阶段延迟之和"""
        ctx = RequestContext()
        ctx.record_timing("rewrite", 10.0)
        ctx.record_timing("retrieval", 50.0)
        ctx.record_timing("rerank", 20.0)
        assert ctx.get_total_latency_ms() == 80.0

    def test_empty_total_latency(self):
        """无阶段数据时总延迟为 0"""
        ctx = RequestContext()
        assert ctx.get_total_latency_ms() == 0.0

    def test_stage_timings_overwrite(self):
        """同一阶段重复记录会覆盖"""
        ctx = RequestContext()
        ctx.record_timing("rewrite", 10.0)
        ctx.record_timing("rewrite", 20.0)
        assert ctx.stage_timings["rewrite"] == 20.0
        assert ctx.get_total_latency_ms() == 20.0

    def test_optional_fields_default_none(self):
        """可选复杂字段默认为 None 或空"""
        ctx = RequestContext()
        assert ctx.rewrite_result is None
        assert ctx.evidence_result is None
        assert ctx.generation_result is None
        assert ctx.answer_gate_result is None

    def test_list_fields_default_empty(self):
        """列表字段默认为空"""
        ctx = RequestContext()
        assert ctx.recall_results == []
        assert ctx.union_recall_set == []
        assert ctx.rerank_results == []
        assert ctx.evidence_locked_doc_ids == []
        assert ctx.stage_timings == {}

    def test_final_response_default(self):
        """final_response 默认为空字符串"""
        ctx = RequestContext()
        assert ctx.final_response == ""


# ── SessionState 多轮对话管理 ──

class TestSessionStateDialogRounds:
    """测试 SessionState 的多轮对话管理"""

    def setup_method(self):
        """每个测试前清理全局会话"""
        SessionState._sessions.clear()

    def test_add_single_round(self):
        """添加一轮对话"""
        state = SessionState(session_id="s1")
        state.add_round("问题1", "回答1")
        assert len(state.dialog_rounds) == 1
        assert state.dialog_rounds[0]["user_input"] == "问题1"
        assert state.dialog_rounds[0]["response"] == "回答1"

    def test_add_multiple_rounds(self):
        """添加多轮对话"""
        state = SessionState(session_id="s1")
        for i in range(4):
            state.add_round(f"问题{i}", f"回答{i}")
        assert len(state.dialog_rounds) == 4

    def test_fifo_six_round_limit(self):
        """FIFO 保持最近 6 轮"""
        state = SessionState(session_id="s1")
        for i in range(10):
            state.add_round(f"问题{i}", f"回答{i}")
        assert len(state.dialog_rounds) == 6
        # 应保留最后 6 轮（第 4~9 轮）
        assert state.dialog_rounds[0]["user_input"] == "问题4"
        assert state.dialog_rounds[5]["user_input"] == "问题9"

    def test_fifo_exactly_six_rounds(self):
        """恰好 6 轮不触发淘汰"""
        state = SessionState(session_id="s1")
        for i in range(6):
            state.add_round(f"问题{i}", f"回答{i}")
        assert len(state.dialog_rounds) == 6
        assert state.dialog_rounds[0]["user_input"] == "问题0"

    def test_fifo_seventh_round_removes_first(self):
        """第 7 轮添加后移除第 1 轮"""
        state = SessionState(session_id="s1")
        for i in range(7):
            state.add_round(f"问题{i}", f"回答{i}")
        assert len(state.dialog_rounds) == 6
        # 第 0 轮被移除，现在第一轮是 "问题1"
        assert state.dialog_rounds[0]["user_input"] == "问题1"

    def test_add_round_with_rewrite(self):
        """添加带 rewrite 结果的对话轮次"""
        rewrite = QueryRewriteResult(
            rewritten_query="改写后",
            business_type="general",
            intent="general",
            requires_context=True,
        )
        state = SessionState(session_id="s1")
        state.add_round("问题", "回答", rewrite=rewrite)
        assert state.dialog_rounds[0]["rewrite"] is rewrite
        assert state.last_rewrite_result is rewrite

    def test_last_rewrite_updated(self):
        """last_rewrite_result 跟随最新一轮更新"""
        state = SessionState(session_id="s1")
        rewrite1 = QueryRewriteResult("q1", "general", "general", True)
        rewrite2 = QueryRewriteResult("q2", "regulation", "compliance", True)
        state.add_round("问题1", "回答1", rewrite=rewrite1)
        assert state.last_rewrite_result is rewrite1
        state.add_round("问题2", "回答2", rewrite=rewrite2)
        assert state.last_rewrite_result is rewrite2


# ── Evidence Locking ──

class TestEvidenceLocking:
    """测试证据锁定机制"""

    def test_lock_evidence(self):
        """lock_evidence 设置锁定的 doc_id 列表"""
        state = SessionState(session_id="s1")
        state.lock_evidence(["doc_001", "doc_002", "doc_003"])
        assert state.locked_doc_ids == ["doc_001", "doc_002", "doc_003"]

    def test_lock_evidence_replaces_previous(self):
        """lock_evidence 替换之前的锁定"""
        state = SessionState(session_id="s1")
        state.lock_evidence(["doc_001"])
        state.lock_evidence(["doc_002", "doc_003"])
        assert state.locked_doc_ids == ["doc_002", "doc_003"]

    def test_initial_locked_empty(self):
        """初始锁定列表为空"""
        state = SessionState(session_id="s1")
        assert state.locked_doc_ids == []


# ── get_recent_queries ──

class TestGetRecentQueries:
    """测试获取最近查询"""

    def test_get_recent_queries(self):
        """获取最近 n 轮用户查询"""
        state = SessionState(session_id="s1")
        for i in range(8):
            state.add_round(f"query_{i}", f"answer_{i}")
        recent = state.get_recent_queries(n=3)
        assert recent == ["query_5", "query_6", "query_7"]

    def test_get_recent_queries_default_six(self):
        """默认获取最近 6 轮"""
        state = SessionState(session_id="s1")
        for i in range(10):
            state.add_round(f"q_{i}", f"a_{i}")
        recent = state.get_recent_queries()
        assert len(recent) == 6

    def test_get_recent_queries_fewer_than_n(self):
        """对话轮数不足 n 时返回全部"""
        state = SessionState(session_id="s1")
        state.add_round("q1", "a1")
        state.add_round("q2", "a2")
        recent = state.get_recent_queries(n=6)
        assert recent == ["q1", "q2"]


# ── get_or_create / cleanup ──

class TestSessionManagement:
    """测试会话管理的 get_or_create 和 cleanup"""

    def setup_method(self):
        """每个测试前清理全局会话"""
        SessionState._sessions.clear()

    def test_get_or_create_new_session(self):
        """get_or_create 创建新会话"""
        state = SessionState.get_or_create("new_session")
        assert state.session_id == "new_session"
        assert "new_session" in SessionState._sessions

    def test_get_or_create_existing_session(self):
        """get_or_create 返回已有会话（不重复创建）"""
        state1 = SessionState.get_or_create("s1")
        state1.add_round("q1", "a1")
        state2 = SessionState.get_or_create("s1")
        assert state1 is state2
        assert len(state2.dialog_rounds) == 1

    def test_cleanup_expired(self):
        """cleanup_expired 清理超过 max_age 的会话"""
        state = SessionState(session_id="s1")
        state.created_at = time.time() - 7200  # 2 小时前
        SessionState._sessions["s1"] = state
        SessionState._sessions["s2"] = SessionState(session_id="s2")  # 新会话

        SessionState.cleanup_expired(max_age_seconds=3600)
        assert "s1" not in SessionState._sessions
        assert "s2" in SessionState._sessions

    def test_cleanup_keeps_fresh_sessions(self):
        """cleanup_expired 不清理未过期的会话"""
        state = SessionState(session_id="s1")
        SessionState._sessions["s1"] = state
        SessionState.cleanup_expired(max_age_seconds=3600)
        assert "s1" in SessionState._sessions


# ── Async CLIP Results ──

class TestAsyncClipResults:
    """测试异步 CLIP 补充召回结果存储"""

    def test_store_async_clip_results(self):
        """存储 CLIP 异步结果"""
        state = SessionState(session_id="s1")
        results = [
            RecallResult(doc_id="img_001", content="图片描述", score=0.9, source="clip_visual"),
            RecallResult(doc_id="img_002", content="图片描述2", score=0.85, source="clip_visual"),
        ]
        state.store_async_clip_result(results)
        assert len(state.async_clip_results) == 2
        assert state.async_clip_results[0].doc_id == "img_001"


# ── 数据类结构验证 ──

class TestDataClassStructure:
    """验证各数据类字段定义正确"""

    def test_rerank_result_fields(self):
        """RerankResult 包含所有评分字段"""
        r = RerankResult(doc_id="d1", content="text")
        assert r.ce_score_a == 0.0
        assert r.ce_score_b == 0.0
        assert r.ce_score_ensemble == 0.0
        assert r.bi_score == 0.0
        assert r.nli_score == 0.0
        assert r.final_score == 0.0

    def test_generation_result_defaults(self):
        """GenerationResult 默认值正确"""
        g = GenerationResult(answer="答案", model_used="qwen3-4b", max_tokens=512)
        assert g.has_more is False
        assert g.session_id is None
        assert g.answer_outline == []

    def test_answer_gate_result_fields(self):
        """AnswerGateResult 字段定义正确"""
        a = AnswerGateResult(
            nli_contradiction_score=0.3,
            nli_entailment_score=0.7,
            passed=True,
        )
        assert a.warning is False
        assert a.is_regulation is False

    def test_recall_result_defaults(self):
        """RecallResult 默认 metadata 为空"""
        r = RecallResult(doc_id="d1", content="c", score=0.5, source="dense_bge")
        assert r.metadata == {}
