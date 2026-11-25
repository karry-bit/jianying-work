# -*- coding: utf-8 -*-
"""大模型接入桥接（可选编排层）。

- 配置存在本机 config/model_config.json，API Key 不明文回显。
- 走 OpenAI 兼容的 POST {base_url}/chat/completions，兼容 DeepSeek / MiniMax / 通义百炼 / 自建等。
- 用标准库 urllib，不引入第三方依赖。
"""
from __future__ import annotations

import base64
import json
import mimetypes
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
CONFIG_PATH = CONFIG_DIR / "model_config.json"

DEFAULT_CONFIG = {
    "provider": "custom",
    "base_url": "",
    "api_key": "",
    "model": "",
    "temperature": 0.7,
    "multimodal": False,
}

PROVIDER_PRESETS = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com",
    "minimax": "https://api.minimax.chat/v1",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "custom": "",
}

SYSTEM_PROMPT = """你是剪映自动化编排器。根据用户需求和素材信息，输出一份可执行的剪辑计划 JSON。
只输出 JSON 对象，不要任何解释、不要 markdown 代码块。

JSON 结构：
{
  "draft_name": "草稿名",
  "canvas": {"width": 1920, "height": 1080, "fps": 30},
  "storyboard": [
    {"duration": "3s", "video": "素材绝对路径", "audio": "可选音频绝对路径", "caption": "字幕", "transition": "可选转场名"}
  ],
  "refine": [
    {"tool": "③工具名", "arguments": {"参数名": "值"}}
  ]
}

硬性规则：
- video/audio 必须使用下方给定素材的绝对路径，禁止编造路径；路径原样填写，两端不要加引号，不要转义反斜杠。
- duration 用 "5s" 这种写法；镜头画面素材时长须 ≥ 镜头时长；旁白音频时长 ≤ 镜头时长。
- 素材时长不足以支撑需求时，保守拆分镜，不要虚构素材时长。
- refine 的 tool 必须从给定的可用工具列表里选；拿不准就返回空数组 []。
- 不要写音量关键帧、蒙版、模糊背景；不使用内置音乐、TTS 配音、自动卡点。
- 转场/滤镜/特效用剪映常见中文名。
- draft_name 不含 \\ / : * ? " < > | 等文件名字符。"""


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            cfg.update({k: v for k, v in saved.items() if k in DEFAULT_CONFIG})
        except json.JSONDecodeError:
            pass
    return cfg


def save_config(cfg: dict) -> dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    merged = load_config()
    for k in DEFAULT_CONFIG:
        if k in cfg:
            merged[k] = cfg[k]
    # 空 Key 不覆盖已保存的 Key（前端留空表示不修改）
    if not cfg.get("api_key"):
        merged["api_key"] = load_config().get("api_key", "")
    CONFIG_PATH.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    return merged


def public_config(cfg: dict) -> dict:
    """回显给前端的配置，剔除 Key，只给是否已配置和掩码。"""
    masked = ""
    if cfg.get("api_key"):
        k = cfg["api_key"]
        masked = ("*" * max(0, len(k) - 4)) + k[-4:]
    return {
        "provider": cfg.get("provider", "custom"),
        "base_url": cfg.get("base_url", ""),
        "model": cfg.get("model", ""),
        "temperature": cfg.get("temperature", 0.7),
        "multimodal": bool(cfg.get("multimodal", False)),
        "has_key": bool(cfg.get("api_key")),
        "api_key_masked": masked,
    }


def _strip_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.removeprefix("json").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("模型返回里没有 JSON 对象")
    return json.loads(text[start:end + 1])


def _encode_image(path: str) -> str:
    mime, _ = mimetypes.guess_type(path)
    mime = mime or "image/jpeg"
    raw = Path(path).read_bytes()
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


def call_chat(messages: list[dict], cfg: dict, timeout: float = 60.0) -> str:
    base = cfg["base_url"].rstrip("/")
    url = f"{base}/chat/completions"
    payload = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": float(cfg.get("temperature", 0.7)),
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg['api_key']}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"模型接口 HTTP {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"模型接口连接失败: {e.reason}")
    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        raise RuntimeError(f"模型返回结构异常: {json.dumps(body, ensure_ascii=False)[:300]}")


def build_messages(requirement: str, assets: list[dict], tools: list[str],
                   multimodal: bool) -> list[dict]:
    """assets: [{path, type, metadata?, frames?}]"""
    asset_lines = []
    for a in assets:
        line = f"- 路径: {a['path']}"
        if a.get("metadata"):
            m = a["metadata"]
            line += (f"｜时长 {m.get('duration_ms')}ms｜{m.get('width')}x{m.get('height')} "
                     f"｜{m.get('codec')}｜{m.get('fps')}fps｜有音频: {m.get('has_audio')}")
        asset_lines.append(line)

    tool_line = "可用精修工具：" + ("、".join(tools) if tools else "（未取得）")
    user_text = (
        f"用户需求：{requirement}\n\n"
        f"素材清单：\n" + "\n".join(asset_lines) + f"\n\n{tool_line}\n\n"
        "请据此输出剪辑计划 JSON。"
    )

    if multimodal:
        content: list[Any] = [{"type": "text", "text": user_text}]
        for a in assets:
            for frame in (a.get("frames") or [])[:6]:
                p = frame.get("path") if isinstance(frame, dict) else frame
                if p and Path(p).exists():
                    content.append({"type": "image_url",
                                    "image_url": {"url": _encode_image(p)}})
        return [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content}]
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_text}]


def requirement_to_plan(requirement: str, assets: list[dict], tools: list[str],
                        canvas: dict, draft_name: str) -> dict:
    cfg = load_config()
    if not cfg.get("api_key") or not cfg.get("model") or not cfg.get("base_url"):
        raise RuntimeError("尚未配置模型（地址 / Key / 模型名）")
    messages = build_messages(requirement, assets, tools, bool(cfg.get("multimodal")))
    content = call_chat(messages, cfg)
    plan = _strip_json(content)

    # 兜底填充画布与草稿名
    plan.setdefault("canvas", canvas)
    plan.setdefault("draft_name", draft_name or "AI 生成短片")
    plan.setdefault("storyboard", [])
    plan.setdefault("refine", [])
    plan.setdefault("allow_replace", False)
    return plan
