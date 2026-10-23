# MinerU Web 控制台

一个傻瓜式的 MinerU 网页操作面板：**上传 ZIP → 自动解压 → 选中文件夹 → 一键调用本机 MinerU 解析 → 实时进度条 → 下载结果**。

> 注意：本项目**不含 MinerU 本体**。请把它和 MinerU 部署在**同一台**云 GPU 服务器上（网页后端直接调用服务器本地的 `mineru` 命令，数据不出本机）。

## 功能

- 拖拽上传 `.zip`，服务端自动解压成一个文件夹
- 文件夹列表（含文件数），点选后一键解析
- 支持选择解析后端：`pipeline` / `vlm-transformers` / `vlm-sglang`
- 实时进度条：按「已完成文件数 / 总文件数」计算，附带剩余时间估算
- 实时滚动 MinerU 运行日志
- 结果一键打包下载 ZIP（结果同时保存在服务器 `data/output/<任务ID>/`）
- 任务状态持久化，服务重启后可恢复记录（运行中的任务会标记为「已中断」）

## 部署步骤（Linux + NVIDIA GPU）

> **一键方式**：克隆仓库后直接 `bash setup.sh`，会自动完成下面 1～4 步（装 vLLM、装网页依赖、下模型、校验）。

1. **安装 MinerU 4.x + GPU 推理引擎 vLLM**（NVIDIA GPU 必须装 `full` 才能用 vLLM）：

   ```bash
   pip install "mineru[full]"
   # 下载 vLLM 版模型（standard 档需 8G+ 显存；国内网络会自动回落到 modelscope）
   mineru-kit models download --tier standard --small-backend torch --vlm-engine vllm
   ```

   也可参考官方文档 https://github.com/opendatalab/MinerU

2. **克隆本项目**：

   ```bash
   git clone <你的仓库地址>
   cd mineru-web
   ```

3. **安装网页依赖**：

   ```bash
   pip install -r requirements.txt
   ```

4. **启动**：

   ```bash
   python app.py
   # 或者
   chmod +x run.sh && ./run.sh
   ```

5. 服务默认监听 `127.0.0.1:6006`（不对外暴露）。在本机用 SSH 隧道访问：

   ```bash
   ssh -CNg -L 6006:127.0.0.1:6006 root@<服务器IP> -p <SSH端口>
   ```

   然后浏览器打开 `http://localhost:6006`。

## 配置

编辑 `config.json`：

| 字段 | 说明 |
|------|------|
| `command_template` | 实际执行的 MinerU 命令，占位符 `{input}` `{output}` `{tier}` `{ocr_mode}` 自动替换 |
| `tiers` / `default_tier` | 网页下拉框里的质量档选项 / 默认质量档（flash/basic/standard/advanced） |
| `ocr_mode` | OCR 模式：`auto`（自动判断，默认）/ `txt`（只提文字）/ `ocr`（强制 OCR） |
| `data_dir` | 数据目录（上传/解压/输出）。根目录小的机器可改成大磁盘路径，如 `/root/autodl-tmp/mineru-data` |
| `supported_extensions` | 哪些扩展名会被当作可解析文件 |
| `extra_args` | 追加到命令末尾的额外参数，例如 `["--disable-image-analysis"]` |
| `auto_cleanup_inputs_after_parse` | 解析完成后自动删除原始 PDF 和临时文件（默认 `true`） |
| `auto_delete_output_after_download` | 下载完成后是否自动删除解析结果（默认 `false`，改为手动点「删除结果」） |
| `host` / `port` | 监听地址和端口（默认 `127.0.0.1` / `6006`，请勿改成 `0.0.0.0` 暴露公网） |

如果 MinerU 版本较新/命令不同，改 `command_template` 即可，无需改代码。

## 可选：API 常驻模式（省掉每次 3 分钟初始化）

默认 CLI 模式（每次解析启动一次 `mineru-kit parse`，付一次模型初始化）。若想模型常驻、彻底不付初始化成本：

```bash
bash start-api.sh standard 8000      # 后台启动常驻 api-server（模型加载一次，日志在 api-server.log）
# 然后把 config.json 改为：
#   "parse_mode": "api"
#   "api_server_url": "http://127.0.0.1:8000"
python app.py                        # 重启网页
```

启用后，网页会调用 `POST /v1/file_parse` 逐个文件解析，模型不再反复加载。

## 目录结构

```
mineru-web/
├── app.py            # FastAPI 后端
├── config.json       # 配置
├── requirements.txt
├── run.sh
├── static/           # 前端（纯 HTML/JS/CSS，无构建）
└── data/             # 运行时生成（已被 .gitignore 忽略）
    ├── uploads/      # 上传的原始 ZIP
    ├── extracted/    # 解压后的文件夹
    ├── staging/      # 拍平后的待解析文件（硬链接）
    └── output/       # MinerU 输出结果
```

## 安全提示

本项目**没有登录鉴权**。默认监听 `127.0.0.1`（仅本机可访问），请通过 **SSH 隧道**访问，不要改成 `0.0.0.0` 暴露到公网。

如需多人访问，可自行加 Nginx/Caddy 反代 + Basic Auth。
