# -*- coding: utf-8 -*-
"""通用 MCP stdio 探测脚本：initialize -> tools/list -> 可选 tools/call。

用法示例：
  py -3.12 scripts/probe_stdio.py --py .venv/Scripts/python.exe \
      --module jianying_autoedit.server --pythonpath <repo> \
      --call list_drafts '{}'
"""
import argparse
import json
import os
import subprocess
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--py", required=True, help="解释器路径")
    ap.add_argument("--module", help="以 -m 模块方式启动")
    ap.add_argument("--script", help="以脚本文件方式启动")
    ap.add_argument("--pythonpath", help="追加到 PYTHONPATH 的目录")
    ap.add_argument("--cwd", default=None)
    ap.add_argument("--call", metavar="TOOL",
                    help="调用一个工具；参数用位置参数 JSON 或 --call-arg-file")
    ap.add_argument("--call-arg-file", metavar="FILE",
                    help="从文件读取 tools/call 的 arguments JSON（避免命令行中文编码问题）")
    args = ap.parse_args()

    env = dict(os.environ)
    if args.pythonpath:
        env["PYTHONPATH"] = args.pythonpath + os.pathsep + env.get("PYTHONPATH", "")

    if args.module:
        cmd = [args.py, "-m", args.module]
    elif args.script:
        cmd = [args.py, args.script]
    else:
        ap.error("--module 或 --script 二选一")

    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "probe", "version": "1"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    if args.call:
        tool_name = args.call
        if args.call_arg_file:
            with open(args.call_arg_file, encoding="utf-8") as f:
                call_args = json.load(f)
        else:
            call_args = {}
        msgs.append({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                     "params": {"name": tool_name, "arguments": call_args}})

    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, encoding="utf-8",
                          errors="ignore", bufsize=1, env=env, cwd=args.cwd)

    for m in msgs:
        p.stdin.write(json.dumps(m, ensure_ascii=False) + "\n")
        p.stdin.flush()

    responses = {}
    import time
    deadline = time.time() + 60
    while len(responses) < len(msgs) and time.time() < deadline:
        line = p.stdout.readline()
        if not line:
            break
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        rid = r.get("id")
        if rid is None:
            continue  # 进度/日志通知，跳过
        responses[rid] = r

    ok = 0
    for m in msgs:
        rid = m["id"]
        r = responses.get(rid)
        if not r:
            continue
        if rid == 1:
            print("[1] initialize ->", r["result"]["serverInfo"])
            ok += 1
        elif rid == 2:
            tools = r["result"]["tools"]
            print("[2] tools/list -> %d 个工具" % len(tools))
            for t in tools:
                print("      -", t["name"])
            ok += 1
        elif rid == 3:
            res = r.get("result") or {}
            if "error" in r:
                print("[3] call %s -> 错误:" % tool_name, json.dumps(r["error"], ensure_ascii=False)[:800])
            else:
                txt = res.get("content", [{}])[0].get("text", "")
                print("[3] call %s ->" % tool_name)
                print(txt[:4000])
            ok += 1

    p.stdin.close()
    p.terminate()
    err = p.stderr.read()
    if err.strip():
        print("stderr:", err[:500])
    print("\n结果: %s" % ("全部通过 ✅" if ok == len(msgs) else "有失败 ❌"))
    return 0 if ok == len(msgs) else 1


if __name__ == "__main__":
    sys.exit(main())
