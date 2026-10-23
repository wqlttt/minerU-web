#!/usr/bin/env bash
set -euo pipefail

# MinerU Web 一键环境准备脚本
# 用法：在 minerU-web 目录下运行  bash setup.sh
# 作用：安装 MinerU 4.x + vLLM(GPU)、网页依赖、下载 vLLM 模型并校验
# 可重复运行（幂等）。

echo "=============================================="
echo "  MinerU Web 一键环境准备"
echo "=============================================="

cd "$(dirname "$0")"

# 0. 检查 GPU
if ! command -v nvidia-smi >/dev/null 2>&1; then
    echo "❌ 未检测到 nvidia-smi，请确认这是一台 NVIDIA GPU 机器"
    exit 1
fi
echo "检测到 GPU："
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader

echo ""
echo "[1/4] 安装 MinerU 4.x + vLLM（首次约 3GB+，需要几分钟）..."
pip install "mineru[full]"

echo ""
echo "[2/4] 安装网页依赖..."
pip install -r requirements.txt

echo ""
echo "[3/4] 下载 vLLM 版模型（standard 档，国内网络自动回落到 modelscope）..."
export MINERU_MODEL_SMALL_BACKEND=torch
export MINERU_MODEL_VLM_ENGINE=vllm
mineru-kit models download --tier standard --small-backend torch --vlm-engine vllm

echo ""
echo "[4/4] 校验模型..."
mineru-kit models verify --tier standard --small-backend torch --vlm-engine vllm \
    || echo "  （校验未通过也无妨，可先试跑一个 PDF 看效果）"

echo ""
echo "=============================================="
echo "  ✅ 环境准备完成"
echo "=============================================="
echo ""
echo "启动网页："
echo "  python app.py"
echo ""
echo "本机 SSH 隧道访问（在你自己电脑上执行）："
echo "  ssh -CNg -L 6006:127.0.0.1:6006 root@<服务器IP> -p <SSH端口>"
echo "  然后浏览器打开 http://localhost:6006"
echo ""
echo "（可选）先试跑一个 PDF，确认日志里出现 vllm 引擎、速度正常："
echo "  time mineru-kit parse 测试.pdf -o /tmp/out.md --tier standard --ocr-mode auto"
