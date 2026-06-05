"""
服务启动脚本

启动 vLLM 实例和 GPU Batch Service（实际部署时使用）

用法：
    python run_services.py --service all
    python run_services.py --service rewrite
    python run_services.py --service gen
    python run_services.py --service rerank
"""

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import time
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

# 全局进程管理
_processes: dict[str, subprocess.Popen] = {}


def signal_handler(signum, frame):
    """Ctrl+C / SIGTERM 优雅关闭所有子进程"""
    logger.info("收到退出信号，关闭所有服务...")
    for name, proc in _processes.items():
        logger.info(f"停止 {name} (pid={proc.pid})")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    sys.exit(0)


def start_vllm_service(
    name: str,
    port: int,
    model_path: str,
    max_model_len: int = 4096,
    gpu_memory_utilization: float = 0.85,
    tensor_parallel_size: int = 1,
) -> subprocess.Popen:
    """
    启动 vLLM 服务

    Args:
        name: 服务名称
        port: 监听端口
        model_path: 模型路径
        max_model_len: 最大模型长度
        gpu_memory_utilization: GPU 显存利用率
        tensor_parallel_size: 张量并行数（多卡时 > 1）

    Returns:
        subprocess.Popen 对象
    """
    if not os.path.exists(model_path):
        logger.warning(f"模型路径不存在: {model_path}，跳过启动")
        return None

    cmd = [
        sys.executable, "-m", "vllm.entrypoints.openai.api_server",
        "--model", model_path,
        "--port", str(port),
        "--max-model-len", str(max_model_len),
        "--gpu-memory-utilization", str(gpu_memory_utilization),
        "--trust-remote-code",
    ]

    if tensor_parallel_size > 1:
        cmd.extend(["--tensor-parallel-size", str(tensor_parallel_size)])

    # 分离模式：子进程不继承父进程 stdin/stdout
    env = os.environ.copy()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=True,
    )

    _processes[name] = proc
    logger.info(f"vLLM 服务已启动: {name} → port {port} (pid={proc.pid})")
    logger.info(f"  命令: {' '.join(cmd)}")

    # 等待服务就绪（最多 30s）
    _wait_for_service(name, port, timeout=30)

    return proc


def _wait_for_service(name: str, port: int, timeout: int = 30):
    """等待服务就绪"""
    import requests

    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = requests.get(f"http://localhost:{port}/health", timeout=2)
            if resp.status_code == 200:
                elapsed = time.time() - start
                logger.info(f"  {name} 就绪 ({elapsed:.1f}s)")
                return
        except requests.RequestException:
            pass
        time.sleep(2)

    logger.warning(f"  {name} 等待就绪超时 ({timeout}s)，继续...")


def start_rerank_service(port: int = 8103) -> subprocess.Popen:
    """
    启动 GPU Batch Rerank Service

    这是一个 FastAPI HTTP 服务，接收 (query, doc) pairs，
    调用 CrossEncoder/NLI/BiEncoder 进行 GPU 批处理推理。

    端点：
    - POST /rerank/batch - 批量 Rerank
    - POST /rerank/nli - 批量 NLI
    - GET  /health - 健康检查

    Args:
        port: 监听端口

    Returns:
        subprocess.Popen 对象
    """
    rerank_service_code = f'''
import argparse
import logging
import time
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("rerank-service")

app = FastAPI(title="Rerank Batch Service")

class RerankRequest(BaseModel):
    pairs: list[list[str]]  # [[query, doc], ...]

class NLIRequest(BaseModel):
    pairs: list[list[str]]  # [[premise, hypothesis], ...]

_aggregator = None
_nli_tokenizer = None
_nli_model = None

@app.on_event("startup")
def startup():
    global _aggregator, _nli_tokenizer, _nli_model
    from retrieval.rerank_batch_aggregator import RerankBatchAggregator
    _aggregator = RerankBatchAggregator()
    logger.info("Rerank Batch Aggregator 初始化完成")

    # NLI 模型：尝试加载，失败则 _nli_model 保持 None，/rerank/nli 返回 501
    try:
        import os as _os
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        _nli_model_path = "./models/nli-deberta"
        if _os.path.exists(_nli_model_path):
            _nli_tokenizer = AutoTokenizer.from_pretrained(_nli_model_path)
            _nli_model = AutoModelForSequenceClassification.from_pretrained(_nli_model_path)
            _nli_model.eval()
            logger.info(f"NLI 模型加载完成: {_nli_model_path}")
        else:
            logger.warning(f"NLI 模型路径不存在: {_nli_model_path}，/rerank/nli 将返回 501")
    except ImportError:
        logger.warning("transformers 未安装，/rerank/nli 将返回 501")
    except Exception as e:
        logger.warning(f"NLI 模型加载失败: {e}，/rerank/nli 将返回 501")

@app.post("/rerank/batch")
def rerank_batch(req: RerankRequest):
    if _aggregator is None:
        raise HTTPException(503, "Aggregator not initialized")
    try:
        from sentence_transformers import CrossEncoder
        # 使用默认 CrossEncoder（实际部署时按配置加载）
        model = CrossEncoder("cross-encoder-ms-marco-MiniLM-L-6")
        pairs = [tuple(p) for p in req.pairs]
        scores = _aggregator.batch_predict(model, pairs)
        return {{"scores": scores, "count": len(scores)}}
    except Exception as e:
        logger.error(f"Rerank batch failed: {{e}}")
        raise HTTPException(500, str(e))

@app.post("/rerank/nli")
def nli_batch(req: NLIRequest):
    if _aggregator is None:
        raise HTTPException(503, "Aggregator not initialized")

    if _nli_model is None:
        raise HTTPException(
            status_code=501,
            detail=(
                "NLI model not available.  Deploy a transformers-compatible NLI model "
                "at ./models/nli-deberta and ensure the 'transformers' package is installed.  "
                "Contract: POST /rerank/nli  body={{pairs:[[premise,hypothesis],...]}}  "
                "response={{results:[{{contradiction:float,entailment:float,neutral:float}},...],count:int}}"
            ),
        )

    if not req.pairs:
        return {{"results": [], "count": 0}}

    try:
        import torch
        premises = [pair[0] for pair in req.pairs]
        hypotheses = [pair[1] for pair in req.pairs]

        inputs = _nli_tokenizer(
            premises, hypotheses,
            return_tensors="pt", truncation=True, max_length=512, padding=True,
        )

        with torch.no_grad():
            outputs = _nli_model(**inputs)
            probs = torch.softmax(outputs.logits, dim=-1)

        # DeBERTa-NLI label order: 0=contradiction, 1=entailment, 2=neutral
        results = []
        for i in range(len(req.pairs)):
            results.append({{
                "contradiction": round(probs[i][0].item(), 6),
                "entailment":    round(probs[i][1].item(), 6),
                "neutral":       round(probs[i][2].item(), 6),
            }})
        return {{"results": results, "count": len(results)}}
    except Exception as e:
        logger.error(f"NLI batch failed: {{e}}")
        raise HTTPException(500, str(e))

@app.get("/health")
def health():
    return {{"status": "healthy", "service": "rerank-batch"}}

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default={port})
    args = parser.parse_args()
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="info")
'''

    service_file = "/tmp/rerank_service_temp.py"
    with open(service_file, "w") as f:
        f.write(rerank_service_code)

    proc = subprocess.Popen(
        [sys.executable, service_file, "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    _processes["rerank-batch"] = proc
    logger.info(f"Rerank Batch Service 已启动 → port {port} (pid={proc.pid})")
    _wait_for_service("rerank-batch", port, timeout=15)

    return proc


def main():
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    parser = argparse.ArgumentParser(description="服务启动器")
    parser.add_argument(
        "--service",
        choices=["all", "rewrite", "gen", "rerank"],
        default="all",
        help="启动的服务"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅打印启动命令，不实际执行"
    )
    args = parser.parse_args()

    with open("config.json", encoding="utf-8", errors="replace") as f:
        content = f.read().replace('\\"', '"')
    config = json.loads(content)

    launched = []

    if args.service in ("all", "rewrite"):
        rw = config["gpu1"]["models"]["vllm_rewrite"]
        if args.dry_run:
            logger.info(f"[DRY RUN] vllm-rewrite: port={rw['port']}")
        else:
            start_vllm_service("vllm-rewrite", rw["port"], rw["model_path"], rw["max_model_len"])
            launched.append("vllm-rewrite")

    if args.service in ("all", "gen"):
        gen_4b = config["gpu1"]["models"]["vllm_gen_4b"]
        if args.dry_run:
            logger.info(f"[DRY RUN] vllm-gen-4b: port={gen_4b['port']}")
        else:
            start_vllm_service("vllm-gen-4b", gen_4b["port"], gen_4b["model_path"], gen_4b["max_model_len"])
            launched.append("vllm-gen-4b")

        gen_14b = config["gpu0"]["models"]["gen_14b"]
        if args.dry_run:
            logger.info(f"[DRY RUN] vllm-gen-14b: port={gen_14b['port']}")
        else:
            gpu_mem = gen_14b.get("gpu_memory_utilization", 0.85)
            start_vllm_service(
                "vllm-gen-14b",
                gen_14b["port"],
                gen_14b["model_path"],
                gen_14b["max_model_len"],
                gpu_memory_utilization=gpu_mem,
            )
            launched.append("vllm-gen-14b")

    if args.service in ("all", "rerank"):
        rerank_port = config["gpu1"]["models"].get("rerank_service", {}).get("port", 8103)
        if args.dry_run:
            logger.info(f"[DRY RUN] rerank-batch: port={rerank_port}")
        else:
            start_rerank_service(port=rerank_port)
            launched.append("rerank-batch")

    if launched:
        logger.info(f"服务启动完成: {', '.join(launched)}")
        logger.info("按 Ctrl+C 关闭所有服务")

        # 保持主进程运行
        try:
            while True:
                time.sleep(1)
                # 检查子进程状态
                dead = [name for name, p in _processes.items() if p.poll() is not None]
                for name in dead:
                    logger.error(f"服务异常退出: {name}")
                    del _processes[name]
                if not _processes:
                    break
        except KeyboardInterrupt:
            pass
    else:
        logger.info("无服务启动（dry-run 或所有模型路径不存在）")


if __name__ == "__main__":
    main()
