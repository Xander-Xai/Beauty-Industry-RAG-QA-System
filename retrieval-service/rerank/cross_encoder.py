"""CrossEncoder Ensemble 重排 — 转发到唯一实现。

本模块曾携带 ``retrieval/cross_encoder_ensemble.py`` 的第二份拷贝，且已经漂移：
服务拷贝没有 Platt Scaling 校准、没有 tokenizer 级截断、也不记录
``ctx.ce_stage_provenance`` 运行时归因，因此已部署的 ``retrieval-service``
实际运行的是一个更弱、且无法区分“真实精排/确定性降级”的版本。

现在两份实现在 ``retrieval/cross_encoder_ensemble.py`` 合一。该文件是完整实现
（含 ``PlattScaler``、token 截断与 provenance 记录），本模块只做转发以保留
``retrieval-service/main.py`` 使用的导入路径。
"""

from retrieval.cross_encoder_ensemble import (  # noqa: F401  (re-exported for the service entry point)
    CrossEncoderEnsemble,
    PlattScaler,
)

__all__ = ["CrossEncoderEnsemble", "PlattScaler"]
