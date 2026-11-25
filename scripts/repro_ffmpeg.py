# -*- coding: utf-8 -*-
"""复现 ffmpeg 在 Popen 下的挂起问题，对比 stdin/stdout/stderr 接法。"""
import subprocess
import sys
import tempfile
import time
from pathlib import Path

FF = r"C:\Program Files\ffmpeg\bin\ffmpeg.exe"
VIDEO = r"C:\Users\karry\AppData\Local\Doubao\User Data\Default\.doubao\agent_mode\workspace\.user_skills\jianying-editor\assets\video.mp4"


def _cmd(out_dir: Path) -> list[str]:
    return [
        FF, "-hide_banner", "-loglevel", "info", "-y", "-i", VIDEO,
        "-t", "5.000", "-skip_frame", "nokey",
        "-vf", "scale=min(512\\,iw):-2,showinfo", "-fps_mode", "vfr",
        "-q:v", "4", "-frames:v", "1000", str(out_dir / "frame_%05d.jpg"),
    ]


def run_case(name, stdin_m, stdout_m, stderr_m, timeout=15):
    out_dir = Path(tempfile.mkdtemp(prefix="jy_repro_"))
    log_path = Path(tempfile.mktemp(suffix=".log"))
    stderr_handle = open(log_path, "w") if stderr_m == "file" else stderr_m
    kw = {"stdin": stdin_m, "stdout": stdout_m, "stderr": stderr_handle}
    t0 = time.time()
    try:
        p = subprocess.Popen(_cmd(out_dir), **kw)
        rc = p.wait(timeout=timeout)
        n = len(list(out_dir.glob("*.jpg")))
        print(f"[{name}] 退出 rc={rc} 帧数={n} 耗时={time.time()-t0:.2f}s")
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait()
        n = len(list(out_dir.glob("*.jpg")))
        print(f"[{name}] 超时挂起 ❌ 帧数={n}")
    finally:
        stderr_handle.close()
        log_path.unlink(missing_ok=True)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode in ("inherit", "all"):
        # 原始行为：stdin 继承父进程；stdout/stderr 接文件
        run_case("stdin=inherit, stdout=DEVNULL, stderr=file",
                 None, subprocess.DEVNULL, "file")
    if mode in ("devnull", "all"):
        run_case("stdin=DEVNULL, stdout=DEVNULL, stderr=file",
                 subprocess.DEVNULL, subprocess.DEVNULL, "file")
    if mode in ("pipe", "all"):
        run_case("stdin=inherit, stdout=PIPE, stderr=PIPE(原始)",
                 None, subprocess.PIPE, subprocess.PIPE)
