# 示例提示词

把 MCP 接进客户端后，直接用自然语言说就行。下面是按场景整理的可用例句，
工具名在括号里，AI 会自己挑。

## 从零建片

```
新建一个 1280×720、30fps 的草稿叫「测试片」，
把 D:\media\a.mp4 从第 5 秒开始截 3 秒放进去，
再加一段 b.mp4 接在后面，最后加字幕「测试」放在 0.5 秒处。
```
（`create_draft` → `add_media_clip` ×2 → `add_text_clip`）

## 改画面参数

```
第 0 段放大到 135%，旋转 8 度，水平翻转，透明度 90%。
```
（`set_clip_transform`）

```
把这段上下各裁 10%，再开防抖和降噪。
```
（`set_crop` → `set_video_algorithm`）

```
第 2 段做智能抠像。
```
（`set_matting`）

## 关键帧

```
给第 0 段的缩放打关键帧：0 秒时 100%，2 秒时 150%。
```
（`set_keyframes`，`property_type=2`）

```
把这个片段的透明度做成 0 → 100% 的淡入。
```
（`set_keyframes`，`property_type=4`）

## 音频

```
所有视频片段音量压到 0.35，配乐保持 1.0。
```
（`set_segment_audio`）

```
第 1 段加 0.5 秒淡入、0.8 秒淡出。
```
（`set_segment_audio`，`fade_in_us` / `fade_out_us`）

```
把这段人声换成「低沉大叔」。
```
（先 `search_catalog(panel="voice_change")` 拿 ID，再 `set_audio_tone`）

## 变速

```
第 3 段改成 2 倍速，第 4 段倒放。
```
（`set_segment_speed`）

## 特效 / 转场 / 动画

```
搜一下「泛光」转场，加在第 0 段和第 1 段之间，时长 0.4 秒。
```
（`search_catalog` → `add_transition_by_id`）

```
给第 0 段加「发光」画面特效，强度 1.0。
```
（`search_catalog(panel="effects")` → `add_effect_by_id`）

```
给这条字幕加「打字机IV」入场动画。
```
（`add_animation_by_id`，`anim_type="in"`）

```
第 8 段现在挂了哪些特效？把第 2 个删掉。
```
（`list_segment_effects` → `remove_segment_effect`）

## 文字样式

```
第 0 条字幕：后现代体，30 号，金色 #FFD400，加粗 0.06。
```
（`set_text_font` → `set_text_style`）

```
字幕加黑色背景块，60% 透明，圆角 0.08，再加一点红色阴影。
```
（`set_text_background` → `set_text_shadow`）

## 巡检与备份

```
列一下我的草稿，告诉我哪些是加密的。
```
（`list_drafts`）

```
把「xxx」这个草稿备份一份，然后逐段报告画面参数。
```
（`backup_draft` → `get_segment_panel`）

## 注意

- 改草稿前**先关掉剪映里打开的那个工程**，否则应用会用内存版本覆盖磁盘
- 会员素材（付费特效/字体）不要用 `add_effect_by_id` 强行加，本项目不绕过其授权
- 导出不在 MCP 范围内，改完在剪映里手动点导出
