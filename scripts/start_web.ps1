# 启动剪映工作台本地网页（127.0.0.1:8765）
$root = Split-Path $PSScriptRoot -Parent
Start-Process "http://127.0.0.1:8765"
& py -3.12 (Join-Path $root "web\server.py") --port 8765
