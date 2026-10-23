#!/usr/bin/env bash
set -euo pipefail

# 启动 MinerU 常驻 API 服务（模型只加载一次，之后每次解析不再付初始化成本）
# 用法：bash start-api.sh [tier] [port]
cd "$(dirname "$0")"
TIER="${1:-standard}"
PORT="${2:-8000}"

echo "启动 mineru-kit api-server（tier=${TIER}, port=${PORT}）..."
nohup mineru-kit api-server --host 127.0.0.1 --port "${PORT}" --tier "${TIER}" > api-server.log 2>&1 &

sleep 3
echo "已后台启动，日志见 api-server.log"
if curl -sf "http://127.0.0.1:${PORT}/v1/health" > /dev/null 2>&1; then
    echo "✅ api-server 健康检查通过"
else
    echo "⚠️ 健康检查未通过，请看 api-server.log（首次启动可能还在加载模型）"
fi

echo ""
echo "接下来把 config.json 改成："
echo "  \"parse_mode\": \"api\""
echo "  \"api_server_url\": \"http://127.0.0.1:${PORT}\""
echo "然后重启网页：python app.py"
