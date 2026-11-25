# 剪映剪视频工作台

用户输入剪辑需求 → AI 编排层结合提示词，串行调用三个开源剪映 MCP 完成**素材观察 → 一键成稿 → 草稿精修**；导出环节由用户在剪映里手动完成（本工作台不做无头导出）。

## 流水线分工（前 → 中 → 后，串行而非并列）

| 阶段 | MCP 服务 | 仓库 | 本机实测 | 职责 |
|---|---|---|---|---|
| ① 观察 | `jianying-ai` | `vendors/jianying-ai-mcp` | 8 工具，抽帧/元数据/清单通 | 看素材、出编辑清单；也可按 manifest 直接建稿 |
| ② 成稿 | `jianying-autoedit` | `vendors/JianYing-AutoEdit-MCP` | 11 工具，分镜一步落盘 | 视频轨 + 旁白 + 字幕 + 转场骨架成稿 |
| ③ 精修 | `jianying-draft` | `vendors/jianying-draft-mcp` | 47 工具，读详情/缩放/滤镜/转场/调色/抠像写入通 | 对成稿做增量微调，写入前自动备份 |

## 目录结构

```
jianyin work/
├── config/
│   ├── paths.json          # 本机路径常量（草稿根、解释器、ffmpeg、样片）
│   └── mcp_servers.json    # 三个 stdio MCP 的注册配置（命令/参数/环境变量）
├── scripts/
│   ├── orchestrator.py     # 编排器：观察→成稿→精修，备份/日志/dry-run
│   ├── mcp_client.py       # 可复用的 stdio MCP 客户端封装
│   ├── probe_stdio.py      # 通用握手/列工具/单工具调用探测脚本
│   ├── repro_ffmpeg.py      # ffmpeg 挂起诊断对照脚本（留档）
│   └── start_web.ps1       # PowerShell 启动网页工作台
├── web/
│   ├── server.py          # 本地 Web 服务（标准库 http.server，无第三方依赖）
│   ├── model_bridge.py   # 大模型接入桥接（配置存取、OpenAI 兼容调用、需求→计划）
│   └── index.html         # 工作台单页界面（原生 JS）
├── plans/
│   └── smoke_plan.json     # 冒烟测试样例计划（正式计划由 AI 按提示词生成）
├── prompts/
│   ├── 01_requirement_to_plan.md   # 需求 → 成稿计划模板
│   └── 02_draft_to_refine.md       # 草稿现状 → 精修指令模板
├── vendors/                 # 三个 MCP 仓库克隆 + 各自隔离 venv
├── work/                   # 运行时产物（观察缓存、日志、报告；运行时生成）
└── start_web.bat          # 双击启动网页工作台
```

## 网页工作台（推荐日常使用）

### 打开方式

- 双击项目根目录的 `start_web.bat`；或在 PowerShell 里运行：

```powershell
py -3.12 web\server.py --port 8765
```

- 浏览器访问 <http://127.0.0.1:8765>（脚本会自动打开）。服务只监听本机回环地址，不对外网暴露。

### 页面操作

1. **（可选）接入大模型**：在「模型接入」区选提供方预设（OpenAI / DeepSeek / MiniMax / 通义百炼 / 自定义），填接口地址、模型名、API Key，保存。Key 只存本机 `config/model_config.json`，页面只回显掩码，不明文返回。
2. 填**草稿名**和画布尺寸/帧率；在**需求描述**里写想剪成什么样。素材点输入框旁的 **📂 浏览**按钮，从本机目录选择视频/音频，路径自动填入；也可以手填绝对路径。
3. 点 **✨ AI 根据需求生成分镜**：后端先观察素材（元数据/代表帧），再调用配置的模型把需求拆成分镜计划，回填到分镜列表；多模态模型可附带代表帧。
4. 在**分镜列表**核对/逐镜调整：视频路径、旁白音频（可选）、时长、字幕、转场；需要微调时在「高级：精修步骤」选工具、填参数 JSON。
5. 点**校验计划**先检查；通过后点**生成草稿**，右侧实时看运行日志和结果报告。
6. 草稿写入剪映草稿目录后，**在剪映里打开预览、手动导出**。

> 不接模型也能用：手工填分镜后直接校验、生成草稿。模型只负责"需求 → 分镜计划"这一步，执行仍走三个 MCP。

### 后台接口（页面背后调用）

| 接口 | 方法 | 作用 |
|---|---|---|
| `/api/meta` | GET | 样片路径 + ③ 可用精修工具列表 |
| `/api/fs/list?path=` | GET | 浏览本机目录：子目录 + 视频/音频素材文件（过滤媒体扩展名） |
| `/api/model-config` | GET/POST | 读取/保存模型配置（Key 不明文回显） |
| `/api/plan-from-requirement` | POST | 观察素材后调用模型，由需求生成计划 JSON |
| `/api/validate` | POST | 校验计划结构与素材路径，不写草稿 |
| `/api/run` | POST | 后台执行计划（`dry_run=true` 只校验），返回 `job_id` |
| `/api/status/<id>` | GET | 轮询任务状态、日志、报告 |

### 模型接入说明

- 走 **OpenAI 兼容**的 `POST {base_url}/chat/completions`，兼容 DeepSeek、MiniMax、通义百炼（阿里云兼容模式）、OpenAI 及自建/中转网关。
- 配置项：提供方预设、`base_url`、`model`、`api_key`、温度、是否多模态。
- 多模态开启时，把观察到的代表帧以 base64 图片消息发给模型；纯文本模型请关闭该开关。
- 模型返回必须是计划 JSON；后端会做兜底填充（画布、草稿名）并再跑一次结构校验。
- 当前同一时刻只跑一条流水线。

## 命令行方式（备选）

> 解释器用 Python 3.12：`C:\Users\karry\AppData\Local\Programs\Python\Python312\python.exe`（命令行用 `py -3.12`）。

### 1. 握手探测（不写草稿）

```powershell
py -3.12 scripts/orchestrator.py --probe
```

逐个拉起三个服务、完成 initialize 握手并列出工具；预期 `8 + 11 + 47` 个工具。

### 2. 校验计划（dry-run，不写草稿）

```powershell
py -3.12 scripts/orchestrator.py --plan plans/smoke_plan.json --dry-run
```

只检查计划结构、素材路径是否存在、时长字段是否齐全。

### 3. 跑完整流水线

```powershell
py -3.12 scripts/orchestrator.py --plan plans/smoke_plan.json
```

按 观察 → 成稿 → 精修 顺序执行；每步写 `work/logs/` 日志，结果汇总到 `work/reports/`。

只跑某个阶段、跳过观察：

```powershell
py -3.12 scripts/orchestrator.py --plan plans/smoke_plan.json --stage draft --skip-observe
```

## 计划 JSON 怎么来

- **新片**：按 `prompts/01_requirement_to_plan.md`，AI 先观察素材，再把用户需求拆成分镜 + 初版精修，产出计划 JSON。
- **改片**：按 `prompts/02_draft_to_refine.md`，AI 读取已有草稿现状，把修改意见翻译成 `refine` 步骤数组。

计划字段约束见两份模板；② 分镜字段、① manifest 字段以仓库 `tools/list` 的 `inputSchema` 和 `vendors/jianying-ai-mcp/skills/jianying-ai-editor/references/manifest-schema.md` 为准。

## 环境基线（本机已核对）

- 剪映专业版：运行中 11.4.2.14459；Apps 目录另存 11.8.0.14778 更新包；顶层启动器读 11.8.0。
- 草稿根：`%LOCALAPPDATA%\JianyingPro\User Data\Projects\com.lveditor.draft`（现有 21 个老草稿均为加密格式）。
- ffmpeg/ffprobe：`C:\Program Files\ffmpeg\bin`。
- ① 基线验证版本 10.0.5，② 锚点 11.1，③ 锚点 4.3.1；在本机 11.4/11.8 上**部分字段覆盖率未全量验证**，生成后需在 GUI 抽检。

## 已验证 / 未验证

### 已实测通过

- 三服务 initialize 握手、tools/list 列举。
- ① `analyze_video_source` 抽代表帧、读元数据（2046×1080 / h264 / 60fps / 无音频）。
- ② `build_from_storyboard` 一步成稿落盘（主画面轨 + 字幕轨，5s）。
- ③ `get_draft_detail` 读回草稿；`set_clip_transform` 等比缩放写入并确认落盘。
- 编排器端到端冒烟：观察 → 成稿 → 备份 → 精修全链路 `status: ok`。

### 尚未验证 / 受限

- **GUI 视觉抽检**：工具写出的明文草稿需在剪映里人工打开，确认画面/字幕/动画/滤镜显示正常（① 兼容性提示明确要求生成后抽样确认）。
- 老的 21 个加密草稿：③ 只读元信息，② 打不开，① 需另配解密器 `jy-draftc.exe` 才能分析；首版不碰老片子。
- 无 `GROQ_API_KEY` / `OPENAI_API_KEY` 时 Whisper 转写不可用（编排器观察调用已固定 `transcription="none"`）。
- ③ 中 HDR、曲线变速、关键帧属性编号、部分抠像/防抖字段未经真实样本验证，先小样验证再批量用。
- 不操作剪映内置音乐/音效库、TTS 配音、自动卡点、口型同步；旁白需自备音频文件。

## 关键踩坑与补丁

**观察器 ffmpeg 挂起（已修）**：① `video_observer.py` 原先用 `subprocess.run(capture_output=True)`（stdout/stderr 均接 PIPE），子进程 stderr 缓冲写满后父子互相等待，死锁挂起。补丁改为：stderr 落临时文件回读、stdout=DEVNULL、stdin=DEVNULL，经 `_run_ffmpeg` 统一调用；ffprobe 仍用 `_run`。
- 补丁位置：`vendors/jianying-ai-mcp/src/jianying_ai_mcp/video_observer.py`。
- 注意：若对 vendor 仓库 `git pull` 更新，需确认该补丁未被覆盖。

## 安全与操作注意

- **草稿在剪映里打开时，应用会用内存版本覆盖磁盘**：精修已有草稿前，先在剪映里关闭该草稿；新草稿文件夹写入风险较低。编排器在精修前会自动调 ③ `jianying_backup_draft` 打包备份。
- 不要把业务剪辑脚本写进 vendor skill 内部目录；业务代码放本项目 `scripts/`、`plans/`、`prompts/`。
- ② 仓库为 GPL-3.0；若工作台后续要分发给他人，合并/分发方式需另行评估。

## 待决事项

1. 是否编译配置 `jy-draftc.exe` 解密器，以支持读取/精修加密老草稿（当前不可用）。
2. 三仓库在 11.4/11.8 上的 GUI 抽检与字段覆盖率补验。
3. 是否需要额外的本地 Web / 对话界面（当前为配置 + 编排脚本 + 提示词模板形态）。
