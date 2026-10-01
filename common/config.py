"""
Singleton configuration loader for the RAG system.

Usage:
    from common.config import get_config
    cfg = get_config()
    pass
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path resolution helpers
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DOTENV_LOADED = False


def _load_project_dotenv() -> None:
    """Best-effort `.env` loader for local and deployment parity.

    The repo documentation instructs users to copy `.env.example` to `.env`.
    Codex and plain `python3 app.py` runs do not load that file automatically,
    so we hydrate unset environment variables here instead of requiring callers
    to wrap every command with `dotenv run`.
    """
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return

    env_path = _PROJECT_ROOT / ".env"
    if not env_path.is_file():
        _DOTENV_LOADED = True
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        # Strip inline comments from unquoted values like `FOO=bar  # note`.
        cleaned = value.strip()
        if cleaned and cleaned[0] not in {'"', "'"} and " #" in cleaned:
            cleaned = cleaned.split(" #", 1)[0].rstrip()
        if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {'"', "'"}:
            cleaned = cleaned[1:-1]
        os.environ[key] = cleaned

    _DOTENV_LOADED = True


_load_project_dotenv()


def _parse_bool_env(value: str | None) -> bool | None:
    """Parse a boolean environment variable value."""
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return None


def _apply_env_overrides(raw: dict) -> dict:
    """Return a copied config dict with supported environment overrides applied."""
    raw = json.loads(json.dumps(raw))

    env_deployment_mode = os.environ.get("DEPLOYMENT_MODE", "").strip().lower()
    if env_deployment_mode in {"production", "testing", "development"}:
        raw["deployment_mode"] = env_deployment_mode

    auth_data = raw.setdefault("auth", {})
    env_auth_dev_mode = _parse_bool_env(os.environ.get("AUTH_DEV_MODE"))
    if env_auth_dev_mode is not None:
        auth_data["dev_mode"] = env_auth_dev_mode

    return raw


def _resolve_config_path() -> Path:
    """Return the absolute path to config.json, honouring CONFIG_PATH env var."""
    env_path = os.environ.get("CONFIG_PATH")
    if env_path:
        return Path(env_path)
    return _PROJECT_ROOT / "config.json"


# ---------------------------------------------------------------------------
# Dataclasses that mirror the JSON structure (read-only view)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SystemConfig:
    name: str = ""
    version: str = "2.0.0"
    dual_gpu: bool = False


@dataclass(frozen=True)
class VllmModelConfig:
    name: str = ""
    engine: str = "vllm"
    port: int = 0
    model_path: str = ""
    max_model_len: int = 4096
    max_tokens: int = 512
    gpu_memory_utilization: float = 0.85
    kv_cache_budget_gb: float = 8.0
    max_output_tokens: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class BertModelConfig:
    name: str = ""
    model_path: str = ""
    device: str = "cpu"
    trigger_threshold: float = 0.0


@dataclass(frozen=True)
class GpuModelsConfig:
    """Holds the two possible model sub-sections under a GPU config."""

    gen_14b: VllmModelConfig | None = None
    vllm_rewrite: VllmModelConfig | None = None
    vllm_gen_4b: VllmModelConfig | None = None
    bert_complexity: BertModelConfig | None = None
    cross_encoder_a: BertModelConfig | None = None
    cross_encoder_b: BertModelConfig | None = None
    nli_model: BertModelConfig | None = None
    bi_encoder: BertModelConfig | None = None
    clip_image_encoder: BertModelConfig | None = None
    clip_text_encoder: BertModelConfig | None = None
    blip: BertModelConfig | None = None


@dataclass(frozen=True)
class RerankBatchAggregatorConfig:
    time_window_ms: int = 15
    max_batch_size: int = 64
    max_pair_batch_size: int = 40


@dataclass(frozen=True)
class GpuConfig:
    role: str = ""
    models: GpuModelsConfig = field(default_factory=GpuModelsConfig)
    rerank_batch_aggregator: RerankBatchAggregatorConfig | None = None


@dataclass(frozen=True)
class EmbeddingEntryConfig:
    model_path: str = ""
    dimension: int = 768
    collection: str = ""


@dataclass(frozen=True)
class EmbeddingConfig:
    text: EmbeddingEntryConfig = field(default_factory=EmbeddingEntryConfig)
    image_clip: EmbeddingEntryConfig = field(default_factory=EmbeddingEntryConfig)


@dataclass(frozen=True)
class QdrantCollectionConfig:
    dimension: int = 768
    distance: str = "Cosine"


@dataclass(frozen=True)
class QdrantConfig:
    host: str = "localhost"
    port: int = 6333
    grpc_port: int = 6334
    collections: dict[str, QdrantCollectionConfig] = field(default_factory=dict)


@dataclass(frozen=True)
class ElasticsearchConfig:
    host: str = "http://localhost:9200"
    index: str = "cosmetics_docs"
    enabled: bool = True
    username: str = ""
    password: str = ""


@dataclass(frozen=True)
class RedisInstanceConfig:
    host: str = "localhost"
    port: int = 6379
    db: int = 0
    ttl_seconds: int = 3600


@dataclass(frozen=True)
class RedisConfig:
    cache: RedisInstanceConfig = field(default_factory=RedisInstanceConfig)
    state: RedisInstanceConfig = field(default_factory=RedisInstanceConfig)


@dataclass(frozen=True)
class OcrConfig:
    engine: str = "paddleocr"
    language: str = "ch"
    visual_weight_repeat: int = 3


@dataclass(frozen=True)
class KnowledgeBaseConfig:
    data_dir: str = "./data"
    chunk_size: int = 500
    chunk_overlap_ratio: float = 0.1
    ocr: OcrConfig = field(default_factory=OcrConfig)


@dataclass(frozen=True)
class QueryRewriteConfig:
    max_output_tokens: int = 192
    temperature: float = 0.1
    dialog_rounds: int = 6


@dataclass(frozen=True)
class ParallelPathConfig:
    enabled: bool = True
    top_k: int = 50


@dataclass(frozen=True)
class RrfConfig:
    k: int = 60
    weights: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class CrossEncoderRetrievalConfig:
    ensemble: bool = True
    final_top_k: int = 10


@dataclass(frozen=True)
class EvidenceGateWeights:
    w1: float = 0.4
    w2: float = 0.2
    w3: float = 0.2
    w4: float = 0.2


@dataclass(frozen=True)
class EvidenceGateThresholds:
    high_confidence: float = 0.7
    low_confidence: float = 0.4
    reject: float = 0.3


@dataclass(frozen=True)
class EvidenceGateConfig:
    weights: EvidenceGateWeights = field(default_factory=EvidenceGateWeights)
    thresholds: EvidenceGateThresholds = field(default_factory=EvidenceGateThresholds)


@dataclass(frozen=True)
class RetrievalConfig:
    parallel_paths: dict[str, ParallelPathConfig] = field(default_factory=dict)
    rrf: RrfConfig = field(default_factory=RrfConfig)
    cross_encoder: CrossEncoderRetrievalConfig = field(default_factory=CrossEncoderRetrievalConfig)
    evidence_gate: EvidenceGateConfig = field(default_factory=EvidenceGateConfig)


@dataclass(frozen=True)
class AnswerGateConfig:
    nli_threshold: float = 0.6


@dataclass(frozen=True)
class GenerationConfig:
    max_conversation_rounds: int = 6
    prompt_version: str = "v2.1"
    answer_gate: AnswerGateConfig = field(default_factory=AnswerGateConfig)


@dataclass(frozen=True)
class ModelTierConfig:
    endpoint: str = ""
    label: str = ""


@dataclass(frozen=True)
class ModelRoutingConfig:
    tiers: dict[str, ModelTierConfig] = field(default_factory=dict)
    endpoint_map: dict[str, str] = field(default_factory=dict)
    complexity_fallback: str = "simple"


def resolve_model_endpoint(tier_name: str, is_production: bool = True) -> str:
    """
    根据配置的模型路由表解析端点键名。

    非 production 模式下 'complex' tier 自动降级为 'simple'（单卡场景）。
    避免代码中硬编码模型名称（如 qwen3-4b / qwen3-14b）。

    Args:
        tier_name: 逻辑 tier 名（'complex' / 'simple' / 'rewrite'）
        is_production: 是否为生产模式（决定是否允许复杂模型直连）

    Returns:
        端点键名（如 'gen_4b', 'gen_14b', 'vllm_rewrite'）
    """
    cfg = get_config().model_routing
    if not is_production and tier_name == "complex":
        tier_name = cfg.complexity_fallback
    tier = cfg.tiers.get(tier_name)
    if tier is None:
        raise ValueError(f"未知模型 tier: {tier_name!r}，可用: {list(cfg.tiers.keys())}")
    return tier.endpoint


@dataclass(frozen=True)
class AdmissionControlConfig:
    safety_factor: float = 0.75
    kv_utilization_threshold: float = 0.88
    kv_pressure_critical: float = 0.93


@dataclass(frozen=True)
class RbacConfig:
    roles: dict[str, int] = field(default_factory=dict)
    departments: dict[str, int] = field(default_factory=dict)
    super_admin_mask: int = 4294967295
    public_mask: int = 0


@dataclass(frozen=True)
class AuthConfig:
    dev_mode: bool = False
    jwt_secret: str = ""
    jwt_expiry_hours: int = 24


@dataclass(frozen=True)
class UiConfig:
    app_title: str = "Knowledge RAG Assistant"
    subtitle: str = "Retrieval-augmented knowledge assistant"
    anonymous_user_id: str = "web-user"
    default_role: str = ""
    role_options: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class AlertRule:
    name: str = ""
    metric: str = ""
    threshold: float = 0.0
    duration_s: int = 30
    severity: str = "warning"
    comparison: str = "gt"


@dataclass(frozen=True)
class AlertingConfig:
    rules: list[AlertRule] = field(default_factory=list)


@dataclass(frozen=True)
class CacheConfig:
    l1_max_entries: int = 1000
    l1_ttl_seconds: int = 300
    l2_ttl_seconds: int = 3600


@dataclass(frozen=True)
class AppConfig:
    """Top-level configuration dataclass, mirrors config.json structure."""

    system: SystemConfig = field(default_factory=SystemConfig)
    gpu0: GpuConfig = field(default_factory=GpuConfig)
    gpu1: GpuConfig = field(default_factory=GpuConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    qdrant: QdrantConfig = field(default_factory=QdrantConfig)
    elasticsearch: ElasticsearchConfig = field(default_factory=ElasticsearchConfig)
    redis: RedisConfig = field(default_factory=RedisConfig)
    knowledge_base: KnowledgeBaseConfig = field(default_factory=KnowledgeBaseConfig)
    query_rewrite: QueryRewriteConfig = field(default_factory=QueryRewriteConfig)
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
    generation: GenerationConfig = field(default_factory=GenerationConfig)
    model_routing: ModelRoutingConfig = field(default_factory=ModelRoutingConfig)
    admission_control: AdmissionControlConfig = field(default_factory=AdmissionControlConfig)
    rbac: RbacConfig = field(default_factory=RbacConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    ui: UiConfig = field(default_factory=UiConfig)
    alerting: AlertingConfig = field(default_factory=AlertingConfig)
    cache_config: CacheConfig = field(default_factory=CacheConfig)
    knowledge_version_epoch: str = "20260603_00"
    deployment_mode: str = "development"


# ---------------------------------------------------------------------------
# Deployment-mode helpers
# ---------------------------------------------------------------------------


def is_production_mode() -> bool:
    """Return True if the current deployment mode is 'production'."""
    return get_config().deployment_mode == "production"


def is_testing_mode() -> bool:
    """Return True if the current deployment mode is 'testing'."""
    return get_config().deployment_mode == "testing"


def is_development_mode() -> bool:
    """Return True if the current deployment mode is 'development'."""
    return get_config().deployment_mode == "development"


# ---------------------------------------------------------------------------
# Parser: raw JSON dict -> dataclass tree
# ---------------------------------------------------------------------------


def _unwrap_optional(tp) -> type:
    """Unwrap Optional[X] -> X (or the original type if not Optional)."""
    args = getattr(tp, "__args__", None)
    if args and type(None) in args:
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0]
    return tp


def _resolve_type_str(type_str: str):
    """Resolve a string type annotation like 'Optional[BertModelConfig]'."""
    import sys

    type_str = type_str.strip()

    # Handle Optional[X] string pattern
    if type_str.startswith("Optional[") and type_str.endswith("]"):
        inner = type_str[len("Optional[") : -1]
        inner_cls = _resolve_type_str(inner)
        return inner_cls | None if inner_cls is not None else type(None)

    # Bare class name
    cls = sys.modules[__name__].__dict__.get(type_str)
    return cls


def _parse_dict(cls, data: dict):
    """Best-effort recursive dict-to-dataclass conversion."""
    import dataclasses as dc

    if not dc.is_dataclass(cls):
        return data
    fieldtypes = {f.name: f.type for f in dc.fields(cls)}
    kwargs = {}
    for fname, ftype in fieldtypes.items():
        val = data.get(fname)
        if val is None:
            continue

        # 1. Resolve string type annotations -> actual type
        if isinstance(ftype, str):
            resolved = _resolve_type_str(ftype)
        else:
            resolved = ftype

        # 2. Unwrap Optional[X] -> X
        actual_cls = _unwrap_optional(resolved)

        if actual_cls is None:
            kwargs[fname] = val
        elif dc.is_dataclass(actual_cls) and isinstance(val, dict):
            kwargs[fname] = _parse_dict(actual_cls, val)
        else:
            kwargs[fname] = val
    return cls(**kwargs)


def _parse_config_dict(raw: dict) -> AppConfig:
    """Parse a raw JSON dict into an AppConfig dataclass."""
    raw = _apply_env_overrides(raw)

    # Handle nested models dict -> GpuModelsConfig
    for gpu_key in ("gpu0", "gpu1"):
        gpu_data = raw.get(gpu_key, {})
        models_raw = gpu_data.get("models", {})
        if models_raw:
            gpu_data["models"] = _parse_dict(GpuModelsConfig, models_raw)
        rba = gpu_data.get("rerank_batch_aggregator")
        if rba:
            gpu_data["rerank_batch_aggregator"] = _parse_dict(RerankBatchAggregatorConfig, rba)

    # Handle retrieval.parallel_paths
    retrieval_data = raw.get("retrieval", {})
    pp_raw = retrieval_data.get("parallel_paths", {})
    if isinstance(pp_raw, dict):
        parsed_pp = {}
        for k, v in pp_raw.items():
            parsed_pp[k] = _parse_dict(ParallelPathConfig, v) if isinstance(v, dict) else v
        retrieval_data["parallel_paths"] = parsed_pp

    # Handle rrf
    rrf_raw = retrieval_data.get("rrf", {})
    if isinstance(rrf_raw, dict):
        retrieval_data["rrf"] = _parse_dict(RrfConfig, rrf_raw)

    # Handle cross_encoder
    ce_raw = retrieval_data.get("cross_encoder", {})
    if isinstance(ce_raw, dict):
        retrieval_data["cross_encoder"] = _parse_dict(CrossEncoderRetrievalConfig, ce_raw)

    # Handle evidence_gate
    eg_raw = retrieval_data.get("evidence_gate", {})
    if isinstance(eg_raw, dict):
        eg_data = {}
        w = eg_raw.get("weights", {})
        if w:
            eg_data["weights"] = _parse_dict(EvidenceGateWeights, w)
        t = eg_raw.get("thresholds", {})
        if t:
            eg_data["thresholds"] = _parse_dict(EvidenceGateThresholds, t)
        retrieval_data["evidence_gate"] = _parse_dict(EvidenceGateConfig, eg_data)

    # Handle model_routing.tiers (dict[str, ModelTierConfig])
    mr_data = raw.get("model_routing", {})
    tiers_raw = mr_data.get("tiers", {})
    if isinstance(tiers_raw, dict):
        parsed_tiers = {}
        for k, v in tiers_raw.items():
            parsed_tiers[k] = _parse_dict(ModelTierConfig, v) if isinstance(v, dict) else v
        mr_data["tiers"] = parsed_tiers

    # Handle alerting rules
    alerting_data = raw.get("alerting", {})
    rules_raw = alerting_data.get("rules", [])
    if rules_raw:
        alerting_data["rules"] = [_parse_dict(AlertRule, r) if isinstance(r, dict) else r for r in rules_raw]

    return _parse_dict(AppConfig, raw)


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_config_lock = threading.Lock()
_config_instance: AppConfig | None = None


def get_config(reload: bool = False) -> AppConfig:
    """
    Return the singleton AppConfig instance.

    Thread-safe. Call with ``reload=True`` to force a re-read of config.json.
    """
    global _config_instance
    if _config_instance is not None and not reload:
        return _config_instance
    with _config_lock:
        if _config_instance is not None and not reload:
            return _config_instance
        path = _resolve_config_path()
        logger.info("Loading config from %s", path)
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        _config_instance = _parse_config_dict(raw)
        return _config_instance


def reload_config() -> AppConfig:
    """Convenience wrapper: force-reload and return.

    Clears both ``_config_instance`` and ``_config_dict_instance``
    so that subsequent calls to ``get_config()`` and ``get_config_dict()``
    both see the fresh config.
    """
    global _config_dict_instance
    _config_dict_instance = None
    return get_config(reload=True)


# M-6: 兼容性函数 — 返回原始 dict，供需要 dict 访问模式的模块使用
_config_dict_instance: dict | None = None


def get_config_dict(reload: bool = False) -> dict:
    """返回 config.json 的原始 dict（用于兼容 `config["key"]` 访问模式）。"""
    global _config_dict_instance
    if _config_dict_instance is not None and not reload:
        return _config_dict_instance
    with _config_lock:
        if _config_dict_instance is not None and not reload:
            return _config_dict_instance
        path = _resolve_config_path()
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        _config_dict_instance = _apply_env_overrides(raw)
        return _config_dict_instance
