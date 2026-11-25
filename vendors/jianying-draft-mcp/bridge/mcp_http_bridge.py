# -*- coding: utf-8 -*-
"""
JianYing Draft MCP - HTTP/SSE 桥
================================================================
把 stdio 的 MCP 服务器暴露成 HTTP，供只能配 URL 的客户端使用：

  POST /mcp                       单次 JSON-RPC，直接返回 JSON（无状态，最简单）
  GET  /sse                       SSE 通道，先下发 endpoint 事件（老版 MCP SSE 传输）
  POST /messages?session=<id>     配合 /sse 的请求投递
  GET  /health                    健康检查 + 工具数

用法：
  python mcp_http_bridge.py --port 8765
  python mcp_http_bridge.py --port 8765 --host 0.0.0.0 -- python D:\path\jianying_mcp_server.py

纯标准库实现。
"""
import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs


class StdioMCP:
    """一个常驻的 stdio MCP 子进程，请求-响应按行配对。"""

    def __init__(self, cmd):
        self.cmd = cmd
        self.proc = None
        self.lock = threading.Lock()
        self.buf = queue.Queue()
        self._boot()

    def _boot(self):
        self.proc = subprocess.Popen(
            self.cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding='utf-8',
            errors='ignore', bufsize=1)
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self.buf.put(json.loads(line))
            except Exception:
                continue

    def request(self, payload, timeout=60):
        with self.lock:
            if self.proc.poll() is not None:
                self._boot()
            self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
            rid = payload.get("id")
            try:
                while True:
                    msg = self.buf.get(timeout=timeout)
                    if rid is None or msg.get("id") == rid:
                        return msg
            except queue.Empty:
                return {"jsonrpc": "2.0", "id": rid,
                        "error": {"code": -32000, "message": "上游超时"}}


class Sessions:
    def __init__(self, cmd):
        self.cmd = cmd
        self.map = {}

    def get(self, sid=None):
        if sid and sid in self.map:
            return sid, self.map[sid]
        sid = sid or uuid.uuid4().hex
        self.map[sid] = StdioMCP(self.cmd)
        return sid, self.map[sid]

    def drop(self, sid):
        m = self.map.pop(sid, None)
        if m and m.proc:
            try:
                m.proc.terminate()
            except Exception:
                pass


def make_handler(sessions, default_cmd):
    class H(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, fmt, *args):
            sys.stderr.write("[bridge] %s\n" % (fmt % args))

        def _json(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(body)

        def _read_body(self):
            n = int(self.headers.get('Content-Length') or 0)
            raw = self.rfile.read(n) if n else b''
            return raw.decode('utf-8', 'ignore')

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Methods', 'GET,POST,OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, Accept')
            self.send_header('Content-Length', '0')
            self.end_headers()

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == '/health':
                sid, mcp = sessions.get('health')
                r = mcp.request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
                n = len((r.get('result') or {}).get('tools') or [])
                return self._json(200, {"ok": True, "tools": n, "bridge": "jianying-mcp"})
            if u.path == '/sse':
                sid, mcp = sessions.get()
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
                self.send_header('Cache-Control', 'no-cache')
                self.send_header('Connection', 'keep-alive')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                endpoint = '/messages?session=%s' % sid
                self.wfile.write(("event: endpoint\ndata: %s\n\n" % endpoint).encode())
                self.wfile.flush()
                try:
                    while True:
                        msg = mcp.buf.get(timeout=30)
                        data = json.dumps(msg, ensure_ascii=False)
                        self.wfile.write(("event: message\ndata: %s\n\n" % data).encode())
                        self.wfile.flush()
                except Exception:
                    sessions.drop(sid)
                    return
            return self._json(404, {"error": "not found"})

        def do_POST(self):
            u = urlparse(self.path)
            qs = parse_qs(u.query)
            raw = self._read_body()
            try:
                payload = json.loads(raw) if raw.strip() else {}
            except Exception as e:
                return self._json(400, {"error": "bad json: %s" % e})
            if u.path == '/mcp':
                sid, mcp = sessions.get()
                if isinstance(payload, list):
                    out = [mcp.request(p) for p in payload]
                    return self._json(200, out)
                return self._json(200, mcp.request(payload))
            if u.path == '/messages':
                sid = (qs.get('session') or [None])[0]
                _, mcp = sessions.get(sid)
                threading.Thread(target=mcp.request, args=(payload,), daemon=True).start()
                self.send_response(202)
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            return self._json(404, {"error": "not found"})

    return H


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    default_server = os.path.join(here, '..', 'server', 'jianying_mcp_server.py')
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--server', default=default_server)
    ap.add_argument('cmd', nargs='*', help='自定义上游命令（默认 python <server>）')
    a = ap.parse_args()
    cmd = a.cmd or [sys.executable, a.server]

    sessions = Sessions(cmd)
    srv = ThreadingHTTPServer((a.host, a.port), make_handler(sessions, cmd))
    print('JianYing Draft MCP bridge')
    print('  上游命令 : %s' % ' '.join(cmd))
    print('  单次调用 : http://%s:%d/mcp' % (a.host, a.port))
    print('  SSE 通道 : http://%s:%d/sse' % (a.host, a.port))
    print('  健康检查 : http://%s:%d/health' % (a.host, a.port))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\nbye')


if __name__ == '__main__':
    main()
