"""Chinese one-click mode for the GitHub Story2Video project.

Story2Video supplies the web application and Edge TTS integration.  This
entrypoint adds a source-preserving subtitle pipeline and local MP4/WAV/SRT
exports for the user's black-background workflow.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import re
import sys
import threading
import traceback
import uuid
import webbrowser

from fastapi import HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

import make_caption_video as pipeline
os.environ["PORTABLE_MODE"] = "1"
from software.story2video import main as upstream


app = upstream.app
ROOT = pipeline.ROOT
INBOX = ROOT / "work" / "inbox"
OUTPUT = ROOT / "成品"
DICTIONARY = ROOT / "发音词典.tsv"
EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="caption-render")
JOBS: dict[str, dict] = {}
LOCK = threading.Lock()


class CreateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=15000)
    filename: str = "文稿.txt"
    voice: str = "zh-CN-XiaoxiaoNeural"
    rate: int = Field(default=-10, ge=-50, le=50)


def safe_stem(name: str) -> str:
    stem = Path(name.replace("\\", "/").split("/")[-1]).stem
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem).strip(" .")
    return (stem or "文稿")[:55]


def set_job(job_id: str, **fields) -> None:
    with LOCK:
        JOBS[job_id].update(fields)


def worker(job_id: str, request: CreateRequest) -> None:
    source = None
    job_inbox = None
    try:
        INBOX.mkdir(parents=True, exist_ok=True)
        name = safe_stem(request.filename)
        job_inbox = INBOX / job_id
        job_inbox.mkdir()
        source = job_inbox / f"{name}.txt"
        source.write_text(request.text, encoding="utf-8")
        def report(message: str) -> None:
            set_job(job_id, message=message)
        folder = pipeline.generate(source, OUTPUT, request.voice, request.rate, DICTIONARY, 44, report)
        files = {ext: next(folder.glob(f"*.{ext}")) for ext in ("mp4", "wav", "srt")}
        set_job(job_id, state="done", folder=str(folder), files={k: str(v) for k, v in files.items()}, message="制作完成，可以预览或分别下载三个文件。")
    except Exception as exc:
        set_job(job_id, state="error", message=str(exc))
        (ROOT / "work").mkdir(parents=True, exist_ok=True)
        (ROOT / "work" / f"error_{job_id}.log").write_text(traceback.format_exc(), encoding="utf-8")
    finally:
        if source is not None:
            source.unlink(missing_ok=True)
        if job_inbox is not None:
            try:
                job_inbox.rmdir()
            except OSError:
                pass


@app.post("/api/create")
def create(request: CreateRequest):
    if not request.text.strip():
        raise HTTPException(400, "请先输入或导入文字。")
    if request.voice not in {"zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural", "zh-CN-XiaoyiNeural", "zh-CN-YunjianNeural"}:
        raise HTTPException(400, "请选择界面提供的中文音色。")
    job_id = uuid.uuid4().hex[:12]
    with LOCK:
        JOBS[job_id] = {"state": "running", "message": "正在准备文稿…"}
    EXECUTOR.submit(worker, job_id, request)
    return {"job_id": job_id}


@app.get("/api/job/{job_id}")
def job(job_id: str):
    with LOCK:
        info = JOBS.get(job_id)
        if info is None:
            raise HTTPException(404, "任务不存在或程序已重启。")
        data = {k: v for k, v in info.items() if k != "files"}
    if data["state"] == "done":
        data["urls"] = {ext: f"/api/file/{job_id}/{ext}" for ext in ("mp4", "wav", "srt")}
    return data


@app.get("/api/file/{job_id}/{ext}")
def file(job_id: str, ext: str):
    if ext not in {"mp4", "wav", "srt"}:
        raise HTTPException(404)
    with LOCK:
        info = JOBS.get(job_id)
        path = info.get("files", {}).get(ext) if info and info.get("state") == "done" else None
    if not path or not Path(path).is_file():
        raise HTTPException(404)
    media = {"mp4": "video/mp4", "wav": "audio/wav", "srt": "text/plain; charset=utf-8"}[ext]
    return FileResponse(path, media_type=media, filename=Path(path).name)


@app.get("/api/health")
def health():
    return {"app": "黑底字幕视频工具", "version": "2.0"}


@app.get("/advanced")
def advanced():
    return HTMLResponse(upstream.FRONTEND_HTML.replace("{{VERSION}}", upstream.VERSION))


def main() -> None:
    import socket
    import urllib.request
    import uvicorn

    host, port = "127.0.0.1", 8765
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/health", timeout=0.5) as response:
            if response.read().find("黑底字幕视频工具".encode("utf-8")) >= 0:
                webbrowser.open(f"http://{host}:{port}/")
                return
    except Exception:
        pass
    with socket.socket() as probe:
        try:
            probe.bind((host, port))
        except OSError:
            probe.bind((host, 0))
            port = probe.getsockname()[1]
    threading.Timer(1.5, lambda: webbrowser.open(f"http://{host}:{port}/")).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
