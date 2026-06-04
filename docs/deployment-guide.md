# 化妆品 RAG 系统部署手册

## 1. 环境要求

### 硬件

| 组件 | 最低配置 | 推荐配置 |
|------|----------|----------|
| GPU | 单卡 (16GB+) | 双卡 (GPU0: 14B, GPU1: 4B+Rerank) |
| 内存 | 32GB | 64GB |
| 磁盘 | 100GB SSD | 500GB NVMe |
| 网络 | 100Mbps | 1Gbps |

### 软件

- Docker >= 24.0
- Docker Compose >= 2.20
- NVIDIA Container Toolkit (GPU 模式)
- nvidia-driver >= 535

## 2. 快速部署

```bash
# 1. 克隆代码
git clone <repo-url>
cd Beauty-Industry-RAG-QA-System

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 设置 DEPLOYMENT_MODE 和模型路径

# 3. 一键启动
DEPLOYMENT_MODE=testing ./scripts/start.sh
```

## 3. 模型下载

```bash
# 从 HuggingFace 下载
mkdir -p models
huggingface-cli download Qwen/Qwen3-14B-Instruct --local-dir models/qwen3-14b
huggingface-cli download Qwen/Qwen3-4B-Instruct --local-dir models/qwen3-4b
huggingface-cli download BAAI/bge-base-zh-v1.5 --local-dir models/bge-base-zh-v1.5

# 或使用 ModelScope (国内加速)
modelscope download --model Qwen/Qwen3-14B-Instruct --local_dir models/qwen3-14b
```

## 4. 知识库初始化

```bash
# 生成 Mock 数据
python3 -m data.mock_generator data/mock_data

# 导入向量库
python3 -m offline.scheduler --mode incremental
```

## 5. 部署模式

| 模式 | 命令 | GPU 要求 | 说明 |
|------|------|----------|------|
| production | `DEPLOYMENT_MODE=production ./scripts/start.sh` | 2x GPU | 双卡，14B+4B |
| testing | `DEPLOYMENT_MODE=testing ./scripts/start.sh` | 1x GPU | 单卡，4B 复用 |
| development | `./scripts/start.sh` | 无 | CPU 模拟 |

## 6. HTTPS 配置

```bash
# 生成自签名证书 (测试环境)
mkdir -p nginx/ssl
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout nginx/ssl/key.pem -out nginx/ssl/cert.pem \
    -subj "/CN=localhost"
```

## 7. 常见问题

### GPU 不可用
检查 `nvidia-smi` 输出。若 Docker 无法访问 GPU，确认 nvidia-container-toolkit 已安装。

### Milvus 启动失败
检查 etcd 是否正常运行。Milvus 依赖 etcd 进行元数据存储。

### Redis 连接超时
确认 Redis 容器已启动且端口 6379 未被占用。

### vLLM 启动慢
首次加载模型需要时间（30s-2min）。健康检查会在启动后自动等待。
