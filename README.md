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

1. **安装 MinerU**（按你 GPU 环境装好，确保命令行能直接跑 `mineru -h`）：

   ```bash
   pip install mineru
   # 或参考官方文档 https://github.com/opendatalab/MinerU
   ```

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

5. 浏览器打开 `http://<服务器IP>:8000`（记得在云厂商安全组放行 8000 端口）。

## 配置

编辑 `config.json`：

| 字段 | 说明 |
|------|------|
| `command_template` | 实际执行的 MinerU 命令，占位符 `{input}` `{output}` `{backend}` 自动替换 |
| `backends` / `default_backend` | 网页下拉框里的后端选项 / 默认后端 |
| `supported_extensions` | 哪些扩展名会被当作可解析文件 |
| `done_markers` | 判断单文件解析完成的标志文件（一般不用改） |
| `extra_args` | 追加到 mineru 命令的额外参数，例如 `["-d", "cuda:0"]` |
| `auto_cleanup_inputs_after_parse` | 解析完成后自动删除原始 PDF 和临时文件（默认 `true`） |
| `auto_delete_output_after_download` | 下载完成后是否自动删除解析结果（默认 `false`，改为手动点「删除结果」） |

如果 MinerU 版本较新/命令不同，改 `command_template` 即可，无需改代码。

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

本项目**没有登录鉴权**，任何能访问该端口的人都能上传文件、执行 MinerU、下载结果。请务必：

- 只在受信网络使用，或用防火墙/安全组限制来源 IP；
- 或用反向代理（Nginx/Caddy）加一层 Basic Auth；
- 不要把 8000 端口直接暴露到公网。
