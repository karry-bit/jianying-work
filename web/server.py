# -*- coding: utf-8 -*-
"""剪映工作台本地 Web 服务（仅标准库）。

启动：
  py -3.12 web/server.py            # 默认 127.0.0.1:8765
  py -3.12 web/server.py --port 9000

接口：
  GET  /                  工作台页面
  GET  /api/meta          样例素材路径 + ③ 可用精修工具名
  POST /api/validate      校验计划 {plan} -> {ok, errors}
  POST /api/run           后台执行计划 -> {job_id}
  GET  /api/status/<id>   任务状态 / 日志 / 报告
"""
from __future__ import annotations

import argparse
import json
import os
import string
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

WEB_DIR = Path(__file__).resolve().parent
ROOT_DIR = WEB_DIR.parent
sys.path.insert(0, str(ROOT_DIR / "scripts"))

from orchestrator import load_server_config, validate_plan, execute_plan, clean_path  # noqa: E402
import model_bridge  # noqa: E402

CONFIG_DIR = ROOT_DIR / "config"
SERVERS = load_server_config(CONFIG_DIR / "mcp_servers.json")

with open(CONFIG_DIR / "paths.json", encoding="utf-8") as f:
    PATHS = json.load(f)

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS


def list_drives() -> list[str]:
    return [f"{d}:\\" for d in string.ascii_uppercase if Path(f"{d}:\\").exists()]


def list_dir(path: str) -> dict:
    if not path:
        drives = list_drives()
        return {"current": "", "parent": "",
                "dirs": [{"name": d, "path": d} for d in drives], "files": []}
    p = Path(path).resolve()
    if not p.exists() or not p.is_dir():
        raise FileNotFoundError(f"目录不存在: {path}")
    dirs, files = [], []
    for child in os.scandir(p):
        try:
            if child.is_dir(follow_symlinks=False):
                dirs.append({"name": child.name, "path": child.path})
            elif Path(child.path).suffix.lower() in MEDIA_EXTS:
                files.append({"name": child.name, "path": child.path,
                              "size": child.stat().st_size})
        except OSError:
            continue
    dirs.sort(key=lambda x: x["name"].lower())
    files.sort(key=lambda x: x["name"].lower())
    parent = str(p.parent) if str(p.parent) != str(p) else ""
    return {"current": str(p), "parent": parent, "dirs": dirs, "files": files}

_TOOLS_CACHE: dict = {"tools": None}


def get_refine_tools() -> list[str]:
    if _TOOLS_CACHE["tools"] is not None:
        return _TOOLS_CACHE["tools"]
    try:
        from mcp_client import McpClient
        cfg = SERVERS["jianying-draft"]
        with McpClient(name="jianying-draft", command=cfg["command"],
                       args=cfg.get("args", []), env=cfg.get("env")) as c:
            _TOOLS_CACHE["tools"] = [t["name"] for t in c.list_tools()]
    except Exception:
        _TOOLS_CACHE["tools"] = []
    return _TOOLS_CACHE["tools"]


def enrich_assets(paths: list[str]) -> list[dict]:
    """对视频素材做观察取元数据/代表帧（尽力而为，失败则只给路径）。"""
    paths = [clean_path(p) for p in paths if p]
    assets: list[dict] = []
    videos = [p for p in paths if Path(p).suffix.lower() in VIDEO_EXTS]
    audios = [p for p in paths if Path(p).suffix.lower() in AUDIO_EXTS]
    if videos:
        try:
            from mcp_client import McpClient
            cfg = SERVERS["jianying-ai"]
            with McpClient(name="jianying-ai", command=cfg["command"],
                           args=cfg.get("args", []), env=cfg.get("env")) as c:
                for v in videos:
                    res = c.call_tool("analyze_video_source",
                                      {"source": v, "transcription": "none",
                                       "detail": "efficient", "max_frames": 6})
                    assets.append({"path": v, "type": "video",
                                   "metadata": res.get("metadata", {}),
                                   "frames": res.get("frames", [])})
        except Exception:
            assets.extend({"path": v, "type": "video"} for v in videos)
    for a in audios:
        assets.append({"path": a, "type": "audio"})
    return assets

# 任务仓库：job_id -> {status, logs, report, error, created_at}
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
RUNNER_LOCK = threading.Lock()  # 同一时刻只跑一条流水线


class CapturingFH:
    """把编排器的日志行收进内存列表，供页面轮询。"""

    def __init__(self, logs: list):
        self.logs = logs

    def write(self, text: str) -> None:
        for line in text.splitlines():
            if line.strip():
                self.logs.append(line)

    def flush(self) -> None:
        pass


def _new_job() -> dict:
    return {"status": "pending", "logs": [], "report": None,
            "error": None, "created_at": time.time()}


def run_job(job_id: str, plan: dict, dry_run: bool) -> None:
    with RUNNER_LOCK:
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "running"
        fh = CapturingFH(JOBS[job_id]["logs"])
        try:
            report, code = execute_plan(plan, SERVERS, dry_run=dry_run, log_fh=fh)
            with JOBS_LOCK:
                JOBS[job_id]["report"] = report
                JOBS[job_id]["status"] = "dry_run_ok" if report.get("status") == "dry_run_ok" else (
                    "ok" if code == 0 else "error")
                if code != 0:
                    JOBS[job_id]["error"] = report.get("error", "执行失败")
        except Exception as e:  # 兜底，避免线程静默死掉
            with JOBS_LOCK:
                JOBS[job_id]["status"] = "error"
                JOBS[job_id]["error"] = f"{type(e).__name__}: {e}"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 静默默认访问日志
        pass

    def _send_json(self, obj: dict, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/meta":
            self._send_json({
                "sample": {
                    "video": PATHS.get("sample_asset_video"),
                    "audio": PATHS.get("sample_asset_audio"),
                    "srt": PATHS.get("sample_asset_srt"),
                },
                "refine_tools": get_refine_tools(),
            })
        elif self.path == "/api/model-config":
            self._send_json(model_bridge.public_config(model_bridge.load_config()))
        elif self.path.startswith("/api/fs/list"):
            q = parse_qs(urlparse(self.path).query)
            path = q.get("path", [""])[0]
            try:
                self._send_json(list_dir(path))
            except Exception as e:
                self._send_json({"error": f"{type(e).__name__}: {e}"}, 400)
        elif self.path.startswith("/api/status/"):
            job_id = self.path.rsplit("/", 1)[-1]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if not job:
                self._send_json({"error": "任务不存在"}, 404)
                return
            self._send_json(job)
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        if self.path == "/api/model-config":
            body = self._read_body()
            saved = model_bridge.save_config(body)
            self._send_json({"ok": True, "config": model_bridge.public_config(saved)})
            return
        if self.path == "/api/plan-from-requirement":
            body = self._read_body()
            requirement = (body.get("requirement") or "").strip()
            if not requirement:
                self._send_json({"ok": False, "errors": ["需求描述为空"]}, 400)
                return
            paths = body.get("assets") or []
            canvas = body.get("canvas") or {"width": 1920, "height": 1080, "fps": 30}
            draft_name = body.get("draft_name") or ""
            try:
                assets = enrich_assets(paths)
                tools = get_refine_tools()
                plan = model_bridge.requirement_to_plan(requirement, assets, tools,
                                                        canvas, draft_name)
                errors = validate_plan(plan)
                self._send_json({"ok": not errors, "plan": plan, "errors": errors})
            except Exception as e:
                self._send_json({"ok": False, "errors": [f"{type(e).__name__}: {e}"]}, 502)
            return
        if self.path == "/api/validate":
            body = self._read_body()
            plan = body.get("plan") or {}
            if isinstance(plan, str):
                try:
                    plan = json.loads(plan)
                except json.JSONDecodeError:
                    plan = {}
            try:
                errors = validate_plan(plan)
            except Exception as e:
                self._send_json({"ok": False, "errors": [f"计划解析失败: {e}"]}, 400)
                return
            self._send_json({"ok": not errors, "errors": errors})
            return
        if self.path == "/api/run":
            body = self._read_body()
            plan = body.get("plan") or {}
            if isinstance(plan, str):
                try:
                    plan = json.loads(plan)
                except json.JSONDecodeError:
                    plan = {}
            dry_run = bool(body.get("dry_run", False))
            try:
                errors = validate_plan(plan)
            except Exception as e:
                self._send_json({"ok": False, "errors": [f"计划解析失败: {e}"]}, 400)
                return
            if errors:
                self._send_json({"ok": False, "errors": errors}, 400)
                return
            job_id = uuid.uuid4().hex[:12]
            with JOBS_LOCK:
                JOBS[job_id] = _new_job()
            threading.Thread(target=run_job, args=(job_id, plan, dry_run), daemon=True).start()
            self._send_json({"ok": True, "job_id": job_id})
            return
        self._send_json({"error": "not found"}, 404)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"剪映工作台已启动: http://{args.host}:{args.port}")
    print("按 Ctrl+C 停止服务。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
