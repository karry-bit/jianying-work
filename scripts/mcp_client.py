# -*- coding: utf-8 -*-
"""可复用的 MCP stdio 客户端封装。

设计要点（来自本次实测踩坑）：
- stderr 落临时文件，不接 PIPE：避免 ffmpeg/子进程 stderr 缓冲写满后父子互相等待死锁。
- stdout 由独立读线程消费、放进队列，主线程按 message id 取结果；通知（无 id）直接跳过。
- 仅依赖标准库，配合 config/mcp_servers.json 使用。
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import tempfile
import threading
import time
from typing import Any, Optional


class McpError(RuntimeError):
    pass


class McpClient:
    def __init__(
        self,
        name: str,
        command: str,
        args: Optional[list[str]] = None,
        env: Optional[dict[str, str]] = None,
        cwd: Optional[str] = None,
        stderr_path: Optional[str] = None,
        default_timeout: float = 60.0,
    ):
        self.name = name
        self.command = command
        self.args = list(args or [])
        self.cwd = cwd
        self.default_timeout = default_timeout
        self._id = 0
        self._proc: Optional[subprocess.Popen] = None
        self._stderr_fh = None
        self._stderr_path = stderr_path
        self._queue: "queue.Queue[Any]" = queue.Queue()
        self._reader: Optional[threading.Thread] = None
        self._env = dict(os.environ)
        if env:
            self._env.update(env)

    # ---- 生命周期 ----

    def start(self) -> dict:
        if self._stderr_path:
            os.makedirs(os.path.dirname(self._stderr_path), exist_ok=True)
            self._stderr_fh = open(self._stderr_path, "w", encoding="utf-8", errors="replace")
            stderr_target: Any = self._stderr_fh
        else:
            self._stderr_fh = tempfile.NamedTemporaryFile(
                mode="w", delete=False, suffix=".stderr", encoding="utf-8", errors="replace"
            )
            self._stderr_path = self._stderr_fh.name
            stderr_target = self._stderr_fh

        self._proc = subprocess.Popen(
            [self.command, *self.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr_target,
            text=True,
            encoding="utf-8",
            errors="ignore",
            bufsize=1,
            env=self._env,
            cwd=self.cwd,
        )
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

        info = self._request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "jianying-workbench", "version": "1.0"},
            },
            timeout=self.default_timeout,
        )
        # 初始化完成通知（无 id、无需响应）
        self._notify("notifications/initialized", {})
        return info.get("serverInfo", info)

    def close(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                if self._proc.stdin:
                    self._proc.stdin.close()
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        if self._stderr_fh:
            try:
                self._stderr_fh.close()
            except Exception:
                pass

    def __enter__(self) -> "McpClient":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ---- 底层收发 ----

    def _read_loop(self) -> None:
        assert self._proc and self._proc.stdout
        for line in self._proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            self._queue.put(msg)
        self._queue.put(None)  # EOF 哨兵

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _write(self, msg: dict) -> None:
        assert self._proc and self._proc.stdin
        self._proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self._proc.stdin.flush()

    def _notify(self, method: str, params: dict) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method: str, params: dict, timeout: Optional[float] = None) -> dict:
        rid = self._next_id()
        self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        deadline = time.time() + (timeout or self.default_timeout)
        while time.time() < deadline:
            try:
                msg = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if msg is None:
                raise McpError(f"[{self.name}] 子进程提前退出（EOF）")
            if msg.get("id") != rid:
                continue  # 其他响应或通知，留待后续
            if "error" in msg:
                raise McpError(f"[{self.name}] {method} 错误: {json.dumps(msg['error'], ensure_ascii=False)}")
            return msg.get("result", {})
        raise McpError(f"[{self.name}] {method} 超时（{timeout or self.default_timeout}s）")

    # ---- 业务接口 ----

    def list_tools(self) -> list[dict]:
        result = self._request("tools/list", {})
        return result.get("tools", [])

    def call_tool(self, tool: str, arguments: Optional[dict] = None,
                  timeout: Optional[float] = None) -> Any:
        result = self._request(
            "tools/call", {"name": tool, "arguments": arguments or {}},
            timeout=timeout,
        )
        if result.get("isError"):
            raise McpError(f"[{self.name}] 工具 {tool} 返回 isError: {result}")
        content = result.get("content", [])
        if not content:
            return result
        text = content[0].get("text", "")
        if not text:
            return result
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def stderr_tail(self, n: int = 800) -> str:
        if not self._stderr_path or not os.path.exists(self._stderr_path):
            return ""
        try:
            with open(self._stderr_path, encoding="utf-8", errors="replace") as f:
                return f.read()[-n:]
        except Exception:
            return ""


def load_server_config(config_path: str) -> dict[str, dict]:
    with open(config_path, encoding="utf-8") as f:
        data = json.load(f)
    return data["mcpServers"]
