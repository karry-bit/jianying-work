# -*- coding: utf-8 -*-
"""自检：initialize + tools/list + 一次真实调用。"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, 'server', 'jianying_mcp_server.py')

msgs = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize",
     "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "verify", "version": "1"}}},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
     "params": {"name": "jianying_locate", "arguments": {}}},
]

p = subprocess.Popen([sys.executable, SERVER], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore', bufsize=1)
for m in msgs:
    p.stdin.write(json.dumps(m, ensure_ascii=False) + "\n")
p.stdin.flush()
ok = 0
for _ in msgs:
    line = p.stdout.readline()
    if not line:
        break
    r = json.loads(line)
    rid = r.get('id')
    if rid == 1:
        print('[1] initialize  ->', r['result']['serverInfo'])
        ok += 1
    elif rid == 2:
        tools = r['result']['tools']
        print('[2] tools/list  -> %d 个工具' % len(tools))
        ok += 1
    elif rid == 3:
        res = r['result']
        txt = res.get('content', [{}])[0].get('text', '')
        d = json.loads(txt) if txt.strip().startswith('{') else {}
        print('[3] locate      -> 安装:', d.get('installed'), '| 草稿目录:', str(d.get('draft_dir'))[:60])
        ok += 1
p.stdin.close()
p.terminate()
err = p.stderr.read()
if err.strip():
    print('stderr:', err[:300])
print('\n结果: %s' % ('全部通过 ✅' if ok == 3 else '有失败 ❌'))
sys.exit(0 if ok == 3 else 1)
