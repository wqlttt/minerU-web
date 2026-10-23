"""MinerU Web 控制台

一个傻瓜式的网页后端：上传 ZIP → 自动解压 → 选中文件夹 → 调用本机 MinerU 解析 → 实时进度条。

针对 MinerU 4.x：使用 `mineru-kit parse`，整目录一次跑完（模型只加载一次），
进度按输出目录里的 markdown 文件数统计。
"""

import asyncio
import json
import os
import shlex
import shutil
import subprocess
import threading
import time
import uuid
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.background import BackgroundTask

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
CONFIG_PATH = BASE_DIR / "config.json"

DEFAULT_CONFIG = {
    # MinerU 4.x 批量接口：整目录一次跑完；{input} {output} {tier} {ocr_mode} 自动替换
    "command_template": "mineru-kit parse {input} -o {output} --tier {tier} --ocr-mode {ocr_mode}",
    "tiers": ["flash", "basic", "standard", "advanced"],
    "default_tier": "standard",
    "ocr_mode": "auto",
    # 传给 mineru-kit 子进程的环境变量（NVIDIA GPU 走 vLLM）
    "env": {
        "MINERU_MODEL_SMALL_BACKEND": "torch",
        "MINERU_MODEL_VLM_ENGINE": "vllm",
    },
    # 数据目录（上传/解压/输出都在这里）。根目录小的机器可改成大磁盘路径，例如 "/root/autodl-tmp/mineru-data"
    "data_dir": "data",
    "supported_extensions": [
        ".pdf", ".doc", ".docx", ".ppt", ".pptx",
        ".xls", ".xlsx", ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff",
    ],
    "extra_args": [],
    "auto_cleanup_inputs_after_parse": True,
    "auto_delete_output_after_download": False,
    "max_log_lines": 500,
    "host": "127.0.0.1",
    "port": 6006,
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception as e:
            print(f"[warn] 读取 config.json 失败，使用默认配置：{e}")
    return cfg


config = load_config()

_data_dir = Path(config.get("data_dir", "data")).expanduser()
if not _data_dir.is_absolute():
    _data_dir = BASE_DIR / _data_dir
DATA_DIR = _data_dir.resolve()
UPLOAD_DIR = DATA_DIR / "uploads"
EXTRACT_DIR = DATA_DIR / "extracted"
STAGING_DIR = DATA_DIR / "staging"
OUTPUT_DIR = DATA_DIR / "output"
JOBS_FILE = DATA_DIR / "jobs.json"

SUPPORTED = {e.lower() for e in config["supported_extensions"]}
MAX_LOG = int(config["max_log_lines"])

# ---- 全局状态 ----
jobs = {}
subscribers = {}
main_loop = None


def ensure_dirs():
    for d in (DATA_DIR, UPLOAD_DIR, EXTRACT_DIR, STAGING_DIR, OUTPUT_DIR, STATIC_DIR):
        d.mkdir(parents=True, exist_ok=True)


def supported_files(root: Path):
    return [p for p in sorted(root.rglob("*")) if p.is_file() and p.suffix.lower() in SUPPORTED]


def snapshot(job):
    return {
        "id": job["id"],
        "name": job["name"],
        "tier": job["tier"],
        "ocr_mode": job.get("ocr_mode", "auto"),
        "status": job["status"],
        "total_files": job["total_files"],
        "done_files": job["done_files"],
        "progress_percent": job["progress_percent"],
        "inputs_cleaned": job.get("inputs_cleaned", False),
        "error": job.get("error"),
        "log": job["log"][-30:],
        "created_at": job["created_at"],
        "finished_at": job.get("finished_at"),
    }


def append_log(job, line):
    job["log"].append(line)
    if len(job["log"]) > MAX_LOG:
        job["log"] = job["log"][-MAX_LOG:]


def push_event(job_id, data):
    loop = main_loop
    if loop is None or loop.is_closed():
        return
    payload = json.dumps(data, ensure_ascii=False)
    for q in list(subscribers.get(job_id, ())):
        try:
            loop.call_soon_threadsafe(q.put_nowait, payload)
        except RuntimeError:
            pass


def flatten_input(root: Path, staging: Path):
    """把 root 下所有可解析文件（含嵌套目录）拍平到 staging，用硬链接避免重复占盘。"""
    counts = {}
    staged = []
    for p in supported_files(root):
        n = counts.get(p.name, 0)
        counts[p.name] = n + 1
        new_name = p.name if n == 0 else f"{p.stem}_{n}{p.suffix}"
        dest = staging / new_name
        try:
            os.link(p, dest)
        except OSError:
            shutil.copy2(p, dest)
        staged.append(dest)
    return staged


def build_command(input_path, output_path, tier, ocr_mode):
    template = config["command_template"]
    s = template.format(input=str(input_path), output=str(output_path), tier=tier, ocr_mode=ocr_mode)
    cmd = shlex.split(s)
    extra = config.get("extra_args") or []
    if extra:
        cmd = cmd + [str(x) for x in extra]  # 追加到命令末尾，作为 parse 的额外选项
    return cmd


def cleanup_inputs(job):
    """解析完成后，删除原始 PDF、上传的 zip、拍平临时目录，只保留解析结果。"""
    if not config.get("auto_cleanup_inputs_after_parse", True):
        return
    staging = STAGING_DIR / job["id"]
    shutil.rmtree(staging, ignore_errors=True)
    inp = Path(job["input_dir"])
    if str(inp.resolve()).startswith(str(EXTRACT_DIR.resolve())):
        shutil.rmtree(inp, ignore_errors=True)
    job["inputs_cleaned"] = True


def cleanup_output(job_id):
    """删除解析结果目录和打包 zip，并把任务标记为已清理。"""
    j = jobs.get(job_id)
    if not j:
        return
    out = Path(j["output_dir"])
    zip_path = out.parent / f"{job_id}_result.zip"
    shutil.rmtree(out, ignore_errors=True)
    zip_path.unlink(missing_ok=True)
    j["status"] = "cleaned"
    j["error"] = None
    append_log(j, "🗑 结果已删除")
    save_jobs()
    push_event(job_id, snapshot(j))


# ---- 任务持久化 ----

def save_jobs():
    try:
        data = {jid: {k: v for k, v in j.items() if k != "process"} for jid, j in jobs.items()}
        JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
        JOBS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[warn] 保存任务失败：{e}")


def load_jobs():
    if not JOBS_FILE.exists():
        return
    try:
        data = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
        for jid, j in data.items():
            j["process"] = None
            j["cancel_requested"] = False
            if j.get("status") in ("running", "queued"):
                j["status"] = "interrupted"
                j["error"] = "服务重启导致任务中断（已生成的输出文件仍在磁盘上）"
            jobs[jid] = j
    except Exception as e:
        print(f"[warn] 加载任务失败：{e}")


# ---- 后台解析 ----

def count_markdown(out: Path) -> int:
    if not out.exists():
        return 0
    return sum(1 for p in out.rglob("*.md") if p.is_file())


def run_job(job_id):
    job = jobs.get(job_id)
    if job is None:
        return
    try:
        job["status"] = "running"
        append_log(job, f"▶ 开始解析：{job['name']}（质量档 {job['tier']}，OCR {job['ocr_mode']}）")
        push_event(job_id, snapshot(job))

        staging = STAGING_DIR / job_id
        staging.mkdir(parents=True, exist_ok=True)
        files = flatten_input(Path(job["input_dir"]), staging)
        if not files:
            job["status"] = "failed"
            job["error"] = "文件夹里没有可解析的文件"
            append_log(job, "❌ 没有可解析的文件")
            return

        total = len(files)
        job["total_files"] = total
        out = Path(job["output_dir"])
        out.mkdir(parents=True, exist_ok=True)

        cmd = build_command(staging, out, job["tier"], job["ocr_mode"])
        append_log(job, "🔧 " + " ".join(cmd))
        append_log(job, f"共 {total} 个文件，整目录一次解析（模型只加载一次）")

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(BASE_DIR),
            env={**os.environ, **(config.get("env") or {})},
        )
        job["process"] = proc

        def reader():
            try:
                for line in proc.stdout:
                    append_log(job, line.rstrip("\n")[-500:])
            except Exception:
                pass

        threading.Thread(target=reader, daemon=True).start()

        while proc.poll() is None:
            if job.get("cancel_requested"):
                proc.terminate()
            md = count_markdown(out)
            job["done_files"] = min(md, total)
            job["progress_percent"] = round(job["done_files"] * 100.0 / total, 1)
            push_event(job_id, snapshot(job))
            time.sleep(1.0)

        rc = proc.wait()
        md = count_markdown(out)
        job["done_files"] = min(md, total)
        job["progress_percent"] = round(job["done_files"] * 100.0 / total, 1)
        append_log(job, f"--- 进程结束 (exit {rc})，已生成 {md} 个 markdown ---")

        if job.get("cancel_requested"):
            job["status"] = "cancelled"
            append_log(job, "任务已取消")
        elif rc == 0:
            job["status"] = "done"
            job["done_files"] = total
            job["progress_percent"] = 100.0
            append_log(job, "✅ 解析完成")
            cleanup_inputs(job)
            append_log(job, "🧹 原始 PDF 与临时文件已清理，仅保留解析结果")
        else:
            job["status"] = "failed"
            job["error"] = f"mineru-kit 退出码 {rc}"
            append_log(job, f"❌ 解析失败（退出码 {rc}），请查看上方日志")
    except Exception as e:
        job["status"] = "failed"
        job["error"] = str(e)
        append_log(job, f"❌ 异常：{e}")
    finally:
        job["process"] = None
        job["finished_at"] = time.time()
        save_jobs()
        push_event(job_id, snapshot(job))


# ---- FastAPI 应用 ----

ensure_dirs()
load_jobs()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global main_loop
    main_loop = asyncio.get_running_loop()
    yield


app = FastAPI(title="MinerU Web 控制台", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class ParseRequest(BaseModel):
    folder: str
    tier: str


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/config")
def get_config():
    return {"tiers": config["tiers"], "default_tier": config["default_tier"], "ocr_mode": config.get("ocr_mode", "auto")}


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(400, "只支持 .zip 文件")

    zip_id = uuid.uuid4().hex[:8]
    base = Path(file.filename).stem.strip() or "upload"
    folder = EXTRACT_DIR / f"{base}_{zip_id}"
    folder.mkdir(parents=True, exist_ok=True)

    zip_path = UPLOAD_DIR / f"{zip_id}.zip"
    with open(zip_path, "wb") as f:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)

    root = folder.resolve()
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for member in zf.infolist():
                target = (folder / member.filename).resolve()
                if not str(target).startswith(str(root) + os.sep) and target != root:
                    raise HTTPException(400, f"ZIP 内含非法路径：{member.filename}")
            zf.extractall(folder)
    except HTTPException:
        raise
    except zipfile.BadZipFile:
        raise HTTPException(400, "文件不是有效的 ZIP 压缩包")

    zip_path.unlink(missing_ok=True)  # 解压完即删 zip，避免堆积
    count = len(supported_files(folder))
    return {"name": folder.name, "path": str(folder), "files": count}


@app.get("/api/folders")
def list_folders():
    items = []
    if EXTRACT_DIR.exists():
        for d in sorted(EXTRACT_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if d.is_dir():
                items.append({"name": d.name, "path": str(d), "files": len(supported_files(d))})
    return {"folders": items}


@app.post("/api/folders/delete")
def delete_folder(body: dict):
    name = str(body.get("name", ""))
    if not name or Path(name).name != name:
        raise HTTPException(400, "非法的文件夹名")
    p = EXTRACT_DIR / name
    if not p.is_dir():
        raise HTTPException(404, "文件夹不存在")
    shutil.rmtree(p, ignore_errors=True)
    return {"ok": True}


@app.post("/api/parse")
def start_parse(req: ParseRequest):
    name = req.folder
    if not name or Path(name).name != name:
        raise HTTPException(400, "非法的文件夹名")
    folder = EXTRACT_DIR / name
    if not folder.is_dir():
        raise HTTPException(404, "文件夹不存在")
    if req.tier not in config["tiers"]:
        raise HTTPException(400, f"未知质量档：{req.tier}")
    files = supported_files(folder)
    if not files:
        raise HTTPException(400, "该文件夹里没有可解析的文件")

    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "name": name,
        "input_dir": str(folder),
        "output_dir": str(OUTPUT_DIR / job_id),
        "tier": req.tier,
        "ocr_mode": config.get("ocr_mode", "auto"),
        "status": "queued",
        "total_files": len(files),
        "done_files": 0,
        "progress_percent": 0.0,
        "inputs_cleaned": False,
        "cancel_requested": False,
        "error": None,
        "log": [],
        "created_at": time.time(),
        "finished_at": None,
        "process": None,
    }
    jobs[job_id] = job
    save_jobs()
    threading.Thread(target=run_job, args=(job_id,), daemon=True).start()
    return {"job_id": job_id, "job": snapshot(job)}


@app.get("/api/jobs")
def list_jobs():
    return {"jobs": [snapshot(j) for j in sorted(jobs.values(), key=lambda x: x["created_at"], reverse=True)]}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    j = jobs.get(job_id)
    if not j:
        raise HTTPException(404, "任务不存在")
    return snapshot(j)


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str):
    j = jobs.get(job_id)
    if not j:
        raise HTTPException(404, "任务不存在")
    q = asyncio.Queue()
    subscribers.setdefault(job_id, set()).add(q)

    async def gen():
        try:
            yield f"data: {json.dumps(snapshot(j), ensure_ascii=False)}\n\n"
            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            subscribers.get(job_id, set()).discard(q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    j = jobs.get(job_id)
    if not j:
        raise HTTPException(404, "任务不存在")
    j["cancel_requested"] = True
    proc = j.get("process")
    if proc is not None and proc.poll() is None:
        proc.terminate()
    append_log(j, "⚠️ 已发送取消信号")
    save_jobs()
    push_event(job_id, snapshot(j))
    return snapshot(j)


@app.get("/api/jobs/{job_id}/download")
def download_job(job_id: str):
    j = jobs.get(job_id)
    if not j:
        raise HTTPException(404, "任务不存在")
    out = Path(j["output_dir"])
    if not out.exists():
        raise HTTPException(404, "输出目录不存在")
    zip_path = out.parent / f"{job_id}_result.zip"
    if not zip_path.exists():
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(out.rglob("*")):
                if p.is_file():
                    zf.write(p, p.relative_to(out))
    background = None
    if config.get("auto_delete_output_after_download", True):
        background = BackgroundTask(cleanup_output, job_id)
    return FileResponse(
        zip_path,
        filename=f"{j['name']}_result.zip",
        media_type="application/zip",
        background=background,
    )


@app.post("/api/jobs/{job_id}/delete")
def delete_job_output(job_id: str):
    j = jobs.get(job_id)
    if not j:
        raise HTTPException(404, "任务不存在")
    cleanup_output(job_id)
    return snapshot(j)


if __name__ == "__main__":
    uvicorn.run("app:app", host=config.get("host", "127.0.0.1"), port=int(config.get("port", 6006)))
