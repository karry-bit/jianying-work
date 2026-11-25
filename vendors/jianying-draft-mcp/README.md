# jianying-draft-mcp

**MCP server for CapCut / 剪映专业版 (JianYing Pro) draft projects** — 让任何支持 MCP 的 AI 直接读写剪映草稿：建片、排轨、字幕、特效、转场、动画、裁剪、抠像、关键帧、变速、变声、调色。

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-stdio%20%2B%20HTTP%2FSSE-6f42c1)](https://modelcontextprotocol.io/)
[![License](https://img.shields.io/badge/license-MIT-green)](./LICENSE)

> Unofficial, community project. Not affiliated with ByteDance / 深圳市脸萌科技 / CapCut.
> It works by reading and writing the **draft project files** that the app itself produces — no DRM circumvention, no paid-feature bypass.

---

## 它解决什么问题

剪映的工程文件（`draft_content.json`）是一套结构固定的 JSON，但字段多、命名不规则、版本之间还会变。这个 MCP 服务器把它封成 **47 个工具**，AI 就能像操作数据库一样操作剪辑工程：

```
你：「把这支片子第 3 段放大到 135%、旋转 8°、音量压到 0.35，再给字幕加黑色半透明背景」
AI：（调用 jianying_set_clip_transform / jianying_set_segment_audio / jianying_set_text_background）
你：（在剪映里打开草稿，参数已经在那儿了）
```

## 为什么不是「调 SDK」

剪映没有公开的自动化接口：主程序和引擎 DLL 只导出 Qt/C++ 内部类，导出面板是 QML 自绘，不暴露可访问对象。所以本项目的做法是**直接对草稿 JSON 做结构化读写**——这正是社区（如 `pyJianYingDraft`）已验证可行的路径，而本项目在字段覆盖和版本适配上做了更细的实测。

## 47 个工具

<details>
<summary><b>草稿与查询（7）</b></summary>

| 工具 | 说明 |
|---|---|
| `jianying_locate` | 定位安装与草稿目录（注册表 → INI → 默认路径三级回退） |
| `jianying_list_drafts` | 草稿列表（时间/大小/是否加密/封面） |
| `jianying_get_draft_detail` | 草稿详情 + 时间线摘要 |
| `jianying_read_side_file` | 读草稿目录里的明文侧文件 |
| `jianying_create_draft` | 新建明文草稿（分辨率/帧率） |
| `jianying_backup_draft` | zip 备份 |
| `jianying_open_draft` | 在剪映里打开草稿 |
</details>

<details>
<summary><b>素材与时间线（3）</b></summary>

| 工具 | 说明 |
|---|---|
| `jianying_add_media_clip` | 加视频/音频（支持挑高光段 `source_start_us`、音量） |
| `jianying_add_text_clip` | 加字幕 |
| `jianying_group_segments` | 成组 / 解组 |
</details>

<details>
<summary><b>视觉（13）</b></summary>

| 工具 | 说明 |
|---|---|
| `jianying_set_clip_transform` | 位置 / 缩放 / 旋转 / 翻转 / 透明度 / 等比缩放 |
| `jianying_set_crop` | 裁剪（八点 + 比例 + 缩放，可 reset） |
| `jianying_set_matting` | 抠像（智能抠像 / 自定义路径 / 清画笔） |
| `jianying_set_video_algorithm` | 防抖 / 降噪 / 去频闪 / 运动模糊 |
| `jianying_set_video_flags` | 美颜总开关 / 素材锁定 |
| `jianying_set_color_switches` | 五个调色开关 |
| `jianying_set_adjust_values` | 亮度/对比/饱和/色温/色调/高光/阴影/锐化/褪色/色相/暗角 |
| `jianying_set_hdr` | HDR（转换模式/强度/nits） |
| `jianying_set_keyframes` | 关键帧（属性 + 时间点 + 曲线） |
| `jianying_set_segment_layer` | 显隐 / 渲染层级 |
| `jianying_set_segment_flags` | cartoon / 占位 / track_attribute |
| `jianying_list_segment_effects` | 列出片段挂载的特效 |
| `jianying_remove_segment_effect` | 移除某个特效 |
</details>

<details>
<summary><b>特效 / 转场 / 动画 / 滤镜（7）</b></summary>

| 工具 | 说明 |
|---|---|
| `jianying_search_catalog` | 搜 10 个面板：effects / transitions / text_anim / fonts / face_prop / video_anim / sticker_anim / tone / voice_change / makeup |
| `jianying_add_effect_by_id` | 按 effect_id + resource_id 加画面特效/人物特效 |
| `jianying_add_transition_by_id` | 按 ID 加转场 |
| `jianying_add_filter_by_id` | 按 ID 加滤镜 |
| `jianying_add_animation_by_id` | 按 ID 加动画（入/出/循环） |
| `jianying_prefetch_package` | 从剪映 CDN 预取资源包并复刻本地缓存 |
| `jianying_scan_effects` | 扫描本机已用过的特效 ID（克隆源） |
</details>

<details>
<summary><b>文字（3）</b></summary>

| 工具 | 说明 |
|---|---|
| `jianying_set_text_font` | 字体（font_id / font_resource_id / font_path） |
| `jianying_set_text_style` | 字号/颜色/粗体/斜体/对齐/行距/字距/描边 |
| `jianying_set_text_shadow` · `jianying_set_text_layout` · `jianying_set_text_background` | 阴影 / 排版透明度 / 背景块 |
</details>

<details>
<summary><b>音频 / 变速 / 兜底（4）</b></summary>

| 工具 | 说明 |
|---|---|
| `jianying_set_segment_audio` | 音量 / 音频增强 / 淡入淡出 |
| `jianying_set_audio_tone` | 变声音色（tone_* 六个字段） |
| `jianying_set_segment_speed` | 倍速 / 倒放 / 变速曲线（自动同步 `source_timerange`） |
| `jianying_set_raw_field` | 分类白名单的通用写入（兜住低频字段，不会把字段写到错的对象上） |
</details>

<details>
<summary><b>面板读取（2）</b></summary>

| 工具 | 说明 |
|---|---|
| `jianying_get_segment_panel` | 一次读出片段的全部面板参数 |
| `jianying_search_effects` / `jianying_list_catalog` / `jianying_add_effect_from_template` / `jianying_add_adjust` / `jianying_add_filter` / `jianying_add_transition` / `jianying_add_video_effect` / `jianying_add_effect_from_template` | 按名称操作的便捷工具 |
</details>

## 快速开始

```bash
git clone https://github.com/<you>/jianying-draft-mcp.git
cd jianying-draft-mcp
python verify.py
```

期望输出：

```
[1] initialize  -> {'name': 'jianying-draft-mcp', 'version': '1.0.0'}
[2] tools/list  -> 47 个工具
[3] locate      -> 安装: True | 草稿目录: ...
结果: 全部通过 ✅
```

## 接入 AI 客户端

### 形态 A：stdio（推荐）

```json
{
  "mcpServers": {
    "jianying": {
      "command": "/path/to/python",
      "args": ["/path/to/jianying-draft-mcp/server/jianying_mcp_server.py"],
      "env": { "PYTHONIOENCODING": "utf-8" }
    }
  }
}
```

各客户端配置位置：

| 客户端 | 配置 |
|---|---|
| Claude Desktop | `%APPDATA%\Claude\claude_desktop_config.json` |
| Claude Code | `claude mcp add jianying -- python /path/to/server.py` |
| Cursor | `~/.cursor/mcp.json` |
| VS Code | 工作区 `.vscode/mcp.json`（键名 `servers`） |
| Cline / Roo Code | `cline_mcp_settings.json` |
| Trae | `%APPDATA%\Trae\User\mcp.json`（键名 `servers`） |
| Qoder | `~/.qoder/settings.json`（键名 `mcpServers`） |
| ZCode | `~/.zcode/cli/config.json`（键名 `mcp.servers`） |
| OpenClaw | `~/.openclaw/openclaw.json`（键名 `mcp.servers`） |
| Loomy（opencode） | `<data>/opencode/opencode.json`（`mcp.<id>`，`type:"local"`） |

现成模板见 [`configs/`](./configs)。

### 形态 B：HTTP / SSE（给只能填 URL 的客户端）

```bash
python bridge/mcp_http_bridge.py --port 8765
```

| 入口 | 用途 |
|---|---|
| `POST http://127.0.0.1:8765/mcp` | 单次 JSON-RPC，直接返回 JSON |
| `GET  http://127.0.0.1:8765/sse` | MCP SSE 传输 |
| `GET  http://127.0.0.1:8765/health` | 健康检查 |

加 `--host 0.0.0.0` 可让局域网设备接入（注意：桥没有鉴权，只在可信网络使用）。

## 示例提示词

```
列出我的草稿，打开「xxx」，第 0 段放大到 135%、旋转 8°、音量 0.35
看看第 8 段挂了哪些特效，把那个会员素材摘掉
给这段打关键帧：0 秒缩放 100%，2 秒缩放到 150%
裁掉画面上下各 10%，再开防抖和降噪
把字幕背景改成黑色 60% 透明、圆角 0.08
搜一下转场里的「泛光」，加到相邻片段上
```

## 设计取向与边界

**做对了什么**

- 字段名全部取自**应用自己重写过**的草稿，不是猜的
- 写入前检查目标对象，`set_raw_field` 会拒绝「把文字字段写进 segment」
- 关键帧、HDR、`freeze` 这几个没有真实样本的，返回值里明确带 `verified: false`

**做不到 / 不做的**

| 事项 | 原因 |
|---|---|
| 绕过会员素材校验 | 那是付费功能，本项目不触碰 |
| 程序化点「导出」 | 剪映导出面板是 QML 自绘，不暴露可访问对象；导出请手动点 |
| 修改已加密草稿 | 5.9+/6.x 的 `draft_content.json` 是流密码加密的，只读元信息 |
| 保证所有字段都被引擎采纳 | 写入 JSON ≠ 生效；见 `docs/SCHEMA_COVERAGE.md` |
| 草稿正在被剪映打开时写入 | 应用会用内存版本覆盖磁盘，先关应用或先备份 |

**已验证版本**：剪映专业版 4.3.1（Windows）。5.x 明文草稿大概率可用，6.x 加密草稿只读。

## 目录结构

```
.
├─ server/jianying_mcp_server.py   MCP 服务器（47 工具，纯标准库）
├─ bridge/mcp_http_bridge.py       HTTP/SSE 桥（纯标准库）
├─ configs/                        各客户端配置模板
├─ docs/SCHEMA_COVERAGE.md         草稿字段 × 工具 覆盖率表
├─ examples/                       提示词示例
├─ start.bat                       stdio 启动器
└─ verify.py                       自检
```

## English

An MCP (Model Context Protocol) server for **CapCut / JianYing Pro** draft projects, Windows.
It exposes 47 tools so any MCP-capable AI client can create drafts, add media/subtitles,
apply effects, transitions, animations, crop, keying, keyframes, speed and colour changes —
by reading and writing the draft JSON the app itself produces.

- Pure Python standard library, no dependencies
- Transports: **stdio** and **HTTP/SSE** bridge
- Tested against JianYing Pro **4.3.1**; encrypted 6.x drafts are read-only
- No DRM circumvention, no paid-feature bypass, not affiliated with ByteDance/CapCut

```bash
python verify.py                       # self-check
python bridge/mcp_http_bridge.py       # optional HTTP transport on :8765
```

## License

[MIT](./LICENSE) — 见 [NOTICE.md](./NOTICE.md) 关于商标与免责声明。

## 贡献

欢迎 PR：新增工具、补 5.x/6.x 明文草稿的字段、补其他客户端配置。
提 issue 时请附 `jianying_get_segment_panel` 的输出（可自行删掉路径）。
