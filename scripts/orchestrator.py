# -*- coding: utf-8 -*-
"""剪映工作台编排器：按「观察 -> 成稿 -> 精修」串行调用三个 MCP。

计划文件（plan.json）由 AI 编排层根据用户需求生成，结构见 prompts/01 模板：
{
  "draft_name": "演示短片",
  "canvas": {"width": 1920, "height": 1080, "fps": 30},
  "observe": ["C:/abs/source.mp4"],          // 可选；默认取分镜里的视频
  "storyboard": [
    {"duration": "3s", "video": "C:/abs/a.mp4", "audio": "C:/abs/v.mp3",
     "caption": "开场字幕", "transition": "叠化"}
  ],
  "refine": [
    {"tool": "jianying_set_clip_transform",
     "arguments": {"uniform_scale": 1.2, "transform_y": 0.0}}
  ],
  "allow_replace": false
}

用法：
  python orchestrator.py --probe                       # 握手三个 server 并列工具
  python orchestrator.py --plan plan.json --dry-run   # 只校验计划与素材，不写草稿
  python orchestrator.py --plan plan.json              # 跑完整流水线
  python orchestrator.py --plan plan.json --stage draft --skip-observe
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_client import McpClient, McpError, load_server_config  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
WORK_DIR = PROJECT_ROOT / "work"
REPORT_DIR = WORK_DIR / "reports"
LOG_DIR = WORK_DIR / "logs"


def load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def log_line(log_fh, stage: str, message: str, **extra) -> None:
    rec = {"ts": datetime.now().isoformat(timespec="seconds"), "stage": stage,
           "message": message, **extra}
    log_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    log_fh.flush()
    print(f"[{stage}] {message}")


def make_client(key: str, servers: dict, stderr_path: Path) -> McpClient:
    cfg = servers[key]
    return McpClient(
        name=key,
        command=cfg["command"],
        args=cfg.get("args", []),
        env=cfg.get("env"),
        stderr_path=str(stderr_path),
    )


def clean_path(p: Any) -> Any:
    """去掉路径两端的引号、空格（模型常给路径多包一层引号）。"""
    if not isinstance(p, str):
        return p
    return p.strip().strip('"').strip("'").strip()


def normalize_plan(plan: dict) -> dict:
    """清洗计划里的媒体路径，原地修改并返回 plan。"""
    for shot in plan.get("storyboard") or []:
        for field in ("video", "audio"):
            if shot.get(field):
                shot[field] = clean_path(shot[field])
    for step in plan.get("refine") or []:
        args = step.get("arguments") or {}
        for field in ("media_path", "source", "video", "audio"):
            if isinstance(args.get(field), str):
                args[field] = clean_path(args[field])
    return plan


def validate_plan(plan: dict) -> list[str]:
    """返回错误列表；空列表表示通过。"""
    normalize_plan(plan)
    errors: list[str] = []
    if not plan.get("draft_name"):
        errors.append("缺少 draft_name")
    shots = plan.get("storyboard") or []
    if plan.get("stage", "all") in ("all", "draft") and not shots:
        errors.append("storyboard 为空，无法成稿")
    for i, shot in enumerate(shots):
        for field in ("video", "audio"):
            p = shot.get(field)
            if p and not os.path.isabs(p):
                errors.append(f"shot[{i}].{field} 不是绝对路径: {p}")
            if p and not Path(p).exists():
                errors.append(f"shot[{i}].{field} 文件不存在: {p}")
        if not shot.get("duration"):
            errors.append(f"shot[{i}] 缺少 duration")
    for i, step in enumerate(plan.get("refine") or []):
        if not step.get("tool"):
            errors.append(f"refine[{i}] 缺少 tool")
    return errors


def cmd_probe(servers: dict) -> int:
    ok = True
    for key, cfg in servers.items():
        stderr_path = LOG_DIR / f"probe_{key}.stderr"
        try:
            with make_client(key, servers, stderr_path) as client:
                tools = client.list_tools()
                print(f"[{key}] 握手 OK，{len(tools)} 个工具:")
                for t in tools:
                    print("   -", t["name"])
        except McpError as e:
            ok = False
            print(f"[{key}] 失败: {e}")
    return 0 if ok else 1


def run_observe(client: McpClient, sources: list[str], report: dict, log_fh) -> None:
    observations = []
    for src in sources:
        if not Path(src).exists():
            log_line(log_fh, "observe", f"素材不存在，跳过: {src}")
            continue
        log_line(log_fh, "observe", f"分析素材: {src}")
        res = client.call_tool("analyze_video_source",
                               {"source": src, "transcription": "none",
                                "detail": "efficient", "max_frames": 6})
        observations.append(res)
        if isinstance(res, dict):
            meta = res.get("metadata", {})
            frames = res.get("frames", [])
            log_line(log_fh, "observe",
                     f"完成 {Path(src).name}: {meta.get('width')}x{meta.get('height')} "
                     f"{meta.get('fps')} {meta.get('codec')}，代表帧 {len(frames)} 张",
                     analysis_id=res.get("analysis_id"))
    report["observations"] = observations


def run_draft(client: McpClient, plan: dict, report: dict, log_fh) -> None:
    canvas = plan.get("canvas", {})
    shots = plan["storyboard"]
    args = {
        "draft_name": plan["draft_name"],
        "shots": shots,
        "width": canvas.get("width", 1920),
        "height": canvas.get("height", 1080),
        "fps": canvas.get("fps", 30),
        "allow_replace": bool(plan.get("allow_replace", False)),
    }
    log_line(log_fh, "draft", f"一步成稿: {plan['draft_name']}（{len(shots)} 镜）")
    res = client.call_tool("build_from_storyboard", args, timeout=180)
    report["draft_result"] = res
    log_line(log_fh, "draft", "成稿完成", result=res)


def run_refine(client: McpClient, plan: dict, report: dict, log_fh) -> None:
    draft = plan["draft_name"]
    steps = plan.get("refine") or []
    if not steps:
        log_line(log_fh, "refine", "无精修步骤，跳过")
        return
    # 写入前先备份草稿
    try:
        backup = client.call_tool("jianying_backup_draft", {"name": draft})
        log_line(log_fh, "refine", "已备份草稿", backup=backup)
    except McpError as e:
        log_line(log_fh, "refine", f"备份失败，终止精修: {e}")
        raise
    results = []
    for i, step in enumerate(steps):
        tool = step["tool"]
        arguments = dict(step.get("arguments") or {})
        arguments.setdefault("name", draft)
        log_line(log_fh, "refine", f"步骤 {i + 1}/{len(steps)}: {tool}")
        res = client.call_tool(tool, arguments, timeout=120)
        results.append({"tool": tool, "arguments": arguments, "result": res})
        log_line(log_fh, "refine", f"{tool} 完成")
    report["refine_results"] = results


def execute_plan(plan: dict, servers: dict, *, dry_run: bool = False,
                 stage: str = "all", skip_observe: bool = False,
                 log_fh=None, save_report: bool = True) -> tuple[dict, int]:
    """执行一份计划，返回 (report, exit_code)。供 CLI 与网页后端共用。"""
    normalize_plan(plan)
    report: dict = {"draft_name": plan["draft_name"], "dry_run": dry_run,
                    "started_at": datetime.now().isoformat(timespec="seconds")}
    own_fh = False
    if log_fh is None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_fh = open(LOG_DIR / f"{stamp}_{plan['draft_name']}.jsonl", "w", encoding="utf-8")
        own_fh = True

    try:
        if dry_run:
            log_line(log_fh, "validate", "dry-run：计划与素材校验通过，不写入草稿")
            report["status"] = "dry_run_ok"
            return report, 0

        sources = plan.get("observe") or [s["video"] for s in plan["storyboard"] if s.get("video")]

        if stage in ("all", "observe") and not skip_observe and sources:
            with make_client("jianying-ai", servers, LOG_DIR / "ai.stderr") as c:
                run_observe(c, sources, report, log_fh)

        if stage in ("all", "draft"):
            with make_client("jianying-autoedit", servers, LOG_DIR / "autoedit.stderr") as c:
                run_draft(c, plan, report, log_fh)

        if stage in ("all", "refine"):
            with make_client("jianying-draft", servers, LOG_DIR / "draft.stderr") as c:
                run_refine(c, plan, report, log_fh)

        report["status"] = "ok"
        return report, 0
    except McpError as e:
        report["status"] = "error"
        report["error"] = str(e)
        log_line(log_fh, "fatal", str(e))
        return report, 1
    finally:
        report["finished_at"] = datetime.now().isoformat(timespec="seconds")
        if save_report:
            REPORT_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            with open(REPORT_DIR / f"{stamp}_{plan['draft_name']}.json", "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
        if own_fh:
            log_fh.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="剪映工作台编排器")
    ap.add_argument("--plan", help="计划 JSON 文件")
    ap.add_argument("--probe", action="store_true", help="仅握手并列工具")
    ap.add_argument("--dry-run", action="store_true", help="只校验，不写草稿")
    ap.add_argument("--stage", choices=["all", "observe", "draft", "refine"], default="all")
    ap.add_argument("--skip-observe", action="store_true", help="跳过观察阶段")
    args = ap.parse_args()

    servers = load_server_config(CONFIG_DIR / "mcp_servers.json")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    if args.probe:
        return cmd_probe(servers)

    if not args.plan:
        ap.error("--plan 或 --probe 必选其一")

    plan = load_json(Path(args.plan))
    plan.setdefault("stage", args.stage)
    errors = validate_plan(plan)
    if errors:
        print("计划校验未通过：")
        for e in errors:
            print("  -", e)
        return 2

    report, code = execute_plan(
        plan, servers, dry_run=args.dry_run, stage=args.stage,
        skip_observe=args.skip_observe,
    )
    if report.get("status") == "dry_run_ok":
        print("dry-run：计划与素材校验通过，不写入草稿")
    return code


if __name__ == "__main__":
    sys.exit(main())
