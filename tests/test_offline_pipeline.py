"""
test_offline_pipeline.py — 新增测试: DocumentProcessor 文本切块、
IncrementalStateManager 增量检测、OfflineScheduler 权限注入、OCR 流程。
"""
import os
import sys
import tempfile
import types
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["DEPLOYMENT_MODE"] = "development"

# torch mock shim
try:
    import torch  # noqa: F401
except ImportError:
    _fake_torch = types.ModuleType("torch")
    _fake_cuda = types.ModuleType("torch.cuda")
    _fake_cuda.is_available = lambda: False
    _fake_cuda.OutOfMemoryError = type("OutOfMemoryError", (Exception,), {})
    _fake_torch.cuda = _fake_cuda
    sys.modules["torch"] = _fake_torch
    sys.modules["torch.cuda"] = _fake_cuda


# ===========================================================================
# 1. DocumentProcessor — Text Chunking
# ===========================================================================

class TestDocumentProcessorChunking:
    """DocumentProcessor._chunk_text() 文本切块测试。"""

    def _make_processor(self, chunk_size=500, overlap_ratio=0.1):
        from offline.document_processor import DocumentProcessor
        proc = DocumentProcessor.__new__(DocumentProcessor)
        proc.chunk_size = chunk_size
        proc.chunk_overlap = int(chunk_size * overlap_ratio)
        proc._state_manager = None
        return proc

    def test_short_text_single_chunk(self):
        """短于 chunk_size 的文本应返回单个块。"""
        proc = self._make_processor(chunk_size=500)
        chunks = proc._chunk_text("这是一段短文本")
        assert len(chunks) == 1
        assert chunks[0] == "这是一段短文本"

    def test_long_text_multiple_chunks(self):
        """长于 chunk_size 的文本应被切分为多个块。"""
        proc = self._make_processor(chunk_size=100, overlap_ratio=0.0)
        text = "第一段内容。\n\n" + "A" * 80 + "\n\n" + "第三段内容。"
        chunks = proc._chunk_text(text)
        assert len(chunks) >= 2

    def test_chunk_respects_paragraph_boundaries(self):
        """切块应优先按段落边界切分。"""
        proc = self._make_processor(chunk_size=100, overlap_ratio=0.0)
        text = "段落一。\n\n段落二。\n\n段落三。"
        chunks = proc._chunk_text(text)
        # 每个段落较短，应各自成为独立块
        assert len(chunks) >= 3

    def test_chunk_overlap_adds_prefix(self):
        """重叠模式下，后续块应包含前一个块的尾部。"""
        proc = self._make_processor(chunk_size=50, overlap_ratio=0.2)
        # overlap = 50 * 0.2 = 10 chars
        text = "A" * 40 + "\n\n" + "B" * 40 + "\n\n" + "C" * 40
        chunks = proc._chunk_text(text)
        if len(chunks) > 1:
            # 第二个块应以第一个块的最后 10 个字符开头
            assert chunks[1].startswith(chunks[0][-10:])

    def test_clean_text_removes_page_numbers(self):
        """文本清洗应去除页码模式。"""
        proc = self._make_processor()
        text = "内容正文\n第 3 页 / 共 10 页\n更多内容"
        cleaned = proc._clean_text(text)
        assert "第 3 页" not in cleaned
        assert "共 10 页" not in cleaned

    def test_clean_text_removes_separator_lines(self):
        """文本清洗应去除分隔线。"""
        proc = self._make_processor()
        text = "内容一\n***\n内容二\n===\n内容三"
        cleaned = proc._clean_text(text)
        assert "***" not in cleaned
        assert "===" not in cleaned

    def test_clean_text_normalizes_whitespace(self):
        """文本清洗应规范化空白。"""
        proc = self._make_processor()
        text = "  多余   空格  \n\n\n\n\n  和换行  "
        cleaned = proc._clean_text(text)
        assert "   " not in cleaned  # 无连续空格
        assert "\n\n\n" not in cleaned  # 无连续三换行

    def test_process_document_txt(self):
        """process_document 应处理 .txt 文件并返回结构化块。"""
        proc = self._make_processor(chunk_size=100, overlap_ratio=0.0)
        content = "第一段内容\n\n第二段内容\n\n第三段内容"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(content)
            fpath = f.name

        try:
            chunks = proc.process_document(fpath, doc_type="general")
            assert len(chunks) >= 1
            for chunk in chunks:
                assert "content" in chunk
                assert "metadata" in chunk
                assert "chunk_index" in chunk["metadata"]
                assert chunk["metadata"]["source_file"].endswith(".txt")
        finally:
            os.unlink(fpath)

    def test_process_document_empty_file(self):
        """空文件应返回空列表。"""
        proc = self._make_processor()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("")
            fpath = f.name
        try:
            chunks = proc.process_document(fpath)
            assert chunks == []
        finally:
            os.unlink(fpath)


# ===========================================================================
# 2. IncrementalStateManager — mtime + content_hash Detection
# ===========================================================================

class TestIncrementalStateManager:
    """IncrementalStateManager 增量更新差异检测测试。"""

    def _make_manager(self, state_file=None):
        from offline.document_processor import IncrementalStateManager
        if state_file is None:
            state_file = tempfile.mktemp(suffix=".json")
        mgr = IncrementalStateManager.__new__(IncrementalStateManager)
        mgr._state_file = state_file
        mgr._state = {}
        return mgr

    def test_needs_update_new_file(self):
        """新文件（不在状态中）应判定为需要更新。"""
        mgr = self._make_manager()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("内容")
            fpath = f.name
        try:
            assert mgr.needs_update(fpath) is True
        finally:
            os.unlink(fpath)

    def test_no_update_unchanged_file(self):
        """未修改的文件应判定为不需要更新。"""
        mgr = self._make_manager()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("内容不变")
            fpath = f.name
        try:
            mgr.mark_processed(fpath)
            assert mgr.needs_update(fpath) is False
        finally:
            os.unlink(fpath)

    def test_needs_update_content_changed(self):
        """内容变更的文件应判定为需要更新。"""
        mgr = self._make_manager()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("原始内容")
            fpath = f.name
        try:
            mgr.mark_processed(fpath)
            # 修改文件内容
            import time
            time.sleep(0.01)  # 确保 mtime 不同
            with open(fpath, "w", encoding="utf-8") as f2:
                f2.write("新内容")
            assert mgr.needs_update(fpath) is True
        finally:
            os.unlink(fpath)

    def test_no_update_mtime_changed_content_same(self):
        """mtime 变化但内容未变（touch）应判定为不需要更新。"""
        mgr = self._make_manager()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("相同内容")
            fpath = f.name
        try:
            # 第一次处理
            mgr.mark_processed(fpath)
            # touch 操作改变 mtime 但不改变内容
            stat = os.stat(fpath)
            os.utime(fpath, (stat.st_mtime + 10, stat.st_mtime + 10))
            # 内容相同，不需要更新
            assert mgr.needs_update(fpath) is False
        finally:
            os.unlink(fpath)

    def test_mark_deleted_sets_archived(self):
        """mark_deleted 应将文件状态设为 archived。"""
        mgr = self._make_manager()
        fpath = "/tmp/test_doc.txt"
        mgr._state[fpath] = {
            "path": fpath, "mtime": 1000.0,
            "content_hash": "abc123", "status": "active",
        }
        mgr.mark_deleted(fpath)
        assert mgr._state[fpath]["status"] == "archived"

    def test_detect_deleted_files(self):
        """detect_deleted_files 应识别已删除的文件。"""
        mgr = self._make_manager()
        mgr._state["/tmp/existing.txt"] = {
            "path": "/tmp/existing.txt", "mtime": 1000.0,
            "content_hash": "abc", "status": "active",
        }
        mgr._state["/tmp/deleted.txt"] = {
            "path": "/tmp/deleted.txt", "mtime": 1000.0,
            "content_hash": "def", "status": "active",
        }
        current = {"/tmp/existing.txt"}
        deleted = mgr.detect_deleted_files(current)
        assert "/tmp/deleted.txt" in deleted

    def test_get_active_paths(self):
        """get_active_paths 应只返回 active 状态的文件。"""
        mgr = self._make_manager()
        mgr._state["/tmp/a.txt"] = {"status": "active", "mtime": 0, "content_hash": ""}
        mgr._state["/tmp/b.txt"] = {"status": "archived", "mtime": 0, "content_hash": ""}
        active = mgr.get_active_paths()
        assert "/tmp/a.txt" in active
        assert "/tmp/b.txt" not in active

    def test_reset_clears_state(self):
        """reset 应清空所有状态。"""
        mgr = self._make_manager()
        mgr._state["/tmp/a.txt"] = {"status": "active"}
        mgr.reset()
        assert len(mgr._state) == 0

    def test_compute_content_hash_consistent(self):
        """同一文件多次计算 content_hash 应一致。"""
        from offline.document_processor import IncrementalStateManager
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("测试内容用于哈希计算")
            fpath = f.name
        try:
            h1 = IncrementalStateManager.compute_content_hash(fpath)
            h2 = IncrementalStateManager.compute_content_hash(fpath)
            assert h1 == h2
            assert len(h1) == 32  # MD5 hex length
        finally:
            os.unlink(fpath)

    def test_needs_update_archived_file_resurfaces(self):
        """已归档的文件重新出现应判定为需要更新。"""
        mgr = self._make_manager()
        fpath = "/tmp/resurface.txt"
        mgr._state[fpath] = {
            "path": fpath, "mtime": 1000.0,
            "content_hash": "abc", "status": "archived",
        }
        # 即使文件不存在，archived 状态也应触发更新
        # （实际上 needs_update 先获取 mtime，如 mtime=0 与旧值不同会走 hash 路径）
        assert mgr.needs_update(fpath) is True


# ===========================================================================
# 3. OfflineScheduler — Permission Injection
# ===========================================================================

class TestOfflineSchedulerPermission:
    """OfflineScheduler._resolve_permission() 权限注入测试。"""

    def _make_scheduler(self):
        from offline.scheduler import OfflineScheduler
        scheduler = OfflineScheduler.__new__(OfflineScheduler)
        scheduler.data_dir = "/tmp/test_data"
        scheduler._doc_processor = None
        scheduler._image_processor = None
        scheduler._vectorizer = None
        scheduler.config_path = "config.json"
        scheduler._fingerprints = {}
        return scheduler

    @patch("offline.scheduler.config", {
        "permission_rules": {
            "rules": [
                {"path_pattern": "regulations", "role_mask": 0xFF, "dept_mask": 0x01},
            ],
            "default_role_mask": 0x01,
            "default_dept_mask": 0x00,
        },
    })
    def test_permission_from_path_pattern(self, ):
        """路径匹配规则应返回正确的 role_mask, dept_mask。"""
        scheduler = self._make_scheduler()
        role, dept = scheduler._resolve_permission("/tmp/test_data/regulations/法规.docx")
        assert role == 0xFF
        assert dept == 0x01

    @patch("offline.scheduler.config", {
        "permission_rules": {
            "rules": [
                {"path_pattern": "regulations", "role_mask": 0xFF, "dept_mask": 0x01},
            ],
            "default_role_mask": 0x02,
            "default_dept_mask": 0x04,
        },
    })
    def test_permission_default_fallback(self):
        """无匹配规则时应返回默认权限。"""
        scheduler = self._make_scheduler()
        role, dept = scheduler._resolve_permission("/tmp/test_data/general/通用文档.txt")
        assert role == 0x02
        assert dept == 0x04

    @patch("offline.scheduler.config", {
        "permission_rules": {
            "rules": [
                {"path_pattern": "confidential", "role_mask": 0xFF, "dept_mask": 0xFF},
                {"path_pattern": "confidential", "role_mask": 0x01, "dept_mask": 0x01},
            ],
            "default_role_mask": 0x00,
            "default_dept_mask": 0x00,
        },
    })
    def test_permission_first_match_wins(self):
        """多条规则匹配时应使用第一条命中规则。"""
        scheduler = self._make_scheduler()
        role, dept = scheduler._resolve_permission("/tmp/test_data/confidential/机密.docx")
        assert role == 0xFF
        assert dept == 0xFF

    @patch("offline.scheduler.config", {
        "permission_rules": {"rules": [], "default_role_mask": 0, "default_dept_mask": 0},
    })
    def test_fingerprint_computation(self):
        """文件指纹应包含 mtime 和 size 信息。"""
        scheduler = self._make_scheduler()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("内容")
            fpath = f.name
        try:
            fp = scheduler._compute_file_fingerprint(fpath)
            assert ":" in fp  # format: mtime:size
            mtime_str, size_str = fp.split(":")
            assert float(mtime_str) > 0
            assert int(size_str) > 0
        finally:
            os.unlink(fpath)

    @patch("offline.scheduler.config", {
        "permission_rules": {"rules": [], "default_role_mask": 0, "default_dept_mask": 0},
    })
    def test_has_file_changed_first_time(self):
        """首次检测（无指纹缓存）应判定为已变更。"""
        scheduler = self._make_scheduler()
        assert scheduler._has_file_changed("/tmp/nonexistent.txt") is True

    @patch("offline.scheduler.config", {
        "permission_rules": {"rules": [], "default_role_mask": 0, "default_dept_mask": 0},
    })
    def test_has_file_changed_unchanged(self):
        """已处理且未修改的文件应判定为未变更。"""
        scheduler = self._make_scheduler()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("内容")
            fpath = f.name
        try:
            scheduler._mark_file_processed(fpath)
            assert scheduler._has_file_changed(fpath) is False
        finally:
            os.unlink(fpath)

    @patch("offline.scheduler.config", {})
    def test_generate_epoch_format(self):
        """epoch 格式应为 YYYYMMDD_HH。"""
        scheduler = self._make_scheduler()
        epoch = scheduler._generate_epoch()
        import re
        assert re.match(r"\d{8}_\d{2}", epoch) is not None


# ===========================================================================
# 4. OCR Image Preprocessing Pipeline (mock)
# ===========================================================================

class TestOCRPreprocessingPipeline:
    """OCR 图片预处理管线测试（mock ImageProcessor）。"""

    @patch("offline.scheduler.config", {
        "knowledge_base": {"data_dir": "/tmp/test_images"},
        "permission_rules": {"rules": [], "default_role_mask": 0, "default_dept_mask": 0},
    })
    def test_incremental_update_processes_images(self):
        """增量更新应处理图片文件并调用 vectorizer。"""
        from offline.scheduler import OfflineScheduler
        scheduler = OfflineScheduler.__new__(OfflineScheduler)
        scheduler.data_dir = "/tmp/test_images"
        scheduler._fingerprints = {}
        scheduler.config_path = "config.json"

        # mock processors
        mock_image_proc = MagicMock()
        mock_image_proc.process_image.return_value = {
            "metadata": {"source_file": "test.png"},
            "clip_features": [0.1] * 512,
            "ocr_main_text": "识别出的文字内容",
        }
        scheduler._image_processor = mock_image_proc

        mock_vectorizer = MagicMock()
        scheduler._vectorizer = mock_vectorizer

        mock_doc_proc = MagicMock()
        scheduler._doc_processor = mock_doc_proc

        # 创建临时目录和文件
        with tempfile.TemporaryDirectory() as tmpdir:
            img_path = os.path.join(tmpdir, "test.png")
            with open(img_path, "wb") as f:
                f.write(b"\x89PNG" + b"\x00" * 100)  # minimal PNG-like content

            scheduler.data_dir = tmpdir

            result = scheduler.run_incremental_update()

            # 应调用了 image processor
            mock_image_proc.process_image.assert_called_once()
            # 应调用了 vectorizer (image + OCR text)
            assert mock_vectorizer.vectorize_and_store_image.call_count >= 1
            assert mock_vectorizer.vectorize_and_store_text.call_count >= 1
            assert result["processed_images"] >= 1

    @patch("offline.scheduler.config", {
        "knowledge_base": {"data_dir": "/tmp/test_skip"},
        "permission_rules": {"rules": [], "default_role_mask": 0, "default_dept_mask": 0},
    })
    def test_incremental_update_skips_unchanged_files(self):
        """增量更新应跳过未变更的文件。"""
        from offline.scheduler import OfflineScheduler
        scheduler = OfflineScheduler.__new__(OfflineScheduler)
        scheduler._fingerprints = {}
        scheduler.config_path = "config.json"
        mock_doc_proc = MagicMock()
        mock_doc_proc.process_document.return_value = [{"content": "c", "metadata": {}}]
        scheduler._doc_processor = mock_doc_proc
        scheduler._image_processor = MagicMock()
        mock_vectorizer = MagicMock()
        scheduler._vectorizer = mock_vectorizer

        with tempfile.TemporaryDirectory() as tmpdir:
            txt_path = os.path.join(tmpdir, "doc.txt")
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write("文档内容")
            scheduler.data_dir = tmpdir

            # 第一次运行
            r1 = scheduler.run_incremental_update()
            assert r1["processed_docs"] == 1

            # 第二次运行（文件未变更，应跳过）
            r2 = scheduler.run_incremental_update()
            assert r2["skipped_files"] >= 1
