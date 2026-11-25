# -*- coding: utf-8 -*-
r"""
剪映草稿 MCP 服务器 (JianYing Draft MCP Server)
================================================
基于对剪映专业版 4.3.1 安装器/运行时的逆向成果构建：
  - 草稿目录定位逻辑复刻自 TTInstaller 存根（注册表 HKCU\Software\Bytedance\JianyingPro
    \GlobalSettings\History → currentCustomDraftPath，INI globalSetting 回退，默认路径兜底）
  - 草稿目录结构 / 明文侧文件（draft_settings, key_value.json, draft_virtual_store.json,
    attachment_pc_common.json, draft_agency_config.json）来自 6.0.1 真实草稿样本
  - 已知边界：剪映 5.9+/6.x 会加密 draft_content.json / draft_meta_info.json（实测 6.0.1，
    base64(流密码)，熵 7.97，跨文件 keystream 不同）。本服务器对加密草稿只读元信息；
    新建草稿为明文格式（4.x/5.x 时代格式），目标版本需为明文草稿兼容版本。

纯 Python 标准库实现 MCP (Model Context Protocol) stdio 传输：
  每行一个 JSON-RPC 2.0 消息；支持 initialize / ping / tools/list / tools/call。
"""
import base64
import io
import json
import os
import re
import shutil
import struct
import sys
import time
import uuid
import zipfile
from pathlib import Path

SERVER_NAME = "jianying-draft-mcp"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2024-11-05"

# --------------------------------------------------------------------------
# 环境 hardened 路径（exec shell 可能缺 APPDATA/LOCALAPPDATA —— 逆向时踩过的坑）
# --------------------------------------------------------------------------
def _user_home() -> Path:
    return Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))


def _local_appdata() -> Path:
    la = os.environ.get("LOCALAPPDATA")
    if la and Path(la).exists():
        return Path(la)
    return _user_home() / "AppData" / "Local"


# --------------------------------------------------------------------------
# 草稿目录定位（复刻逆向出的剪映自身逻辑）
# --------------------------------------------------------------------------
REG_CURRENT_DRAFT_PATH = r"Software\Bytedance\JianyingPro\GlobalSettings\History"
INI_GLOBAL_SETTING = r"User Data\Config\globalSetting"
DEFAULT_DRAFT_REL = r"User Data\Projects\com.lveditor.draft"


def _read_registry_value(hive: int, subkey: str, value: str):
    """读注册表，不依赖 winreg 之外的库；失败返回 None。"""
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(hive, subkey) as k:
            v, _ = winreg.QueryValueEx(k, value)
            return v
    except OSError:
        return None


def _read_ini_value(ini_path: Path, section: str, key: str):
    try:
        txt = ini_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    cur_sec = None
    for line in txt.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            cur_sec = line[1:-1].strip()
            continue
        if cur_sec == section and "=" in line:
            k, _, v = line.partition("=")
            if k.strip() == key:
                return v.strip()
    return None


def locate() -> dict:
    home = _user_home()
    la = _local_appdata()
    jy_root = la / "JianyingPro"

    # 1) 注册表 currentCustomDraftPath（逆向确认的权威来源）
    draft_dir = None
    src = None
    reg = _read_registry_value(0x80000001, REG_CURRENT_DRAFT_PATH, "currentCustomDraftPath")  # HKCU
    if reg and Path(reg).exists():
        draft_dir, src = Path(reg), "registry:currentCustomDraftPath"

    # 2) INI globalSetting [General] currentCustomDraftPath（安装器迁移逻辑读取的另一个来源）
    if draft_dir is None:
        ini = jy_root / INI_GLOBAL_SETTING
        v = _read_ini_value(ini, "General", "currentCustomDraftPath")
        if v and Path(v).exists():
            draft_dir, src = Path(v), "ini:globalSetting"

    # 3) 默认路径兜底
    if draft_dir is None:
        cand = jy_root / DEFAULT_DRAFT_REL
        if cand.exists():
            draft_dir, src = cand, "default"

    # 安装状态
    exe = None
    apps = jy_root / "Apps"
    if apps.exists():
        vers = sorted([p for p in apps.iterdir() if p.is_dir()], key=lambda p: p.name)
        for vdir in reversed(vers):
            for cand_name in ("JianyingPro.exe",):
                c = vdir / cand_name
                if c.exists():
                    exe = str(c)
                    break
            if exe:
                break
    reg_uninstall = _read_registry_value(
        0x80000001, r"Software\Microsoft\Windows\CurrentVersion\Uninstall\JianyingPro", "DisplayVersion")
    reg_meta = {}
    for k in ("hid", "uninstallTime", "last_checked_version"):
        v = _read_registry_value(0x80000001, r"Software\Bytedance\JianyingPro", k)
        if v is not None:
            reg_meta[k] = str(v)

    installed = exe is not None
    return {
        "jy_root": str(jy_root),
        "draft_dir": str(draft_dir) if draft_dir else None,
        "draft_dir_source": src,
        "app_exe": exe,
        "installed": installed,
        "registry": reg_meta,
        "registry_uninstall_display_version": reg_uninstall,
        "notes": [
            "定位逻辑复刻自剪映安装器/运行时：注册表 currentCustomDraftPath → INI globalSetting → 默认路径",
            "注册表显示 last_checked_version=%s uninstallTime=%s" % (
                reg_meta.get("last_checked_version", "?"), reg_meta.get("uninstallTime", "?")),
        ] if not installed else ["剪映已安装"],
    }


# --------------------------------------------------------------------------
# 草稿枚举与读取
# --------------------------------------------------------------------------
ENC_MARKER = "ENCRYPTED(base64)"


def _is_plain_json(p: Path) -> bool:
    try:
        with open(p, "rb") as f:
            head = f.read(2)
        return head[:1] == b"{"
    except OSError:
        return False


def list_drafts() -> list:
    loc = locate()
    dd = loc.get("draft_dir")
    if not dd:
        return []
    out = []
    for d in sorted(Path(dd).iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not d.is_dir() or d.name.startswith("."):
            continue
        settings = _read_ini_value(d / "draft_settings", "General", "draft_create_time")
        last_edit = _read_ini_value(d / "draft_settings", "General", "draft_last_edit_time")
        content = d / "draft_content.json"
        meta = d / "draft_meta_info.json"
        size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        out.append({
            "name": d.name,
            "folder": str(d),
            "draft_create_time": int(settings) if settings and settings.isdigit() else None,
            "draft_last_edit_time": int(last_edit) if last_edit and last_edit.isdigit() else None,
            "content_state": "plaintext" if _is_plain_json(content) else ENC_MARKER,
            "meta_state": "plaintext" if _is_plain_json(meta) else ENC_MARKER,
            "has_cover": (d / "draft_cover.jpg").exists(),
            "size_bytes": size,
        })
    return out


def _draft_dir(name: str) -> Path:
    loc = locate()
    dd = loc.get("draft_dir")
    if not dd:
        raise FileNotFoundError("未找到草稿目录（剪映未安装且注册表无记录）")
    p = Path(dd) / name
    if not p.is_dir():
        raise FileNotFoundError(f"草稿不存在: {name}")
    return p


def draft_detail(name: str) -> dict:
    d = _draft_dir(name)
    out = {"name": name, "folder": str(d), "files": {}}
    for f in sorted(d.iterdir()):
        if f.is_file():
            out["files"][f.name] = f.stat().st_size
    for side in ("key_value.json", "draft_virtual_store.json",
                 "attachment_pc_common.json", "draft_agency_config.json"):
        p = d / side
        if p.exists() and _is_plain_json(p):
            try:
                out[side] = json.loads(p.read_text(encoding="utf-8"))
            except Exception as ex:
                out[side] = f"<解析失败: {ex}>"
    st = {}
    for k in ("draft_create_time", "draft_last_edit_time", "real_edit_seconds"):
        v = _read_ini_value(d / "draft_settings", "General", k)
        if v:
            st[k] = v
    out["draft_settings"] = st
    content = d / "draft_content.json"
    if _is_plain_json(content):
        try:
            out["content_summary"] = summarize_content(
                json.loads(content.read_text(encoding="utf-8")))
        except Exception as ex:
            out["content_summary"] = f"<解析失败: {ex}>"
    else:
        out["content_summary"] = "draft_content.json 已加密（剪映 5.9+/6.x 行为），无法读取时间线"
    return out


def summarize_content(c: dict) -> dict:
    mats = c.get("materials", {}) or {}
    trk = []
    for t in c.get("tracks", []) or []:
        segs = []
        for s in t.get("segments", []) or []:
            tt = s.get("target_timerange") or {}
            segs.append({
                "material_id": s.get("material_id"),
                "start_us": tt.get("start"), "duration_us": tt.get("duration"),
            })
        trk.append({"type": t.get("type"), "segments": segs})
    return {
        "name": c.get("name"), "duration_us": c.get("duration"),
        "fps": c.get("fps"), "canvas": c.get("canvas_config"),
        "materials_count": {k: len(v) for k, v in mats.items() if isinstance(v, list)},
        "tracks": trk,
    }


# --------------------------------------------------------------------------
# 备份
# --------------------------------------------------------------------------
def backup_draft(name: str, dest: str = None) -> str:
    d = _draft_dir(name)
    ts = time.strftime("%Y%m%d_%H%M%S")
    if not dest:
        dest = str(Path(d).parent / "_mcp_backups")
    os.makedirs(dest, exist_ok=True)
    out = os.path.join(dest, f"{name}_{ts}.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in Path(d).rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(d))
    return out


# --------------------------------------------------------------------------
# 新建草稿（明文 4.x/5.x 格式）
# --------------------------------------------------------------------------
def _uuid() -> str:
    return str(uuid.uuid4()).upper()


EMPTY_MATERIALS = {
    "ai_translates": [], "audio_balances": [], "audio_effects": [], "audios": [],
    "beats": [], "canvases": [], "chromas": [], "color_curves": [], "digital_humans": [],
    "drafts": [], "effects": [], "filters": [], "handwrites": [], "hsl": [], "images": [],
    "log_color_wheels": [], "loudnesses": [], "masks": [], "material_animations": [],
    "material_colors": [], "multilanguage_refs": [], "placeholders": [], "plugin_effects": [],
    "realtime_denoises": [], "shapes": [], "smart_crops": [], "smart_relights": [],
    "sound_channel_mappings": [], "speeds": [], "stickers": [], "tail_leaders": [], "text_templates": [], "texts": [],
    "time_marks": [], "transitions": [], "video_effects": [], "video_trackings": [],
    "videos": [], "vocal_beautifys": [], "watermarks": [], "zmathues": [],
}


def _new_content(name: str, w: int, h: int, fps: int) -> dict:
    now = int(time.time())
    return {
        "canvas_config": {"height": h, "ratio": "original", "width": w},
        "color_space": "",
        "config": {"maintrack_adsorb": True},
        "cover": "",
        "creation_time": now,
        "duration": 0,
        "extra_info": None,
        "fps": fps,
        "id": _uuid(),
        "keep_materials": False,
        "keyframes": {"materials": [], "id": ""},
        "last_modified": now,
        "materials": {k: list(v) for k, v in EMPTY_MATERIALS.items()},
        "mutable_config": None,
        "name": name,
        "new_version": "70.0.0",
        "platform": {"app_id": 3704, "app_source": "edit",
                     "app_version": "4.3.1.10241", "device_id": "",
                     "hard_disk_id": "", "os_name": "Windows", "os_version": "",
                     "update_from": 0},
        "relationships": [],
        "render_index_track": None,
        "responsive_layout": None,
        "reverse_path": "",
        "root_path": "",
        "shader_watermark": "",
        "speed": None,
        "statistics": None,
        "tracks": [],
        "update_time": now,
        "version": 3.0,
    }


def create_draft(name: str, width: int = 1920, height: int = 1080, fps: int = 30) -> dict:
    loc = locate()
    if not loc.get("draft_dir"):
        raise FileNotFoundError("草稿目录不存在（注册表/INI/默认路径均未命中）")
    d = Path(loc["draft_dir"]) / name
    if d.exists():
        raise FileExistsError(f"草稿已存在: {name}")
    d.mkdir(parents=True)
    content = _new_content(name, width, height, fps)
    meta = {
        "cloud_package_completed_time": "",
        "draft_cloud_purchase": {},
        "draft_download_link": "",
        "draft_id": content["id"],
        "draft_is_ai_shorts": False,
        "draft_is_article_video_draft": False,
        "draft_is_from_deeplink": False,
        "draft_materials": [],
        "draft_name": name,
        "draft_new_version": content["new_version"],
        "draft_removable_storage_device": "",
        "draft_root_path": str(d) + os.sep,
        "draft_timeline_materials_size_": 0,
        "tm_draft_cloud_completed": "",
        "tm_draft_create": int(time.time() * 1000000),
        "tm_draft_modified": int(time.time() * 1000000),
        "tm_duration": 0,
    }
    (d / "draft_content.json").write_text(
        json.dumps(content, ensure_ascii=False, indent=1), encoding="utf-8")
    (d / "draft_meta_info.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    (d / "draft_settings").write_text(
        f"[General]\ndraft_create_time={int(time.time())}\n"
        f"draft_last_edit_time={int(time.time())}\nreal_edit_seconds=0\nreal_edit_keys=0\n",
        encoding="utf-8")
    (d / "draft_agency_config.json").write_text(
        '{"marterials":null,"use_converter":false,"video_resolution":720}', encoding="utf-8")
    return {"created": str(d), "draft_id": content["id"], "format": "plaintext(4.x/5.x 兼容)"}


# --------------------------------------------------------------------------
# 添加片段（仅对明文草稿）
# --------------------------------------------------------------------------
def _track_of(content: dict, ttype: str) -> dict:
    for t in content["tracks"]:
        if t.get("type") == ttype:
            return t
    t = {"attribute": 0, "extra_info": None, "flag": 0, "id": _uuid(),
         "is_default_name": True, "name": "", "segments": [], "type": ttype}
    content["tracks"].append(t)
    return t


def _clip_transform() -> dict:
    return {"alpha": 1.0, "flip": {"horizontal": False, "vertical": False},
            "rotation": 0.0, "scale": {"x": 1.0, "y": 1.0}, "transform": {"x": 0.0, "y": 0.0}}


def _keyframes() -> dict:
    return {"materials": [], "id": ""}


def add_media_clip(name: str, media_path: str, start_us: int, duration_us: int,
                   ctype: str = "video", track_type: str = None,
                   source_start_us: int = 0, volume: float = 1.0) -> dict:
    d = _draft_dir(name)
    cf = d / "draft_content.json"
    if not _is_plain_json(cf):
        raise ValueError("该草稿的 draft_content.json 已加密（5.9+/6.x），无法用本工具修改")
    media_path = str(Path(media_path))
    if not Path(media_path).exists():
        raise FileNotFoundError(f"媒体文件不存在: {media_path}")
    content = json.loads(cf.read_text(encoding="utf-8"))
    track_type = track_type or ("video" if ctype == "video" else "audio")
    mid = _uuid()
    fname = Path(media_path).name
    width = height = 0
    if ctype == "video":
        material = {"avg_bit_rate": 0, "category_name": "local", "duration_us": duration_us,
                    "extra_type_option": 0, "file_Path": media_path, "has_audio": True,
                    "height": height, "id": mid, "import_time_us": int(time.time() * 1000000),
                    "is_another_photo": False, "is_ai_generate_content": False,
                    "is_unregistered_beautify_material": False, "local_material_id": "",
                    "material_name": fname, "material_type": "", "md5": "",
                    "metatype": "", "path": media_path, "record_status": 0, "remote_url": "",
                    "type": ctype, "width": width}
        content["materials"]["videos"].append(material)
    else:
        material = {"category_name": "local", "duration_us": duration_us, "file_Path": media_path,
                    "id": mid, "import_time_us": int(time.time() * 1000000),
                    "material_name": fname, "material_type": "", "path": media_path,
                    "record_status": 0, "remote_url": "", "type": ctype}
        content["materials"]["audios"].append(material)
    speed_id = _uuid()
    content["materials"]["speeds"].append({
        "curve_speed": None, "id": speed_id, "mode": 0, "speed": 1.0, "type": "speed"})
    scm_id = _uuid()
    content["materials"]["sound_channel_mappings"].append({
        "audio_channel_mapping": 0, "id": scm_id, "type": "sound_channel_mapping"})
    refs = [speed_id]
    if ctype == "video":
        refs.append(scm_id)
    segment = {
        "additional_materials": [], "cartoon": False, "clip": _clip_transform(),
        "color_overlds": [], "enable_adjust": True, "enable_color_curves": True,
        "enable_color_wheels": True, "enable_lut": True, "extra_material_refs": refs,
        "flag": 0, "group_id": "", "id": _uuid(), "intensifies_audio_path": "",
        "intensifies_path": "", "is_ai_generate_content": False, "is_placeholder": False,
        "keyframes": _keyframes(), "last_nonzero_volume": 1.0, "loudness_rack": None,
        "material_id": mid, "middle_notes": [], "mini_beaats": [], "mini_beat_params": None,
        "mk_materials": [], "musical_notes": [], "path": "", "render_index": 0,
        "render_track_flags": 0, "responsive_layout": {"enable": False, "horizontal_pos": 0.0,
        "horizontal_scale": 1.0, "ratio": 0.0, "vertical_pos": 0.0, "vertical_scale": 1.0},
        "reverse": False, "source_timerange": {"duration": duration_us, "start": source_start_us},
        "speed": 1.0, "target_timerange": {"duration": duration_us, "start": start_us},
        "tracked_color_effects": [], "uniform_scale": {"on": True, "value": 1.0},
        "visible": True, "volume": volume, "extra_info": None,
    }
    tr = _track_of(content, track_type)
    tr["segments"].append(segment)
    tr["segments"].sort(key=lambda s: s["target_timerange"]["start"])
    content["duration"] = max(
        [s["target_timerange"]["start"] + s["target_timerange"]["duration"]
         for t2 in content["tracks"] for s in t2.get("segments", [])] or [0])
    content["last_modified"] = int(time.time())
    content["update_time"] = int(time.time())
    cf.write_text(json.dumps(content, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"added": {"material_id": mid, "type": ctype, "start_us": start_us,
                      "duration_us": duration_us}, "new_duration_us": content["duration"]}


def add_text_clip(name: str, text: str, start_us: int, duration_us: int,
                  font_size: int = 20, color: str = "#FFFFFF") -> dict:
    d = _draft_dir(name)
    cf = d / "draft_content.json"
    if not _is_plain_json(cf):
        raise ValueError("该草稿已加密，无法用本工具修改")
    content = json.loads(cf.read_text(encoding="utf-8"))
    mid = _uuid()
    content["materials"]["texts"].append({
        "alignment": "1", "background_color": "", "background_style": "", "bold_width": 0.0,
        "border_color": "", "border_width": 0.0, "box_crop": None, "content": text,
        "fonts": [], "force_apply_line_max_width": False, "global_alpha": 1.0,
        "gradient_colors": "", "has_shadow": False, "id": mid, "letter_spacing": 0.0,
        "line_feed": 1, "line_spacing": 0.02, "multi_language_current": "none",
        "shadow_color": "", "shadow_alpha": 0.9, "shadow_angle": -45.0, "shadow_distance": 15.0,
        "shadow_point": {"x": 0.0, "y": 0.0}, "shadow_smoothing": 0.58, "size": float(font_size),
        "strikeout": False, "text_color": color.replace("#", ""), "text_preset_resource_id": "",
        "text_size": 8.0, "tilling_offset": {"x": 0.0, "y": 0.0}, "type": "subtitle",
        "underline": False, "underline_color": "", "underline_offset": 0.22,
        "underline_width": 0.08, "use_gradient_color": False,
    })
    segment = {
        "cartoon": False, "clip": _clip_transform(), "common_keyframes": [],
        "enable_adjust": False, "enable_color_curves": False, "enable_color_wheels": False,
        "enable_lut": False, "extra_material_refs": [], "flag": 0, "group_id": "",
        "id": _uuid(), "keyframes": _keyframes(), "material_id": mid,
        "render_index": 14000, "responsive_layout": {"enable": False, "horizontal_pos": 0.0,
        "horizontal_scale": 1.0, "ratio": 0.0, "vertical_pos": 0.0, "vertical_scale": 1.0},
        "reverse": False, "source_timerange": None,
        "target_timerange": {"duration": duration_us, "start": start_us},
        "visible": True, "volume": 1.0, "extra_info": None,
    }
    tr = _track_of(content, "text")
    tr["segments"].append(segment)
    tr["segments"].sort(key=lambda s: s["target_timerange"]["start"])
    content["duration"] = max(
        [s["target_timerange"]["start"] + s["target_timerange"]["duration"]
         for t2 in content["tracks"] for s in t2.get("segments", [])] or [0])
    content["last_modified"] = int(time.time())
    content["update_time"] = int(time.time())
    cf.write_text(json.dumps(content, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"added": {"material_id": mid, "type": "text", "text": text,
                      "start_us": start_us, "duration_us": duration_us},
            "new_duration_us": content["duration"]}


def scan_effects() -> dict:
    """扫描全部明文草稿，收集可复用的特效/滤镜/转场/动画模板（含真实 effect_id/resource_id/path）."""
    loc = locate()
    dd = loc.get("draft_dir")
    out = {"filters": [], "video_effects": [], "transitions": [], "material_animations": []}
    if not dd:
        return out
    seen = {k: set() for k in out}
    for d in Path(dd).iterdir():
        if not d.is_dir() or d.name.startswith('.'):
            continue
        cf = d / "draft_content.json"
        if not _is_plain_json(cf):
            continue
        try:
            c = json.loads(cf.read_text(encoding='utf-8'))
        except Exception:
            continue
        mats = c.get("materials") or {}
        for k in out:
            for m in (mats.get(k) or []):
                if not isinstance(m, dict):
                    continue
                sig = (m.get("effect_id") or "") + "|" + (m.get("resource_id") or "") + "|" + (m.get("name") or "")
                if sig in seen[k]:
                    continue
                seen[k].add(sig)
                out[k].append({
                    "from_draft": d.name,
                    "name": m.get("name"),
                    "effect_id": m.get("effect_id"),
                    "resource_id": m.get("resource_id"),
                    "category_name": m.get("category_name"),
                    "path": m.get("path"),
                    "has_local_path": bool(m.get("path") and Path(str(m.get("path"))).exists()),
                    "animations": [a.get("name") for a in (m.get("animations") or []) if isinstance(a, dict)],
                })
    return {"templates": out,
            "counts": {k: len(v) for k, v in out.items()},
            "note": "这些是本地草稿里真实用过的模板；用 jianying_add_effect 的 template 模式可克隆到任意片段。"}


def _find_segment(content, track_type, index=0):
    for t in content.get("tracks", []):
        if t.get("type") == track_type and t.get("segments"):
            segs = sorted(t["segments"], key=lambda s: s["target_timerange"]["start"])
            if index < len(segs):
                return segs[index]
    return None


ADJUST_NAMES = [("brightness", -1.0, 1.0), ("contrast", -1.0, 1.0), ("saturation", -1.0, 1.0),
                ("temperature", -1.0, 1.0), ("tone", -1.0, 1.0), ("highlight", -1.0, 1.0),
                ("shadow", -1.0, 1.0), ("sharpen", -1.0, 1.0), ("fade", 0.0, 1.0),
                ("hue", -1.0, 1.0), ("vignetting", 0.0, 1.0)]


def add_adjust(name: str, track_type: str = "video", segment_index: int = 0,
               brightness: float = 0.0, contrast: float = 0.0, saturation: float = 0.0,
               temperature: float = 0.0, tone: float = 0.0, highlight: float = 0.0,
               shadow: float = 0.0, sharpen: float = 0.0, fade: float = 0.0,
               hue: float = 0.0, vignetting: float = 0.0) -> dict:
    """添加参数调色（“调节”层，无需服务器资源；“滤镜”类特效的前置可用形式）."""
    d = _draft_dir(name)
    cf = d / "draft_content.json"
    if not _is_plain_json(cf):
        raise ValueError("草稿已加密，无法修改")
    content = json.loads(cf.read_text(encoding='utf-8'))
    seg = _find_segment(content, track_type, segment_index)
    if seg is None:
        raise ValueError("目标轨道无片段")
    vals = {"brightness": brightness, "contrast": contrast, "saturation": saturation,
            "temperature": temperature, "tone": tone, "highlight": highlight,
            "shadow": shadow, "sharpen": sharpen, "fade": fade,
            "hue": hue, "vignetting": vignetting}
    params = [{"default_value": 0.0, "max_value": mx, "min_value": mn,
               "name": nm, "value": float(vals[nm])} for nm, mn, mx in ADJUST_NAMES]
    fid = _uuid()
    content["materials"].setdefault("filters", []).append({
        "adjust_params": params, "apply_target_type": 0, "apply_time_range": None,
        "category_id": "", "category_name": "adjust", "effect_id": "", "id": fid,
        "name": "调节", "path": "", "platform": "all", "resource_id": "",
        "type": "filter", "value": 1.0,
    })
    refs = seg.setdefault("extra_material_refs", [])
    if fid not in refs:
        refs.append(fid)
    seg["enable_adjust"] = True
    content["last_modified"] = int(time.time())
    content["update_time"] = int(time.time())
    cf.write_text(json.dumps(content, ensure_ascii=False, indent=1), encoding='utf-8')
    return {"added": "调节(adjust)", "filter_id": fid, "params": {k: v for k, v in vals.items() if v},
            "segment": {"start_us": seg["target_timerange"]["start"],
                        "duration_us": seg["target_timerange"]["duration"]}}


def add_effect_from_template(name: str, template_key: str, kind: str = "filter",
                             track_type: str = "video", segment_index: int = 0) -> dict:
    """从本地草稿里已用过的特效/滤镜/转场克隆一份，挂到目标片段（template_key = name 或 effect_id）."""
    d = _draft_dir(name)
    cf = d / "draft_content.json"
    if not _is_plain_json(cf):
        raise ValueError("草稿已加密，无法修改")
    loc = locate()
    dd = Path(loc["draft_dir"])
    tmpl = None
    for other in dd.iterdir():
        if not other.is_dir() or other.name.startswith('.'):
            continue
        ocf = other / "draft_content.json"
        if not _is_plain_json(ocf):
            continue
        try:
            oc = json.loads(ocf.read_text(encoding='utf-8'))
        except Exception:
            continue
        for m in (oc.get("materials", {}).get(kind) or []):
            if not isinstance(m, dict):
                continue
            key = f"{m.get('name')}|{m.get('effect_id')}|{m.get('resource_id')}"
            if template_key in (m.get("name") or "") or template_key == m.get("effect_id") or template_key in key:
                tmpl = m
                break
        if tmpl:
            break
    if not tmpl:
        raise FileNotFoundError(
            f"本地没有任何草稿用过 {kind} 模板 '{template_key}'。"
            "请先在剪映里手动给某个草稿加一个，再调用本工具克隆（一次性录入，之后可批量复用）。")
    content = json.loads(cf.read_text(encoding='utf-8'))
    seg = _find_segment(content, track_type, segment_index)
    if seg is None:
        raise ValueError("目标轨道无片段")
    new_m = json.loads(json.dumps(tmpl))
    new_m["id"] = _uuid()
    content["materials"].setdefault(kind, []).append(new_m)
    refs = seg.setdefault("extra_material_refs", [])
    refs.append(new_m["id"])
    content["last_modified"] = int(time.time())
    content["update_time"] = int(time.time())
    cf.write_text(json.dumps(content, ensure_ascii=False, indent=1), encoding='utf-8')
    return {"cloned": {"name": new_m.get("name"), "effect_id": new_m.get("effect_id"),
                       "resource_id": new_m.get("resource_id"), "path": new_m.get("path")},
            "kind": kind,
            "resource_available_locally": bool(new_m.get("path") and Path(str(new_m.get("path"))).exists())}


def _meta_enum(kind: str):
    from pyJianYingDraft import metadata as M
    m = {'filter': M.FilterType, 'transition': M.TransitionType,
         'video_effect': M.VideoSceneEffectType, 'face_effect': M.VideoCharacterEffectType,
         'intro': M.IntroType, 'outro': M.OutroType, 'group_anim': M.GroupAnimationType,
         'text_intro': M.TextIntro, 'text_outro': M.TextOutro, 'text_loop': M.TextLoopAnim}
    if kind not in m:
        raise ValueError(f"不支持的 kind={kind}，可选: {list(m)}")
    return m[kind]


def _find_meta(kind: str, name: str):
    enum = _meta_enum(kind)
    exact = fuzzy = None
    for e in enum:
        v = e.value
        nm = getattr(v, 'name', '') or ''
        if nm == name:
            exact = v
            break
        if fuzzy is None and name and name.lower() in nm.lower():
            fuzzy = v
    return exact or fuzzy


def list_catalog(kind: str = 'filter', keyword: str = '', limit: int = 40) -> dict:
    """列出剪映可用的特效/滤镜/转场/动画目录（来自 pyJianYingDraft 元数据）."""
    enum = _meta_enum(kind)
    items = []
    for e in enum:
        v = e.value
        nm = getattr(v, 'name', str(v)) or ''
        if keyword and keyword.lower() not in nm.lower():
            continue
        items.append({'name': nm, 'effect_id': str(getattr(v, 'effect_id', '')),
                      'resource_id': str(getattr(v, 'resource_id', ''))})
        if len(items) >= limit:
            break
    return {'kind': kind, 'keyword': keyword, 'count': len(items), 'items': items}


def add_filter(name: str, filter_name: str, intensity: float = 1.0,
               segment_index: int = 0, track_type: str = 'video') -> dict:
    """加剪映滤镜（需剪映自行下载对应资源包）."""
    meta = _find_meta('filter', filter_name)
    if meta is None:
        raise ValueError(f"找不到滤镜 '{filter_name}'，先用 jianying_list_catalog(kind='filter') 查")
    d = _draft_dir(name)
    cf = d / 'draft_content.json'
    if not _is_plain_json(cf):
        raise ValueError('草稿已加密，无法修改')
    content = json.loads(cf.read_text(encoding='utf-8'))
    seg = _find_segment(content, track_type, segment_index)
    if seg is None:
        raise ValueError('目标轨道无片段')
    fid = _uuid()
    content['materials'].setdefault('filters', []).append({
        'adjust_params': [], 'algorithm_artifact_path': '', 'apply_target_type': 0,
        'bloom_params': None, 'category_id': '', 'category_name': '',
        'color_match_info': {'source_feature_path': '', 'target_feature_path': '',
                             'target_image_path': ''},
        'effect_id': str(meta.effect_id), 'enable_skin_tone_correction': False,
        'exclusion_group': [], 'face_adjust_params': [], 'formula_id': '', 'id': fid,
        'intensity_key': '', 'multi_language_current': '', 'name': meta.name, 'panel_id': '',
        'platform': 'all', 'resource_id': str(meta.resource_id), 'source_platform': 1,
        'sub_type': 'none', 'time_range': None, 'type': 'filter',
        'value': float(intensity), 'version': '',
    })
    seg.setdefault('extra_material_refs', []).append(fid)
    content['last_modified'] = int(time.time())
    content['update_time'] = int(time.time())
    cf.write_text(json.dumps(content, ensure_ascii=False, indent=1), encoding='utf-8')
    return {'added': 'filter', 'name': meta.name, 'effect_id': str(meta.effect_id),
            'resource_id': str(meta.resource_id), 'intensity': intensity, 'filter_id': fid}


def add_transition(name: str, transition_name: str, segment_index: int = 0,
                   duration_ms: int = 500, track_type: str = 'video') -> dict:
    """在指定片段与其后一个片段之间加转场."""
    meta = _find_meta('transition', transition_name)
    if meta is None:
        raise ValueError(f"找不到转场 '{transition_name}'")
    d = _draft_dir(name)
    cf = d / 'draft_content.json'
    if not _is_plain_json(cf):
        raise ValueError('草稿已加密，无法修改')
    content = json.loads(cf.read_text(encoding='utf-8'))
    seg = _find_segment(content, track_type, segment_index)
    if seg is None:
        raise ValueError('目标轨道无片段')
    tid = _uuid()
    content['materials'].setdefault('transitions', []).append({
        'category_id': '', 'category_name': '', 'duration': int(duration_ms * 1000),
        'effect_id': str(meta.effect_id), 'id': tid, 'is_overlap': False,
        'name': meta.name, 'platform': 'all', 'resource_id': str(meta.resource_id),
        'type': 'transition',
    })
    seg.setdefault('extra_material_refs', []).append(tid)
    content['last_modified'] = int(time.time())
    content['update_time'] = int(time.time())
    cf.write_text(json.dumps(content, ensure_ascii=False, indent=1), encoding='utf-8')
    return {'added': 'transition', 'name': meta.name, 'after_segment_index': segment_index,
            'duration_ms': duration_ms, 'effect_id': str(meta.effect_id)}


def add_video_effect(name: str, effect_name: str, segment_index: int = 0,
                     track_type: str = 'video', apply_all: bool = False) -> dict:
    """加画面特效（视频特效，需剪映下载资源包）."""
    meta = _find_meta('video_effect', effect_name)
    if meta is None:
        raise ValueError(f"找不到特效 '{effect_name}'")
    d = _draft_dir(name)
    cf = d / 'draft_content.json'
    if not _is_plain_json(cf):
        raise ValueError('草稿已加密，无法修改')
    content = json.loads(cf.read_text(encoding='utf-8'))
    tr = None
    for t in content.get('tracks', []):
        if t.get('type') == track_type and t.get('segments'):
            tr = t
            break
    if tr is None:
        raise ValueError('目标轨道无片段')
    segs = sorted(tr['segments'], key=lambda s: s['target_timerange']['start'])
    targets = segs if apply_all else ([segs[segment_index]] if segment_index < len(segs) else [])
    if not targets:
        raise ValueError('目标片段不存在')
    made = []
    for seg in targets:
        eid = _uuid()
        content['materials'].setdefault('video_effects', []).append({
            'adjust_params': [], 'apply_target_type': 0, 'apply_time_range': None,
            'category_id': '', 'category_name': '', 'common_keyframes': [],
            'disable_effect_faces': [], 'effect_id': str(meta.effect_id), 'formula_id': '',
            'id': eid, 'name': meta.name, 'platform': 'all', 'render_index': 11000,
            'resource_id': str(meta.resource_id), 'source_platform': 0, 'time_range': None,
            'track_render_index': 0, 'type': 'video_effect', 'value': 1.0, 'version': '',
        })
        seg.setdefault('extra_material_refs', []).append(eid)
        made.append(seg['target_timerange']['start'])
    content['last_modified'] = int(time.time())
    content['update_time'] = int(time.time())
    cf.write_text(json.dumps(content, ensure_ascii=False, indent=1), encoding='utf-8')
    return {'added': 'video_effect', 'name': meta.name, 'effect_id': str(meta.effect_id),
            'resource_id': str(meta.resource_id), 'segments': made}


def _effect_catalog_path():
    loc = locate()
    jy = Path(loc.get("jy_root") or '')
    cache = jy / 'User Data' / 'Cache' / 'effect'
    cands = [p for p in cache.glob('*_effects*') if p.suffix not in ('.md5', '.tag', '.json')]
    cands.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for p in cands:
        try:
            j = json.loads(p.read_bytes().decode('utf-8', 'replace'))
            if isinstance(j, dict) and j.get('data', {}).get('effects'):
                return p
        except Exception:
            continue
    return None


def search_effects(keyword: str = '', limit: int = 30) -> dict:
    """搜剪映本地特效目录（含 1000+ 条目，带 CDN 直链）."""
    p = _effect_catalog_path()
    if not p:
        raise FileNotFoundError('未找到剪映特效目录文件（Cache\\effect\\*_effects*）')
    j = json.loads(p.read_bytes().decode('utf-8', 'replace'))
    items = j.get('data', {}).get('effects') or []
    hit = []
    for it in items:
        nm = it.get('name') or ''
        if keyword and keyword.lower() not in nm.lower():
            continue
        rid = str(it.get('resource_id') or '')
        hit.append({
            'name': nm,
            'effect_id': str(it.get('effect_id') or ''),
            'resource_id': rid,
            'requirements': it.get('requirements'),
            'downloaded': _is_downloaded(str(it.get('effect_id') or ''), rid),
        })
        if len(hit) >= limit:
            break
    return {'catalog': str(p.name), 'total': len(items), 'keyword': keyword,
            'count': len(hit), 'items': hit}


def _cache_effect_root() -> str:
    loc = locate()
    return str(Path(loc.get("jy_root") or '') / 'User Data' / 'Cache' / 'effect')


def _is_downloaded(effect_id: str, resource_id: str) -> bool:
    """剪映缓存目录以 effect_id 命名（滤镜在 artistEffect 下）."""
    root = Path(_cache_effect_root())
    for cand in (effect_id, resource_id):
        if not cand:
            continue
        if (root / cand).exists() or (root.parent / 'artistEffect' / cand).exists():
            return True
    return False


def add_effect_by_id(name: str, effect_id: str, resource_id: str, effect_name: str = '',
                     segment_index: int = 0, track_type: str = 'video',
                     apply_all: bool = False, value: float = 1.0,
                     effect_kind: str = 'video_effect') -> dict:
    """按剪映自己的 effect_id/resource_id 直接加画面特效（资源由剪映自动下载）."""
    d = _draft_dir(name)
    cf = d / 'draft_content.json'
    if not _is_plain_json(cf):
        raise ValueError('草稿已加密，无法修改')
    content = json.loads(cf.read_text(encoding='utf-8'))
    tr = None
    for t in content.get('tracks', []):
        if t.get('type') == track_type and t.get('segments'):
            tr = t
            break
    if tr is None:
        raise ValueError('目标轨道无片段')
    segs = sorted(tr['segments'], key=lambda s: s['target_timerange']['start'])
    targets = segs if apply_all else ([segs[segment_index]] if segment_index < len(segs) else [])
    if not targets:
        raise ValueError('目标片段不存在')
    made = []
    for seg in targets:
        eid = _uuid()
        content['materials'].setdefault('video_effects', []).append({
            'adjust_params': [], 'apply_target_type': 0, 'apply_time_range': None,
            'category_id': '', 'category_name': '', 'common_keyframes': [],
            'disable_effect_faces': [], 'effect_id': str(effect_id), 'formula_id': '',
            'id': eid, 'name': effect_name or str(effect_id), 'platform': 'all',
            'render_index': 11000, 'resource_id': str(resource_id), 'source_platform': 0,
            'time_range': None, 'track_render_index': 0, 'type': effect_kind,
            'value': float(value), 'version': '',
        })
        seg.setdefault('extra_material_refs', []).append(eid)
        made.append(seg['target_timerange']['start'])
    content['last_modified'] = int(time.time())
    content['update_time'] = int(time.time())
    cf.write_text(json.dumps(content, ensure_ascii=False, indent=1), encoding='utf-8')
    return {'added': effect_kind, 'name': effect_name, 'effect_id': str(effect_id),
            'resource_id': str(resource_id), 'segments': made,
            'locally_downloaded': _is_downloaded(str(effect_id), str(resource_id))}


def add_filter_by_id(name: str, filter_effect_id: str, filter_resource_id: str,
                     filter_name: str = '', intensity: float = 1.0,
                     segment_index: int = 0, track_type: str = 'video') -> dict:
    """按剪映自己的滤镜 effect_id/resource_id 直接加滤镜（资源需已在本地或可下载）."""
    d = _draft_dir(name)
    cf = d / 'draft_content.json'
    if not _is_plain_json(cf):
        raise ValueError('草稿已加密，无法修改')
    content = json.loads(cf.read_text(encoding='utf-8'))
    seg = _find_segment(content, track_type, segment_index)
    if seg is None:
        raise ValueError('目标轨道无片段')
    fid = _uuid()
    content['materials'].setdefault('filters', []).append({
        'adjust_params': [], 'algorithm_artifact_path': '', 'apply_target_type': 0,
        'bloom_params': None, 'category_id': '', 'category_name': '',
        'color_match_info': {'source_feature_path': '', 'target_feature_path': '',
                             'target_image_path': ''},
        'effect_id': str(filter_effect_id), 'enable_skin_tone_correction': False,
        'exclusion_group': [], 'face_adjust_params': [], 'formula_id': '', 'id': fid,
        'intensity_key': '', 'multi_language_current': '',
        'name': filter_name or str(filter_effect_id), 'panel_id': '', 'platform': 'all',
        'resource_id': str(filter_resource_id), 'source_platform': 1, 'sub_type': 'none',
        'time_range': None, 'type': 'filter', 'value': float(intensity), 'version': '',
    })
    seg.setdefault('extra_material_refs', []).append(fid)
    content['last_modified'] = int(time.time())
    content['update_time'] = int(time.time())
    cf.write_text(json.dumps(content, ensure_ascii=False, indent=1), encoding='utf-8')
    return {'added': 'filter', 'effect_id': str(filter_effect_id),
            'resource_id': str(filter_resource_id), 'intensity': intensity,
            'filter_id': fid, 'segment_start_us': seg['target_timerange']['start'],
            'resource_local': _is_downloaded(str(filter_effect_id), str(filter_resource_id))}


PANEL_FILES = {
    'effects': 'effects2', 'transitions': 'transitions', 'text_anim': 'text',
    'fonts': 'fonts', 'sticker_anim': 'sticker', 'video_anim': 'video',
    'face_prop': 'face-prop', 'tone': 'tone', 'voice_change': 'voice-change',
    'makeup': 'makeup', 'auto_beauty': 'auto-beauty', 'bubble': 'bubble2',
    'flower': 'flower2', 'split_screen': 'split-screen', 'canvas': 'canvas',
    'videomask': 'videomask', 'text_glow': 'text_glow',
}


def _catalog_entries(panel: str):
    fname = PANEL_FILES.get(panel, panel)
    p = Path(_cache_effect_root()) / f'jianyingpro_0_2.0.0_{fname}'
    if not p.exists():
        raise FileNotFoundError(f'无此面板目录: {panel} -> {p.name}')
    j = json.loads(p.read_bytes().decode('utf-8', 'replace'))
    data = j.get('data') or {}
    arr = None
    for k in ('effects', 'effect_list', 'items', 'list', 'resources', 'resource_list'):
        if isinstance(data.get(k), list):
            arr = data[k]
            break
    if arr is None:
        for v in data.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                arr = v
                break
    cats = [c.get('name') for c in (data.get('category') or []) if isinstance(c, dict)]
    return arr or [], cats


def search_catalog(panel: str = 'effects', keyword: str = '', limit: int = 30) -> dict:
    """通用目录搜索：effects/transitions/text_anim/fonts/sticker_anim/video_anim/face_prop/tone/..."""
    arr, cats = _catalog_entries(panel)
    root = Path(_cache_effect_root())
    items = []
    for it in arr:
        nm = str(it.get('name') or it.get('title') or '')
        if keyword and keyword.lower() not in nm.lower():
            continue
        rid = str(it.get('resource_id') or '')
        eid = str(it.get('effect_id') or it.get('id') or '')
        items.append({
            'name': nm, 'effect_id': eid, 'resource_id': rid,
            'downloaded': bool(rid) and ((root / rid).exists() or (root.parent / 'artistEffect' / rid).exists()),
            'requirements': it.get('requirements'),
        })
        if len(items) >= limit:
            break
    return {'panel': panel, 'total': len(arr), 'categories': cats[:20],
            'keyword': keyword, 'count': len(items), 'items': items}


def add_transition_by_id(name: str, effect_id: str, resource_id: str,
                         transition_name: str = '', segment_index: int = 0,
                         duration_ms: int = 500, track_type: str = 'video') -> dict:
    d = _draft_dir(name)
    cf = d / 'draft_content.json'
    if not _is_plain_json(cf):
        raise ValueError('草稿已加密，无法修改')
    content = json.loads(cf.read_text(encoding='utf-8'))
    seg = _find_segment(content, track_type, segment_index)
    if seg is None:
        raise ValueError('目标轨道无片段')
    tid = _uuid()
    content['materials'].setdefault('transitions', []).append({
        'category_id': '', 'category_name': '', 'duration': int(duration_ms * 1000),
        'effect_id': str(effect_id), 'id': tid, 'is_overlap': False,
        'name': transition_name or str(effect_id), 'platform': 'all',
        'resource_id': str(resource_id), 'type': 'transition',
    })
    seg.setdefault('extra_material_refs', []).append(tid)
    content['last_modified'] = int(time.time())
    content['update_time'] = int(time.time())
    cf.write_text(json.dumps(content, ensure_ascii=False, indent=1), encoding='utf-8')
    return {'added': 'transition', 'name': transition_name, 'effect_id': str(effect_id),
            'after_segment': segment_index, 'duration_ms': duration_ms}


def add_animation_by_id(name: str, effect_id: str, resource_id: str, anim_type: str = 'in',
                        anim_name: str = '', target_kind: str = 'text',
                        segment_index: int = 0, duration_us: int = 500000) -> dict:
    """给文本/视频片段加入场/出场/循环/组合动画（material_animations）."""
    if anim_type not in ('in', 'out', 'loop', 'group'):
        raise ValueError('anim_type 必须是 in/out/loop/group')
    track_type = 'text' if target_kind == 'text' else 'video'
    d = _draft_dir(name)
    cf = d / 'draft_content.json'
    if not _is_plain_json(cf):
        raise ValueError('草稿已加密，无法修改')
    content = json.loads(cf.read_text(encoding='utf-8'))
    seg = _find_segment(content, track_type, segment_index)
    if seg is None:
        raise ValueError(f'{track_type} 轨道无片段')
    mtype = 'sticker_animation' if target_kind == 'text' else 'video_animation'
    refs = seg.setdefault('extra_material_refs', [])
    holder = None
    for mid in refs:
        for m in (content['materials'].get('material_animations') or []):
            if m.get('id') == mid and m.get('type') == mtype:
                holder = m
                break
        if holder:
            break
    if holder is None:
        holder = {'id': _uuid(), 'type': mtype, 'animations': [],
                  'multi_language_current': 'none'}
        content['materials'].setdefault('material_animations', []).append(holder)
        refs.append(holder['id'])
    anims = [a for a in (holder.get('animations') or []) if a.get('type') != anim_type]
    anims.append({
        'anim_adjust_params': None, 'platform': 'all',
        'panel': 'video' if target_kind != 'text' else '',
        'material_type': 'video' if target_kind != 'text' else 'sticker',
        'name': anim_name or str(effect_id), 'id': str(effect_id), 'type': anim_type,
        'resource_id': str(resource_id), 'start': 0, 'duration': int(duration_us),
    })
    holder['animations'] = anims
    content['last_modified'] = int(time.time())
    content['update_time'] = int(time.time())
    cf.write_text(json.dumps(content, ensure_ascii=False, indent=1), encoding='utf-8')
    return {'added': 'animation', 'type': anim_type, 'name': anim_name,
            'effect_id': str(effect_id), 'material_animations_id': holder['id'],
            'target': f'{track_type}[{segment_index}]'}


def set_text_font(name: str, font_resource_id: str, font_path: str = '',
                  font_name: str = '', text_segment_index: int = 0) -> dict:
    """给文本片段设置字体（写 4.3.1 文本素材的 font_* 顶层字段）."""
    d = _draft_dir(name)
    cf = d / 'draft_content.json'
    if not _is_plain_json(cf):
        raise ValueError('草稿已加密，无法修改')
    content = json.loads(cf.read_text(encoding='utf-8'))
    seg = _find_segment(content, 'text', text_segment_index)
    if seg is None:
        raise ValueError('文本轨道无片段')
    mid = seg.get('material_id')
    target = None
    for m in (content['materials'].get('texts') or []):
        if m.get('id') == mid:
            target = m
            break
    if target is None:
        raise ValueError('找不到文本素材')
    target['font_id'] = str(font_resource_id)
    target['font_resource_id'] = str(font_resource_id)
    if font_name:
        target['font_name'] = font_name
        target['font_title'] = font_name
    if font_path:
        target['font_path'] = font_path
    target['font_source_platform'] = 1
    content['last_modified'] = int(time.time())
    content['update_time'] = int(time.time())
    cf.write_text(json.dumps(content, ensure_ascii=False, indent=1), encoding='utf-8')
    return {'set_font': str(font_resource_id), 'font_name': font_name,
            'font_path': font_path or '(未提供)', 'text_segment': text_segment_index}


def set_text_style(name: str, text_segment_index: int = 0, font_size: float = None,
                   text_color: str = None, bold_width: float = None,
                   italic: bool = None, underline: bool = None,
                   alignment: int = None, letter_spacing: float = None,
                   line_spacing: float = None, background_color: str = None,
                   background_alpha: float = None, stroke_color: str = None,
                   stroke_width: float = None) -> dict:
    """设置 4.3.1 文本素材的样式字段（字号/颜色/粗体/斜体/下划线/对齐/行距/背景）."""
    d = _draft_dir(name)
    cf = d / 'draft_content.json'
    if not _is_plain_json(cf):
        raise ValueError('草稿已加密，无法修改')
    content = json.loads(cf.read_text(encoding='utf-8'))
    seg = _find_segment(content, 'text', text_segment_index)
    if seg is None:
        raise ValueError('文本轨道无片段')
    mid = seg.get('material_id')
    target = None
    for m in (content['materials'].get('texts') or []):
        if m.get('id') == mid:
            target = m
            break
    if target is None:
        raise ValueError('找不到文本素材')
    applied = {}
    if font_size is not None:
        target['font_size'] = float(font_size)
        target['text_size'] = target.get('text_size', 8)
        applied['font_size'] = font_size
    if text_color is not None:
        target['text_color'] = text_color.lstrip('#')
        applied['text_color'] = target['text_color']
    if bold_width is not None:
        target['bold_width'] = float(bold_width)
        applied['bold_width'] = bold_width
    if italic is not None:
        target['italic_degree'] = 1 if italic else 0
        applied['italic'] = italic
    if underline is not None:
        target['underline'] = bool(underline)
        applied['underline'] = underline
    if alignment is not None:
        target['alignment'] = int(alignment)
        applied['alignment'] = alignment
    if letter_spacing is not None:
        target['letter_spacing'] = float(letter_spacing)
        applied['letter_spacing'] = letter_spacing
    if line_spacing is not None:
        target['line_spacing'] = float(line_spacing)
        applied['line_spacing'] = line_spacing
    if background_color is not None:
        target['background_color'] = background_color.lstrip('#')
        applied['background_color'] = target['background_color']
    if background_alpha is not None:
        target['background_alpha'] = float(background_alpha)
        applied['background_alpha'] = background_alpha
    if stroke_color is not None:
        target['border_color'] = stroke_color.lstrip('#')
        applied['border_color'] = target['border_color']
    if stroke_width is not None:
        target['border_width'] = float(stroke_width)
        applied['border_width'] = stroke_width
    content['last_modified'] = int(time.time())
    content['update_time'] = int(time.time())
    cf.write_text(json.dumps(content, ensure_ascii=False, indent=1), encoding='utf-8')
    return {'text_segment': text_segment_index, 'applied': applied}


def _catalog_record(panel: str, keyword: str):
    arr, _cats = _catalog_entries(panel)
    for it in arr:
        nm = str(it.get('name') or '')
        if keyword and (keyword == nm or keyword in nm):
            return it
    for it in arr:
        if str(it.get('effect_id')) == keyword or str(it.get('resource_id')) == keyword:
            return it
    return None


def _download_unzip(url: str, dest: Path) -> dict:
    import urllib.request
    import zipfile
    import io
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=180) as r:
        blob = r.read()
    if not blob.startswith(b'PK'):
        return {'ok': False, 'reason': f'不是 zip（{blob[:8]!r}）', 'bytes': len(blob)}
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        z.extractall(dest)
    return {'ok': True, 'bytes': len(blob), 'entries': len(z.namelist())}


def prefetch_package(panel: str = 'effects', keyword: str = '', effect_id: str = '',
                     resource_id: str = '') -> dict:
    """从剪映 CDN 直接下包并解到本地缓存，使剪映认为已下载."""
    rec = None
    key = effect_id or resource_id or keyword
    if key:
        rec = _catalog_record(panel, key)
    if rec is None:
        raise ValueError(f'目录中找不到: {key}')
    eid = str(rec.get('effect_id') or '')
    rid = str(rec.get('resource_id') or '')
    et = int(rec.get('effect_type') or 0)
    md5 = str(rec.get('id') or '')
    fu = rec.get('file_url') or {}
    urls = fu.get('url_list') or []
    if not urls:
        raise ValueError('该条目没有 file_url 直链')
    root = Path(_cache_effect_root())
    base = root.parent / 'artistEffect' / eid if et == 12 else root / eid
    if md5 and (base / md5).exists():
        return {'ok': True, 'skipped': '已存在', 'path': str(base)}
    res = _download_unzip(urls[0], base / md5 if md5 else base)
    if not res['ok']:
        return {'ok': False, 'error': res}
    if et == 12:
        cache = {
            'collection': {'resource_id_list': []},
            'common_attr': {'cover_url': {'small': '', 'static_img': ''},
                            'effect_id': eid, 'effect_type': 12, 'id': '',
                            'item_urls': urls, 'md5': md5},
            'text_template': {'depend_resource_list': []},
            'video': {'duration': 0,
                      'origin_video': {'definition': '', 'format': '', 'height': 0,
                                       'size': 0, 'video_url': '', 'width': 0},
                      'transcoded_video': {}},
        }
    else:
        cache = {'effect_id': eid,
                 'file_url': {'uri': md5, 'url_list': urls},
                 'model_names': rec.get('model_names') or ''}
    (base / 'cache.json').write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding='utf-8')
    return {'ok': True, 'name': rec.get('name'), 'effect_id': eid, 'resource_id': rid,
            'effect_type': et, 'bytes': res['bytes'], 'entries': res['entries'],
            'path': str(base)}


# --------------------------------------------------------------------------
# 打开草稿
# --------------------------------------------------------------------------
def open_draft(name: str) -> dict:
    loc = locate()
    d = _draft_dir(name)
    exe = None
    # 优先外层启动器（单实例分发，能复用已开窗口）
    outer = Path(loc.get("jy_root", "")) / "Apps" / "JianyingPro.exe"
    if outer.exists():
        exe = str(outer)
    elif loc.get("app_exe"):
        exe = loc["app_exe"]
    if not exe:
        return {"ok": False,
                "error": "未检测到已安装的剪映（Apps 目录无 JianyingPro.exe）。"
                         "注册表显示 last_checked_version=%s uninstallTime=%s。"
                         "请先安装剪映（注意：6.x 会加密草稿；4.3.1 使用明文草稿）。" % (
                             loc["registry"].get("last_checked_version", "?"),
                             loc["registry"].get("uninstallTime", "?"))}
    import subprocess
    # 已有实例在跑时不要再拉起（4.3.1 的单实例交接会崩（实测 dump）），直接提示
    running = None
    try:
        out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq JianyingPro.exe', '/FO', 'CSV', '/NH'],
                             capture_output=True, text=True, timeout=10).stdout
        if 'JianyingPro.exe' in out:
            running = out.strip().splitlines()[0]
    except Exception:
        pass
    if running:
        return {"ok": True, "already_running": True,
                "draft": str(d),
                "note": "剪映已在运行，未重复启动（旧版本单实例交接会崩）。请在剪映草稿列表中打开该草稿。"}
    # 关键：必须借 explorer.exe 拉起。直接从本进程 CreateProcess 启动会触发
    # metasecml.dll（安全 SDK）崩溃（实测 dump：metasecml+0x46dadd 读 0x10），
    # explorer 作父进程等价于用户双击，可正常启动。
    subprocess.Popen(['explorer.exe', exe, str(d)])
    return {"ok": True, "launched": exe, "draft": str(d), "via": "explorer.exe"}


# --------------------------------------------------------------------------
# MCP 工具清单
# --------------------------------------------------------------------------
TOOLS = [
    {"name": "jianying_locate",
     "description": "定位剪映专业版安装与草稿目录。复刻剪映自身定位逻辑：注册表 currentCustomDraftPath → INI globalSetting → 默认路径；返回安装状态、版本、卸载时间。",
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
    {"name": "jianying_list_drafts",
     "description": "列出所有草稿：名称、创建/最后编辑时间、内容是否加密、大小、封面。按修改时间倒序。",
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
    {"name": "jianying_get_draft_detail",
     "description": "草稿详情：明文侧文件内容（key_value/draft_virtual_store/attachment_pc_common/draft_agency_config）、draft_settings、（若明文）时间线摘要。",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string", "description": "草稿名（即文件夹名）"}},
                     "required": ["name"]}},
    {"name": "jianying_read_side_file",
     "description": "读取草稿内某个明文侧文件的原始内容。",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"},
                     "file": {"type": "string", "enum": ["key_value.json", "draft_virtual_store.json",
                              "attachment_pc_common.json", "draft_agency_config.json", "draft_settings"]}},
                     "required": ["name", "file"]}},
    {"name": "jianying_backup_draft",
     "description": "把草稿文件夹打包 zip 备份到 _mcp_backups 目录（或指定路径），返回备份文件路径。",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"},
                     "dest": {"type": "string", "description": "可选：备份目录"}}, "required": ["name"]}},
    {"name": "jianying_create_draft",
     "description": "新建明文格式草稿（4.x/5.x 兼容格式，配合 4.3.1 等明文草稿版本使用）。注意：6.x 剪映期望加密草稿。",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"},
                     "width": {"type": "integer", "default": 1920},
                     "height": {"type": "integer", "default": 1080},
                     "fps": {"type": "integer", "default": 30}}, "required": ["name"]}},
    {"name": "jianying_add_media_clip",
     "description": "向明文草稿添加视频/音频片段（自动备份原文件）。时间单位微秒。",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"}, "media_path": {"type": "string"},
         "start_us": {"type": "integer"}, "duration_us": {"type": "integer"},
         "ctype": {"type": "string", "enum": ["video", "audio"], "default": "video"},
         "source_start_us": {"type": "integer", "default": 0,
                            "description": "源素材入点（微秒），用于从长素材中挑高光片段"},
         "volume": {"type": "number", "default": 1.0}},
         "required": ["name", "media_path", "start_us", "duration_us"]}},
    {"name": "jianying_add_text_clip",
     "description": "向明文草稿添加文本字幕片段（自动备份原文件）。",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"}, "text": {"type": "string"},
         "start_us": {"type": "integer"}, "duration_us": {"type": "integer"},
         "font_size": {"type": "integer", "default": 20},
         "color": {"type": "string", "default": "#FFFFFF"}},
         "required": ["name", "text", "start_us", "duration_us"]}},
    {"name": "jianying_scan_effects",
     "description": "扫描本地全部明文草稿，列出已用过的真实特效/滤镜/转场/动画模板（含 effect_id / resource_id / 本地路径），作为可克隆模板库。",
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
    {"name": "jianying_add_adjust",
     "description": "给片段加参数调色（调节层：亮度/对比度/饱和度/色温/色调/高光/阴影/锐化/褪色），无需服务器资源，纯本地可用。",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"},
         "track_type": {"type": "string", "enum": ["video"], "default": "video"},
         "segment_index": {"type": "integer", "default": 0},
         "brightness": {"type": "number", "default": 0},
         "contrast": {"type": "number", "default": 0},
         "saturation": {"type": "number", "default": 0},
         "temperature": {"type": "number", "default": 0},
         "tone": {"type": "number", "default": 0},
         "highlight": {"type": "number", "default": 0},
         "shadow": {"type": "number", "default": 0},
         "sharpen": {"type": "number", "default": 0},
         "fade": {"type": "number", "default": 0},
         "hue": {"type": "number", "default": 0},
         "vignetting": {"type": "number", "default": 0}},
         "required": ["name"]}},
    {"name": "jianying_add_effect_from_template",
     "description": "从本地草稿里已用过的特效/滤镜/转场模板克隆一份，挂到目标片段。先跑 jianying_scan_effects 看有哪些模板；本地没有时需用户在剪映里手动加一次。",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"},
         "template_key": {"type": "string", "description": "模板名或 effect_id"},
         "kind": {"type": "string", "enum": ["filter", "video_effects", "transitions"], "default": "filter"},
         "track_type": {"type": "string", "default": "video"},
         "segment_index": {"type": "integer", "default": 0}},
         "required": ["name", "template_key"]}},
    {"name": "jianying_list_catalog",
     "description": "查剪映特效目录（kind: filter 滤镜 / transition 转场 / video_effect 画面特效 / face_effect 人物特效 / intro 入场动画 / outro 出场动画 / group_anim 组合动画 / text_intro 文字入场等），支持 keyword 过滤。",
     "inputSchema": {"type": "object", "properties": {
         "kind": {"type": "string", "default": "filter"},
         "keyword": {"type": "string", "default": ""},
         "limit": {"type": "integer", "default": 40}},
         "required": []}},
    {"name": "jianying_add_filter",
     "description": "给片段加剪映滤镜（资源由剪映按 resource_id 自动下载）。",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"}, "filter_name": {"type": "string"},
         "intensity": {"type": "number", "default": 1.0},
         "segment_index": {"type": "integer", "default": 0}},
         "required": ["name", "filter_name"]}},
    {"name": "jianying_add_transition",
     "description": "在指定片段与其后片段之间加转场。",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"}, "transition_name": {"type": "string"},
         "segment_index": {"type": "integer", "default": 0},
         "duration_ms": {"type": "integer", "default": 500}},
         "required": ["name", "transition_name"]}},
    {"name": "jianying_add_video_effect",
     "description": "给片段加画面特效（apply_all=true 则给整条视频轨所有片段都加）。",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"}, "effect_name": {"type": "string"},
         "segment_index": {"type": "integer", "default": 0},
         "apply_all": {"type": "boolean", "default": False}},
         "required": ["name", "effect_name"]}},
    {"name": "jianying_search_effects",
     "description": "搜剪映本地特效目录（1000+ 条，含 effect_id/resource_id/是否需要抠像/是否已下载）。",
     "inputSchema": {"type": "object", "properties": {
         "keyword": {"type": "string", "default": ""},
         "limit": {"type": "integer", "default": 30}},
         "required": []}},
    {"name": "jianying_add_effect_by_id",
     "description": "按 effect_id + resource_id 直接给片段加画面特效（资源由剪映自动下载）。",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"}, "effect_id": {"type": "string"},
         "resource_id": {"type": "string"}, "effect_name": {"type": "string", "default": ""},
         "segment_index": {"type": "integer", "default": 0},
         "apply_all": {"type": "boolean", "default": False},
         "value": {"type": "number", "default": 1.0},
         "effect_kind": {"type": "string", "enum": ["video_effect", "face_effect"],
                         "default": "video_effect",
                         "description": "face_effect = 人物特效"}},
         "required": ["name", "effect_id", "resource_id"]}},
    {"name": "jianying_add_filter_by_id",
     "description": "按剪映自己的滤镜 effect_id + resource_id 加滤镜（本地已下载的滤镜优先）。",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"}, "filter_effect_id": {"type": "string"},
         "filter_resource_id": {"type": "string"}, "filter_name": {"type": "string", "default": ""},
         "intensity": {"type": "number", "default": 1.0},
         "segment_index": {"type": "integer", "default": 0}},
         "required": ["name", "filter_effect_id", "filter_resource_id"]}},
    {"name": "jianying_search_catalog",
     "description": "通用目录搜索。panel: effects(1120画面特效)/transitions(523转场)/text_anim(353文字动画)/fonts(1272字体)/sticker_anim(34贴纸动画)/video_anim(331视频动画)/face_prop(260人物特效)/tone(185音色)/voice_change(37变声)/makeup/auto_beauty/bubble/flower/split_screen/canvas。",
     "inputSchema": {"type": "object", "properties": {
         "panel": {"type": "string", "default": "effects"},
         "keyword": {"type": "string", "default": ""},
         "limit": {"type": "integer", "default": 30}},
         "required": []}},
    {"name": "jianying_add_transition_by_id",
     "description": "按 effect_id + resource_id 在指定片段与其后片段之间加转场。",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"}, "effect_id": {"type": "string"},
         "resource_id": {"type": "string"},
         "transition_name": {"type": "string", "default": ""},
         "segment_index": {"type": "integer", "default": 0},
         "duration_ms": {"type": "integer", "default": 500}},
         "required": ["name", "effect_id", "resource_id"]}},
    {"name": "jianying_add_animation_by_id",
     "description": "给文本或视频片段加入场/出场/循环/组合动画（target_kind: text|video；anim_type: in|out|loop|group）。",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"}, "effect_id": {"type": "string"},
         "resource_id": {"type": "string"},
         "anim_type": {"type": "string", "enum": ["in", "out", "loop", "group"], "default": "in"},
         "anim_name": {"type": "string", "default": ""},
         "target_kind": {"type": "string", "enum": ["text", "video"], "default": "text"},
         "segment_index": {"type": "integer", "default": 0},
         "duration_us": {"type": "integer", "default": 500000}},
         "required": ["name", "effect_id", "resource_id"]}},
    {"name": "jianying_set_text_font",
     "description": "给文本片段设置字体（写 4.3.1 的 font_id/font_name/font_path/font_resource_id 字段）。",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"}, "font_resource_id": {"type": "string"},
         "font_path": {"type": "string", "default": ""},
         "font_name": {"type": "string", "default": ""},
         "text_segment_index": {"type": "integer", "default": 0}},
         "required": ["name", "font_resource_id"]}},
    {"name": "jianying_set_text_style",
     "description": "设置文本样式（字号/颜色/粗体/斜体/下划线/对齐/行距/字距/背景/描边）——按 4.3.1 真实字段名。",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"},
         "text_segment_index": {"type": "integer", "default": 0},
         "font_size": {"type": "number"},
         "text_color": {"type": "string"},
         "bold_width": {"type": "number"},
         "italic": {"type": "boolean"},
         "underline": {"type": "boolean"},
         "alignment": {"type": "integer"},
         "letter_spacing": {"type": "number"},
         "line_spacing": {"type": "number"},
         "background_color": {"type": "string"},
         "background_alpha": {"type": "number"},
         "stroke_color": {"type": "string"},
         "stroke_width": {"type": "number"}},
         "required": ["name"]}},
    {"name": "jianying_prefetch_package",
     "description": "直接从剪映 CDN 下特效/转场/滤镜包并解到本地缓存（让剪映以为已下载）。可用 effect_id / resource_id / 名称 指定。",
     "inputSchema": {"type": "object", "properties": {
         "panel": {"type": "string", "default": "effects"},
         "keyword": {"type": "string", "default": ""},
         "effect_id": {"type": "string", "default": ""},
         "resource_id": {"type": "string", "default": ""}},
         "required": []}},
    {"name": "jianying_open_draft",
     "description": "在剪映专业版中打开草稿（需已安装剪映）。",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},

    # ---------- 面板级参数（画面 / 音频 / 变速 / 调节）----------
    {"name": "jianying_get_segment_panel",
     "description": "读取某个片段在面板上的全部真实参数：位置/缩放/旋转/翻转/透明度、等比缩放、音量、变速、倒放、可见、层级、抠像、防抖降噪、挂载的特效与动画。字段名按剪映 4.3.1 实际 schema。",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"},
         "track_type": {"type": "string", "default": "video"},
         "segment_index": {"type": "integer", "default": 0}},
         "required": ["name"]}},

    {"name": "jianying_set_clip_transform",
     "description": "画面-基础：设置位置(x/y)、缩放(scale_x/scale_y 或 uniform_scale)、旋转、水平/垂直翻转、透明度(alpha)、等比缩放开关。写 segment.clip 与 uniform_scale。",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"},
         "track_type": {"type": "string", "default": "video"},
         "segment_index": {"type": "integer", "default": 0},
         "x": {"type": "number"}, "y": {"type": "number"},
         "scale_x": {"type": "number"}, "scale_y": {"type": "number"},
         "uniform_scale": {"type": "number"},
         "lock_ratio": {"type": "boolean"},
         "rotation": {"type": "number"},
         "flip_h": {"type": "boolean"}, "flip_v": {"type": "boolean"},
         "alpha": {"type": "number"}},
         "required": ["name"]}},

    {"name": "jianying_set_segment_speed",
     "description": "变速：设置倍速(0.1~100)、倒放、变速曲线。会同步修正 source_timerange 时长（target 不变，source = target × speed），并更新关联的 speeds 素材。",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"},
         "track_type": {"type": "string", "default": "video"},
         "segment_index": {"type": "integer", "default": 0},
         "speed": {"type": "number"},
         "reverse": {"type": "boolean"},
         "curve_points": {"type": "array", "items": {"type": "object"},
                          "description": "曲线变速点，直写 speeds.curve_speed"}},
         "required": ["name"]}},

    {"name": "jianying_set_segment_audio",
     "description": "音频：设置片段音量、音频增强(intensifies_audio)、淡入淡出(audio_fade，写在关联视频素材上)。",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"},
         "track_type": {"type": "string", "default": "video"},
         "segment_index": {"type": "integer", "default": 0},
         "volume": {"type": "number"},
         "intensifies_audio": {"type": "boolean"},
         "fade_in_us": {"type": "integer"},
         "fade_out_us": {"type": "integer"}},
         "required": ["name"]}},

    {"name": "jianying_set_segment_layer",
     "description": "层级与显隐：visible 开关、render_index 渲染层级（数值越大越靠上）。",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"},
         "track_type": {"type": "string", "default": "video"},
         "segment_index": {"type": "integer", "default": 0},
         "visible": {"type": "boolean"},
         "render_index": {"type": "integer"}},
         "required": ["name"]}},

    {"name": "jianying_set_matting",
     "description": "画面-抠像：开关抠像并设置类型（智能抠像 flag=1 / 自定义抠像 path / 清除画笔 strokes）。写 videos[].matting。注：智能抠像的遮罩由剪映引擎生成，本工具只写入开关与参数。",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"},
         "track_type": {"type": "string", "default": "video"},
         "segment_index": {"type": "integer", "default": 0},
         "enable": {"type": "boolean", "default": True},
         "flag": {"type": "integer", "description": "0=关闭 1=智能抠像"},
         "path": {"type": "string"},
         "clear_strokes": {"type": "boolean", "default": False}},
         "required": ["name"]}},

    {"name": "jianying_set_video_algorithm",
     "description": "画面-防抖/降噪/去频闪/运动模糊：写 videos[].video_algorithm 与 stable。",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"},
         "track_type": {"type": "string", "default": "video"},
         "segment_index": {"type": "integer", "default": 0},
         "stable": {"type": "boolean"},
         "denoise": {"type": "boolean"},
         "deflicker": {"type": "boolean"},
         "motion_blur": {"type": "boolean"}},
         "required": ["name"]}},

    {"name": "jianying_list_segment_effects",
     "description": "列出某片段挂载的所有特效（对应面板「特效：名称1/名称2」），含 effect_id / resource_id / type / value 与所属索引，便于逐个删除或改强度。",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"},
         "track_type": {"type": "string", "default": "video"},
         "segment_index": {"type": "integer", "default": 0}},
         "required": ["name"]}},

    {"name": "jianying_remove_segment_effect",
     "description": "从片段上移除一个特效（按 list_segment_effects 返回的索引），同时清掉 extra_material_refs 引用与空悬素材。",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"},
         "track_type": {"type": "string", "default": "video"},
         "segment_index": {"type": "integer", "default": 0},
         "effect_index": {"type": "integer", "default": 0}},
         "required": ["name"]}},

    {"name": "jianying_set_adjust_values",
     "description": "调节：修改片段已有的调色参数（亮度/对比度/饱和度/色温/色调/高光/阴影/锐化/褪色/色相/暗角）。会定位草稿中已存在的 adjust 载体（materials.video_effects 或 filters 的 adjust_params）并就地改值；若草稿里没有载体则报错提示，不凭空创建未验证结构。",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"},
         "track_type": {"type": "string", "default": "video"},
         "segment_index": {"type": "integer", "default": 0},
         "brightness": {"type": "number"}, "contrast": {"type": "number"},
         "saturation": {"type": "number"}, "temperature": {"type": "number"},
         "tone": {"type": "number"}, "highlight": {"type": "number"},
         "shadow": {"type": "number"}, "sharpen": {"type": "number"},
         "fade": {"type": "number"}, "hue": {"type": "number"},
         "vignetting": {"type": "number"}},
         "required": ["name"]}},
    # ---------- 第二批：裁剪 / 调色开关 / HDR / 关键帧 / 文字背景 / 分组 / 标志位 ----------
    {"name": "jianying_set_crop",
     "description": "画面-裁剪：设置裁剪四角点（归一化 0~1）与裁剪比例/缩放，或 reset 回满幅。写 videos[].crop 与 crop_ratio/crop_scale。",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"},
         "track_type": {"type": "string", "default": "video"},
         "segment_index": {"type": "integer", "default": 0},
         "mode": {"type": "string", "default": "reset", "description": "reset=取消裁剪；rect=按四角点裁剪"},
         "x1": {"type": "number", "description": "左上 x，0~1"},
         "y1": {"type": "number", "description": "左上 y，0~1"},
         "x2": {"type": "number", "description": "右下 x，0~1"},
         "y2": {"type": "number", "description": "右下 y，0~1"},
         "ratio": {"type": "string", "default": "free"},
         "scale": {"type": "number"}},
         "required": ["name"]}},

    {"name": "jianying_set_color_switches",
     "description": "调色开关：enable_adjust / enable_lut / enable_color_curves / enable_color_wheels / enable_smart_color_adjust 五个布尔开关。",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"},
         "track_type": {"type": "string", "default": "video"},
         "segment_index": {"type": "integer", "default": 0},
         "enable_adjust": {"type": "boolean"},
         "enable_lut": {"type": "boolean"},
         "enable_color_curves": {"type": "boolean"},
         "enable_color_wheels": {"type": "boolean"},
         "enable_smart_color_adjust": {"type": "boolean"}},
         "required": ["name"]}},

    {"name": "jianying_set_hdr",
     "description": "HDR 设置：写 segment.hdr_settings（enabled/convert_mode/intensity/nits）。二进制里确认存在 hdr_settings 与 hdrSetting{ConvertMode,Intensity,Nits,Enabled}，但本机无已启用 HDR 的草稿样本，字段组合未经真实样本验证。",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"},
         "track_type": {"type": "string", "default": "video"},
         "segment_index": {"type": "integer", "default": 0},
         "enabled": {"type": "boolean"},
         "convert_mode": {"type": "integer", "description": "0=HDR2SDR 1=SDR2HDR"},
         "intensity": {"type": "number", "default": 1.0},
         "nits": {"type": "integer"},
         "reset": {"type": "boolean", "default": False}},
         "required": ["name"]}},

    {"name": "jianying_set_keyframes",
     "description": "关键帧：给片段某属性打关键帧（位置/缩放/旋转/透明度/音量等）。写 segment.common_keyframes 与 keyframe_refs，支持曲线与清空。字段名已在 4.3.1 二进制确认（common_keyframes/keyframe_list/time_offset/property_type/curve_type），属性编号未用真实样本验证。",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"},
         "track_type": {"type": "string", "default": "video"},
         "segment_index": {"type": "integer", "default": 0},
         "property_type": {"type": "integer", "description": "属性编号：0=位置X 1=位置Y 2=缩放 3=旋转 4=透明度 5=音量（待验证）"},
         "points": {"type": "array", "description": "关键帧点：[[距片段起点微秒, 值], ...]",
                    "items": {"type": "array", "items": {"type": "number"}}},
         "curve": {"type": "string", "default": "Line"},
         "clear": {"type": "boolean", "default": False}},
         "required": ["name"]}},

    {"name": "jianying_set_text_background",
     "description": "文字-背景块：颜色/透明度/宽高/圆角/水平与垂直偏移/背景样式。写 materials.texts[] 的 background_* 字段。",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"},
         "text_segment_index": {"type": "integer", "default": 0},
         "background_color": {"type": "string"},
         "background_alpha": {"type": "number"},
         "background_width": {"type": "number"},
         "background_height": {"type": "number"},
         "background_round_radius": {"type": "number"},
         "background_horizontal_offset": {"type": "number"},
         "background_vertical_offset": {"type": "number"},
         "background_style": {"type": "integer"}},
         "required": ["name"]}},

    {"name": "jianying_group_segments",
     "description": "成组/解组：给多个片段写同一个 group_id（成组），或 clear=true 解除。成组后片段在时间线上一起移动。",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"},
         "track_type": {"type": "string", "default": "video"},
         "segment_indices": {"type": "array", "items": {"type": "integer"}},
         "clear": {"type": "boolean", "default": False}},
         "required": ["name", "segment_indices"]}},

    {"name": "jianying_set_segment_flags",
     "description": "杂项标志位：cartoon（漫画风）/ is_placeholder（占位）/ is_tone_modify / track_attribute。cartoon 还需 cartoon_path 指向已生成帧，单独置位效果未知，慎用。",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"},
         "track_type": {"type": "string", "default": "video"},
         "segment_index": {"type": "integer", "default": 0},
         "cartoon": {"type": "boolean"},
         "is_placeholder": {"type": "boolean"},
         "is_tone_modify": {"type": "boolean"},
         "track_attribute": {"type": "integer"}},
         "required": ["name"]}},
    # ---------- 第三批：文字阴影 / 文字排版 / 变声音色 / 视频标志位 ----------
    {"name": "jianying_set_text_shadow",
     "description": "文字-阴影：开关 + 颜色/透明度/角度/距离/羽化/偏移点。写 materials.texts[] 的 has_shadow 与 shadow_*（默认值取自真实草稿：alpha 0.9 / angle -45 / distance 15 / smoothing 0.58）。",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"},
         "text_segment_index": {"type": "integer", "default": 0},
         "enable": {"type": "boolean", "default": True},
         "shadow_color": {"type": "string"},
         "shadow_alpha": {"type": "number"},
         "shadow_angle": {"type": "number"},
         "shadow_distance": {"type": "number"},
         "shadow_smoothing": {"type": "number"},
         "shadow_x": {"type": "number"},
         "shadow_y": {"type": "number"}},
         "required": ["name"]}},

    {"name": "jianying_set_text_layout",
     "description": "文字-排版与透明度：global_alpha/text_alpha、固定框宽高（-1 表示自适应）、层级权重 layer_weight、强制行宽。",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"},
         "text_segment_index": {"type": "integer", "default": 0},
         "global_alpha": {"type": "number"},
         "text_alpha": {"type": "number"},
         "fixed_width": {"type": "number"},
         "fixed_height": {"type": "number"},
         "layer_weight": {"type": "integer"},
         "force_apply_line_max_width": {"type": "boolean"}},
         "required": ["name"]}},

    {"name": "jianying_set_audio_tone",
     "description": "音频-变声/音色：写 audios[] 的 tone_type / tone_category_id / tone_category_name / tone_effect_id / tone_effect_name / tone_speaker。注：字段位置已由真实草稿确认，但变声实际效果由剪映音频引擎按这些 ID 解析，ID 须来自目录（用 search_catalog 的 voice_change 面板查）。",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"},
         "audio_segment_index": {"type": "integer", "default": 0},
         "tone_type": {"type": "string"},
         "tone_category_id": {"type": "string"},
         "tone_category_name": {"type": "string"},
         "tone_effect_id": {"type": "string"},
         "tone_effect_name": {"type": "string"},
         "tone_speaker": {"type": "string"},
         "clear": {"type": "boolean", "default": False}},
         "required": ["name"]}},

    {"name": "jianying_set_video_flags",
     "description": "视频素材标志位：is_unified_beauty_mode（美颜总开关）、object_locked（素材锁定）。美颜具体强度是引擎侧实时参数，草稿里只有这个布尔开关。",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"},
         "track_type": {"type": "string", "default": "video"},
         "segment_index": {"type": "integer", "default": 0},
         "is_unified_beauty_mode": {"type": "boolean"},
         "object_locked": {"type": "boolean"}},
         "required": ["name"]}},
    # ---------- 第四批：通用字段写入（白名单）----------
    {"name": "jianying_set_raw_field",
     "description": "通用字段写入（白名单）：用于覆盖低频字段（如 language、underline_offset、shape_clip_x/y、sub_type、font_url 等）。target=segment 写片段，target=material 写其素材。fields 为 {字段名: 值}，只接受已知存在于 4.3.1 草稿 schema 的键，其余拒绝。不解释语义，写入前返回旧值便于回滚。",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"},
         "target": {"type": "string", "default": "segment", "description": "segment | material"},
         "track_type": {"type": "string", "default": "video"},
         "segment_index": {"type": "integer", "default": 0},
         "fields": {"type": "object", "description": "{字段名: 值}"}},
         "required": ["name", "fields"]}},
]


def _load_content_for_edit(name: str) -> dict:
    d = _draft_dir(name)
    cf = d / "draft_content.json"
    if not _is_plain_json(cf):
        raise ValueError("草稿已加密，无法修改")
    return json.loads(cf.read_text(encoding='utf-8'))


def _save_content(name: str, content: dict) -> str:
    d = _draft_dir(name)
    cf = d / "draft_content.json"
    cf.write_text(json.dumps(content, ensure_ascii=False), encoding='utf-8')
    return str(cf)


def _seg_and_mat(name, track_type, segment_index):
    """返回 (content, segment, material, material_kind)."""
    content = _load_content_for_edit(name)
    seg = _find_segment(content, track_type, segment_index)
    if seg is None:
        raise ValueError("目标轨道无片段")
    mid = seg.get("material_id")
    mats = content.get("materials") or {}
    for kind, lst in mats.items():
        if not isinstance(lst, list):
            continue
        for it in lst:
            if isinstance(it, dict) and it.get("id") == mid:
                return content, seg, it, kind
    return content, seg, None, ""


def _ref_lookup(content, ref_id):
    for kind, lst in (content.get("materials") or {}).items():
        if not isinstance(lst, list):
            continue
        for it in lst:
            if isinstance(it, dict) and it.get("id") == ref_id:
                return kind, it
    return None, None


def get_segment_panel(name: str, track_type: str = "video", segment_index: int = 0) -> dict:
    """读出片段在面板上的全部真实参数。"""
    content, seg, mat, kind = _seg_and_mat(name, track_type, segment_index)
    clip = seg.get("clip") or {}
    scale = clip.get("scale") or {}
    tf = clip.get("transform") or {}
    flip = clip.get("flip") or {}
    tg = seg.get("target_timerange") or {}
    sr = seg.get("source_timerange") or {}
    effects, anims = [], []
    for r in (seg.get("extra_material_refs") or []):
        rk, ro = _ref_lookup(content, r)
        if not ro:
            continue
        if rk == "video_effects":
            effects.append({"name": ro.get("name"), "effect_id": ro.get("effect_id"),
                            "resource_id": ro.get("resource_id"), "type": ro.get("type"),
                            "value": ro.get("value"), "material_id": ro.get("id")})
        elif rk == "material_animations":
            for a in (ro.get("animations") or []):
                anims.append({"name": a.get("name"), "type": a.get("type"),
                              "resource_id": a.get("resource_id"),
                              "duration_us": a.get("duration")})
    return {
        "草稿": name, "轨道": track_type, "片段序号": segment_index,
        "素材": (mat or {}).get("material_name") or (mat or {}).get("name"),
        "时间轴": {"start_us": tg.get("start"), "duration_us": tg.get("duration")},
        "素材入点": {"start_us": sr.get("start"), "duration_us": sr.get("duration")},
        "画面-基础": {
            "位置": {"x": tf.get("x"), "y": tf.get("y")},
            "缩放": {"x": scale.get("x"), "y": scale.get("y")},
            "旋转": clip.get("rotation"),
            "翻转": {"horizontal": flip.get("horizontal"), "vertical": flip.get("vertical")},
            "透明度": clip.get("alpha"),
            "等比缩放": seg.get("uniform_scale"),
        },
        "音频": {"音量": seg.get("volume"),
                 "音频增强": seg.get("intensifies_audio"),
                 "淡入淡出": (mat or {}).get("audio_fade")},
        "变速": {"倍速": seg.get("speed"), "倒放": seg.get("reverse")},
        "层级": {"可见": seg.get("visible"), "render_index": seg.get("render_index")},
        "抠像": (mat or {}).get("matting"),
        "画质算法": (mat or {}).get("video_algorithm"),
        "防抖": (mat or {}).get("stable"),
        "特效列表": effects,
        "动画列表": anims,
        "调色开关": {"enable_adjust": seg.get("enable_adjust"),
                     "enable_lut": seg.get("enable_lut"),
                     "enable_color_curves": seg.get("enable_color_curves")},
    }


def set_clip_transform(name: str, track_type: str = "video", segment_index: int = 0,
                       x=None, y=None, scale_x=None, scale_y=None, uniform_scale=None,
                       lock_ratio=None, rotation=None, flip_h=None, flip_v=None,
                       alpha=None) -> dict:
    content, seg, mat, kind = _seg_and_mat(name, track_type, segment_index)
    clip = seg.setdefault("clip", {"alpha": 1.0, "rotation": 0.0,
                                   "scale": {"x": 1.0, "y": 1.0},
                                   "transform": {"x": 0.0, "y": 0.0},
                                   "flip": {"horizontal": False, "vertical": False}})
    clip.setdefault("transform", {"x": 0.0, "y": 0.0})
    clip.setdefault("scale", {"x": 1.0, "y": 1.0})
    clip.setdefault("flip", {"horizontal": False, "vertical": False})
    if x is not None:
        clip["transform"]["x"] = float(x)
    if y is not None:
        clip["transform"]["y"] = float(y)
    if lock_ratio is not None:
        us = seg.setdefault("uniform_scale", {"on": True, "value": 1.0})
        us["on"] = bool(lock_ratio)
    if uniform_scale is not None:
        us = seg.setdefault("uniform_scale", {"on": True, "value": 1.0})
        us["on"] = True
        us["value"] = float(uniform_scale)
        clip["scale"]["x"] = float(uniform_scale)
        clip["scale"]["y"] = float(uniform_scale)
    if scale_x is not None:
        clip["scale"]["x"] = float(scale_x)
    if scale_y is not None:
        clip["scale"]["y"] = float(scale_y)
    if rotation is not None:
        clip["rotation"] = float(rotation)
    if flip_h is not None:
        clip["flip"]["horizontal"] = bool(flip_h)
    if flip_v is not None:
        clip["flip"]["vertical"] = bool(flip_v)
    if alpha is not None:
        clip["alpha"] = max(0.0, min(1.0, float(alpha)))
    path = _save_content(name, content)
    return {"ok": True, "file": path, "clip": clip,
            "uniform_scale": seg.get("uniform_scale")}


def set_segment_speed(name: str, track_type: str = "video", segment_index: int = 0,
                      speed=None, reverse=None, curve_points=None) -> dict:
    content, seg, mat, kind = _seg_and_mat(name, track_type, segment_index)
    info = {}
    if speed is not None:
        sp = max(0.1, min(100.0, float(speed)))
        tg = seg.get("target_timerange") or {}
        sr = seg.get("source_timerange") or {}
        tg_dur = int(tg.get("duration") or 0)
        seg["speed"] = sp
        if tg_dur > 0:
            new_src = int(tg_dur * sp)
            mat_dur = int((mat or {}).get("duration") or 0)
            src_start = int(sr.get("start") or 0)
            if mat_dur > 0:
                new_src = min(new_src, max(mat_dur - src_start, 1000))
            sr["duration"] = new_src
            seg["source_timerange"] = sr
            info["source_duration_us"] = new_src
        info["speed"] = sp
    if reverse is not None:
        seg["reverse"] = bool(reverse)
        info["reverse"] = bool(reverse)
    # 同步关联 speeds 素材
    for r in (seg.get("extra_material_refs") or []):
        rk, ro = _ref_lookup(content, r)
        if rk == "speeds" and ro is not None:
            if speed is not None:
                ro["speed"] = float(seg["speed"])
            if curve_points is not None:
                ro["curve_speed"] = curve_points
                ro["mode"] = 1 if curve_points else 0
            info["speeds_material"] = {"speed": ro.get("speed"), "mode": ro.get("mode")}
    path = _save_content(name, content)
    return {"ok": True, "file": path, "segment_speed": seg.get("speed"),
            "detail": info}


def set_segment_audio(name: str, track_type: str = "video", segment_index: int = 0,
                      volume=None, intensifies_audio=None,
                      fade_in_us=None, fade_out_us=None) -> dict:
    content, seg, mat, kind = _seg_and_mat(name, track_type, segment_index)
    out = {}
    if volume is not None:
        v = max(0.0, float(volume))
        seg["volume"] = v
        if v > 0:
            seg["last_nonzero_volume"] = v
        out["volume"] = v
    if intensifies_audio is not None:
        seg["intensifies_audio"] = bool(intensifies_audio)
        out["intensifies_audio"] = bool(intensifies_audio)
    if (fade_in_us is not None or fade_out_us is not None) and mat is not None:
        cur = mat.get("audio_fade")
        if not isinstance(cur, dict):
            cur = {"in": 0, "out": 0}
        if fade_in_us is not None:
            cur["in"] = int(fade_in_us)
        if fade_out_us is not None:
            cur["out"] = int(fade_out_us)
        mat["audio_fade"] = cur
        out["audio_fade"] = cur
    path = _save_content(name, content)
    return {"ok": True, "file": path, "detail": out}


def set_segment_layer(name: str, track_type: str = "video", segment_index: int = 0,
                      visible=None, render_index=None) -> dict:
    content, seg, mat, kind = _seg_and_mat(name, track_type, segment_index)
    out = {}
    if visible is not None:
        seg["visible"] = bool(visible)
        out["visible"] = bool(visible)
    if render_index is not None:
        seg["render_index"] = int(render_index)
        seg["track_render_index"] = int(render_index)
        out["render_index"] = int(render_index)
    path = _save_content(name, content)
    return {"ok": True, "file": path, "detail": out}


def set_matting(name: str, track_type: str = "video", segment_index: int = 0,
                enable: bool = True, flag=None, path: str = "",
                clear_strokes: bool = False) -> dict:
    content, seg, mat, kind = _seg_and_mat(name, track_type, segment_index)
    if mat is None:
        raise ValueError("该片段没有可抠像的素材")
    m = mat.get("matting")
    if not isinstance(m, dict):
        m = {"flag": 0, "has_use_quick_brush": False, "has_use_quick_eraser": False,
             "interactiveTime": [], "path": "", "strokes": []}
    if flag is not None:
        m["flag"] = int(flag)
    else:
        m["flag"] = 1 if enable else 0
    if path:
        m["path"] = path
    if clear_strokes:
        m["strokes"] = []
        m["has_use_quick_brush"] = False
        m["has_use_quick_eraser"] = False
    mat["matting"] = m
    p = _save_content(name, content)
    return {"ok": True, "file": p, "matting": m,
            "note": "智能抠像的遮罩由剪映引擎生成；此处只写开关与参数"}


def set_video_algorithm(name: str, track_type: str = "video", segment_index: int = 0,
                        stable=None, denoise=None, deflicker=None,
                        motion_blur=None) -> dict:
    content, seg, mat, kind = _seg_and_mat(name, track_type, segment_index)
    if mat is None:
        raise ValueError("该片段没有视频素材")
    alg = mat.get("video_algorithm")
    if not isinstance(alg, dict):
        alg = {"algorithms": [], "deflicker": None, "motion_blur_config": None,
               "noise_reduction": None, "path": "", "time_range": None}
    if stable is not None:
        mat["stable"] = {"time_range": None} if stable else None
    if denoise is not None:
        alg["noise_reduction"] = {"time_range": None} if denoise else None
    if deflicker is not None:
        alg["deflicker"] = {"time_range": None} if deflicker else None
    if motion_blur is not None:
        alg["motion_blur_config"] = {} if motion_blur else None
    mat["video_algorithm"] = alg
    p = _save_content(name, content)
    return {"ok": True, "file": p, "video_algorithm": alg, "stable": mat.get("stable")}


def list_segment_effects(name: str, track_type: str = "video", segment_index: int = 0) -> dict:
    content, seg, mat, kind = _seg_and_mat(name, track_type, segment_index)
    items = []
    for i, r in enumerate(seg.get("extra_material_refs") or []):
        rk, ro = _ref_lookup(content, r)
        if rk != "video_effects" or not ro:
            continue
        items.append({"index": len(items), "name": ro.get("name"),
                      "effect_id": ro.get("effect_id"),
                      "resource_id": ro.get("resource_id"),
                      "type": ro.get("type"), "value": ro.get("value"),
                      "material_id": ro.get("id"), "ref_pos": i})
    return {"草稿": name, "片段序号": segment_index, "特效数": len(items), "特效": items}


def remove_segment_effect(name: str, track_type: str = "video", segment_index: int = 0,
                          effect_index: int = 0) -> dict:
    content, seg, mat, kind = _seg_and_mat(name, track_type, segment_index)
    refs = seg.get("extra_material_refs") or []
    hit, hit_pos, removed = None, None, None
    n = -1
    for i, r in enumerate(refs):
        rk, ro = _ref_lookup(content, r)
        if rk != "video_effects" or not ro:
            continue
        n += 1
        if n == int(effect_index):
            hit, hit_pos, removed = r, i, ro
            break
    if hit is None:
        raise ValueError("特效索引越界")
    refs.pop(hit_pos)
    seg["extra_material_refs"] = refs
    ve = content["materials"].get("video_effects") or []
    content["materials"]["video_effects"] = [e for e in ve if e.get("id") != hit]
    p = _save_content(name, content)
    return {"ok": True, "file": p, "removed": {"name": removed.get("name"),
                                                  "effect_id": removed.get("effect_id"),
                                                  "resource_id": removed.get("resource_id")}}


ADJUST_MAP = {"brightness": 0.0, "contrast": 0.0, "saturation": 0.0,
              "temperature": 0.0, "tone": 0.0, "highlight": 0.0, "shadow": 0.0,
              "sharpen": 0.0, "fade": 0.0, "hue": 0.0, "vignetting": 0.0}


def set_adjust_values(name: str, track_type: str = "video", segment_index: int = 0,
                      **kwargs) -> dict:
    """就地修改已有 adjust 载体里的参数（不新建未验证结构）。"""
    content, seg, mat, kind = _seg_and_mat(name, track_type, segment_index)
    targets = []
    for r in (seg.get("extra_material_refs") or []):
        rk, ro = _ref_lookup(content, r)
        if ro and ro.get("adjust_params"):
            targets.append((rk, ro))
    if not targets:
        for f in (content["materials"].get("filters") or []):
            if f.get("adjust_params"):
                targets.append(("filters", f))
    if not targets:
        raise ValueError("本片段没有已有的调节载体；4.3.1 无权威样本，请改用 add_effect_by_id 的调色类特效色，"
                         "或先在剪映里手动添加一次调节，建立载体后再用本工具批量改值")
    changed = {}
    for rk, ro in targets:
        for prm in ro.get("adjust_params") or []:
            nm = prm.get("name")
            if nm in kwargs and kwargs[nm] is not None:
                v = float(kwargs[nm])
                mn = prm.get("min_value", -1.0)
                mx = prm.get("max_value", 1.0)
                prm["value"] = max(float(mn), min(float(mx), v))
                changed[nm] = prm["value"]
    p = _save_content(name, content)
    return {"ok": True, "file": p, "载体": [t[0] for t in targets], "已改": changed}


def set_crop(name: str, track_type: str = "video", segment_index: int = 0,
             mode: str = "reset", x1=None, y1=None, x2=None, y2=None,
             ratio: str = "free", scale=None) -> dict:
    """画面-裁剪：写 videos[].crop 八个角点 + crop_ratio/crop_scale。"""
    content, seg, mat, kind = _seg_and_mat(name, track_type, segment_index)
    if mat is None:
        raise ValueError("该片段没有视频素材")
    if mode == "reset":
        crop = {"lower_left_x": 0.0, "lower_left_y": 1.0,
                "lower_right_x": 1.0, "lower_right_y": 1.0,
                "upper_left_x": 0.0, "upper_left_y": 0.0,
                "upper_right_x": 1.0, "upper_right_y": 0.0}
    else:
        ax = 0.0 if x1 is None else max(0.0, min(1.0, float(x1)))
        ay = 0.0 if y1 is None else max(0.0, min(1.0, float(y1)))
        bx = 1.0 if x2 is None else max(0.0, min(1.0, float(x2)))
        by = 1.0 if y2 is None else max(0.0, min(1.0, float(y2)))
        if bx <= ax or by <= ay:
            raise ValueError("裁剪区域非法：需满足 x2>x1 且 y2>y1")
        crop = {"lower_left_x": ax, "lower_left_y": by,
                "lower_right_x": bx, "lower_right_y": by,
                "upper_left_x": ax, "upper_left_y": ay,
                "upper_right_x": bx, "upper_right_y": ay}
    mat["crop"] = crop
    mat["crop_ratio"] = ratio
    if scale is not None:
        mat["crop_scale"] = float(scale)
    p = _save_content(name, content)
    return {"ok": True, "file": p, "crop": crop, "crop_ratio": mat.get("crop_ratio"),
            "crop_scale": mat.get("crop_scale")}


def set_color_switches(name: str, track_type: str = "video", segment_index: int = 0,
                       enable_adjust=None, enable_lut=None, enable_color_curves=None,
                       enable_color_wheels=None, enable_smart_color_adjust=None) -> dict:
    content, seg, mat, kind = _seg_and_mat(name, track_type, segment_index)
    out = {}
    for k, v in (("enable_adjust", enable_adjust), ("enable_lut", enable_lut),
                 ("enable_color_curves", enable_color_curves),
                 ("enable_color_wheels", enable_color_wheels),
                 ("enable_smart_color_adjust", enable_smart_color_adjust)):
        if v is not None:
            seg[k] = bool(v)
            out[k] = bool(v)
    p = _save_content(name, content)
    return {"ok": True, "file": p, "detail": out,
            "note": "hdr_settings 之外的颜色开关；这些开关只控制对应子项是否生效"}


def set_hdr(name: str, track_type: str = "video", segment_index: int = 0,
            enabled=None, convert_mode=None, intensity=None, nits=None,
            reset: bool = False) -> dict:
    """HDR 设置。注意：无真实样本，字段组合为推断值。"""
    content, seg, mat, kind = _seg_and_mat(name, track_type, segment_index)
    if reset:
        seg["hdr_settings"] = None
        p = _save_content(name, content)
        return {"ok": True, "file": p, "hdr_settings": None}
    cur = seg.get("hdr_settings")
    if not isinstance(cur, dict):
        cur = {"enable": False, "mode": 0, "intensity": 1.0, "nits": 1000}
    if enabled is not None:
        cur["enable"] = bool(enabled)
    if convert_mode is not None:
        cur["mode"] = int(convert_mode)
    if intensity is not None:
        cur["intensity"] = float(intensity)
    if nits is not None:
        cur["nits"] = int(nits)
    seg["hdr_settings"] = cur
    p = _save_content(name, content)
    return {"ok": True, "file": p, "hdr_settings": cur,
            "verified": False,
            "note": "二进制中确认存在 hdr_settings 与 hdrSetting{ConvertMode,Intensity,Nits,Enabled}，"
                    "但本机无已启用 HDR 的真实草稿样本，字段名组合为推断，请在剪映里核对"}


KEYFRAME_PROPERTY = {
    0: "位置X", 1: "位置Y", 2: "缩放", 3: "旋转", 4: "透明度", 5: "音量",
}


def set_keyframes(name: str, track_type: str = "video", segment_index: int = 0,
                  property_type: int = 2, points=None, curve: str = "Line",
                  clear: bool = False) -> dict:
    """关键帧：写 segment.common_keyframes + keyframe_refs。"""
    content, seg, mat, kind = _seg_and_mat(name, track_type, segment_index)
    kfs = seg.get("common_keyframes")
    if not isinstance(kfs, list):
        kfs = []
    refs = seg.get("keyframe_refs")
    if not isinstance(refs, list):
        refs = []
    if clear:
        for g in kfs:
            gid = g.get("id")
            if gid in refs:
                refs.remove(gid)
        seg["common_keyframes"] = []
        seg["keyframe_refs"] = refs
        p = _save_content(name, content)
        return {"ok": True, "file": p, "cleared": True, "keyframe_refs": refs}
    if not points:
        raise ValueError("points 不能为空；清空请用 clear=true")
    lst = []
    for pt in points:
        if not isinstance(pt, (list, tuple)) or len(pt) < 2:
            raise ValueError("points 每项应为 [time_offset_us, value]")
        lst.append({"time_offset": int(pt[0]), "values": [float(pt[1])],
                    "curve_type": curve, "curveType": curve})
    lst.sort(key=lambda x: x["time_offset"])
    gid = _uuid()
    group = {"id": gid, "keyframe_list": lst,
             "material_id": seg.get("id"), "property_type": int(property_type)}
    # 同一属性已有分组则替换，避免叠加
    kfs = [g for g in kfs if g.get("property_type") != int(property_type)]
    kfs.append(group)
    seg["common_keyframes"] = kfs
    if gid not in refs:
        refs.append(gid)
    seg["keyframe_refs"] = refs
    p = _save_content(name, content)
    return {"ok": True, "file": p, "property_type": int(property_type),
            "property_name": KEYFRAME_PROPERTY.get(int(property_type), "?"),
            "keyframes": len(lst), "keyframe_refs": refs,
            "verified": False,
            "note": "字段名已在二进制确认，属性编号与 material_id 指向未用真实样本验证"}


def set_text_background(name: str, text_segment_index: int = 0,
                        background_color: str = None, background_alpha=None,
                        background_width=None, background_height=None,
                        background_round_radius=None,
                        background_horizontal_offset=None,
                        background_vertical_offset=None,
                        background_style=None) -> dict:
    """文字-背景块参数。"""
    content = _load_content_for_edit(name)
    seg = _find_segment(content, "text", int(text_segment_index))
    if seg is None:
        raise ValueError("没有该文字片段")
    mid = seg.get("material_id")
    tgt = None
    for t in (content["materials"].get("texts") or []):
        if t.get("id") == mid:
            tgt = t
            break
    if tgt is None:
        raise ValueError("找不到文字素材")
    out = {}
    mapping = [("background_color", background_color), ("background_alpha", background_alpha),
               ("background_width", background_width), ("background_height", background_height),
               ("background_round_radius", background_round_radius),
               ("background_horizontal_offset", background_horizontal_offset),
               ("background_vertical_offset", background_vertical_offset),
               ("background_style", background_style)]
    for k, v in mapping:
        if v is not None:
            tgt[k] = v
            out[k] = v
    p = _save_content(name, content)
    return {"ok": True, "file": p, "detail": out}


def group_segments(name: str, track_type: str = "video", segment_indices=None,
                   clear: bool = False) -> dict:
    """成组 / 解组。"""
    content = _load_content_for_edit(name)
    trk = None
    for t in content.get("tracks", []):
        if t.get("type") == track_type and t.get("segments"):
            trk = t
            break
    if trk is None:
        raise ValueError("目标轨道无片段")
    segs = sorted(trk["segments"], key=lambda s: s["target_timerange"]["start"])
    gid = "" if clear else _uuid()
    touched = []
    for idx in (segment_indices or []):
        if 0 <= int(idx) < len(segs):
            segs[int(idx)]["group_id"] = gid
            touched.append(int(idx))
    if not touched:
        raise ValueError("segment_indices 无有效项")
    p = _save_content(name, content)
    return {"ok": True, "file": p, "group_id": gid, "segments": touched}


def set_segment_flags(name: str, track_type: str = "video", segment_index: int = 0,
                      cartoon=None, is_placeholder=None, is_tone_modify=None,
                      track_attribute=None) -> dict:
    content, seg, mat, kind = _seg_and_mat(name, track_type, segment_index)
    out = {}
    for k, v in (("cartoon", cartoon), ("is_placeholder", is_placeholder),
                 ("is_tone_modify", is_tone_modify), ("track_attribute", track_attribute)):
        if v is not None:
            seg[k] = v
            out[k] = v
    p = _save_content(name, content)
    return {"ok": True, "file": p, "detail": out,
            "warning": "底层标志位；cartoon 需要 cartoon_path 指向已生成的漫画帧，单独置位可能无效"}


def _text_material(name: str, text_segment_index: int):
    content = _load_content_for_edit(name)
    seg = _find_segment(content, "text", int(text_segment_index))
    if seg is None:
        raise ValueError("没有该文字片段")
    mid = seg.get("material_id")
    for t in (content["materials"].get("texts") or []):
        if t.get("id") == mid:
            return content, seg, t
    raise ValueError("找不到文字素材")


def set_text_shadow(name: str, text_segment_index: int = 0, enable: bool = True,
                    shadow_color=None, shadow_alpha=None, shadow_angle=None,
                    shadow_distance=None, shadow_smoothing=None,
                    shadow_x=None, shadow_y=None) -> dict:
    content, seg, t = _text_material(name, text_segment_index)
    t["has_shadow"] = bool(enable)
    out = {"has_shadow": bool(enable)}
    for k, v in (("shadow_color", shadow_color), ("shadow_alpha", shadow_alpha),
                 ("shadow_angle", shadow_angle), ("shadow_distance", shadow_distance),
                 ("shadow_smoothing", shadow_smoothing)):
        if v is not None:
            t[k] = v
            out[k] = v
    if shadow_x is not None or shadow_y is not None:
        pt = t.get("shadow_point")
        if not isinstance(pt, dict):
            pt = {"x": 0.0, "y": 0.0}
        if shadow_x is not None:
            pt["x"] = float(shadow_x)
        if shadow_y is not None:
            pt["y"] = float(shadow_y)
        t["shadow_point"] = pt
        out["shadow_point"] = pt
    # 应用默认值兜底（取自真实草稿）
    t.setdefault("shadow_alpha", 0.9)
    t.setdefault("shadow_angle", -45.0)
    t.setdefault("shadow_distance", 15.0)
    t.setdefault("shadow_smoothing", 0.58)
    t.setdefault("shadow_point", {"x": 0.0, "y": 0.0})
    p = _save_content(name, content)
    return {"ok": True, "file": p, "detail": out}


def set_text_layout(name: str, text_segment_index: int = 0, global_alpha=None,
                    text_alpha=None, fixed_width=None, fixed_height=None,
                    layer_weight=None, force_apply_line_max_width=None) -> dict:
    content, seg, t = _text_material(name, text_segment_index)
    out = {}
    for k, v in (("global_alpha", global_alpha), ("text_alpha", text_alpha),
                 ("fixed_width", fixed_width), ("fixed_height", fixed_height),
                 ("layer_weight", layer_weight),
                 ("force_apply_line_max_width", force_apply_line_max_width)):
        if v is not None:
            t[k] = v
            out[k] = v
    p = _save_content(name, content)
    return {"ok": True, "file": p, "detail": out}


TONE_KEYS = ("tone_type", "tone_category_id", "tone_category_name",
             "tone_effect_id", "tone_effect_name", "tone_speaker")


def set_audio_tone(name: str, audio_segment_index: int = 0, tone_type=None,
                   tone_category_id=None, tone_category_name=None,
                   tone_effect_id=None, tone_effect_name=None,
                   tone_speaker=None, clear: bool = False) -> dict:
    content = _load_content_for_edit(name)
    seg = _find_segment(content, "audio", int(audio_segment_index))
    if seg is None:
        raise ValueError("没有该音频片段")
    mid = seg.get("material_id")
    tgt = None
    for a in (content["materials"].get("audios") or []):
        if a.get("id") == mid:
            tgt = a
            break
    if tgt is None:
        raise ValueError("找不到音频素材")
    if clear:
        for k in TONE_KEYS:
            tgt[k] = ""
        p = _save_content(name, content)
        return {"ok": True, "file": p, "cleared": True}
    vals = {"tone_type": tone_type, "tone_category_id": tone_category_id,
            "tone_category_name": tone_category_name, "tone_effect_id": tone_effect_id,
            "tone_effect_name": tone_effect_name, "tone_speaker": tone_speaker}
    out = {}
    for k, v in vals.items():
        if v is not None:
            tgt[k] = v
            out[k] = v
    p = _save_content(name, content)
    return {"ok": True, "file": p, "detail": out,
            "note": "变声由剪映音频引擎按这些 ID 解析，ID 可从 search_catalog(panel='voice_change') 获取"}


def set_video_flags(name: str, track_type: str = "video", segment_index: int = 0,
                    is_unified_beauty_mode=None, object_locked=None) -> dict:
    content, seg, mat, kind = _seg_and_mat(name, track_type, segment_index)
    if mat is None:
        raise ValueError("该片段没有视频素材")
    out = {}
    if is_unified_beauty_mode is not None:
        mat["is_unified_beauty_mode"] = bool(is_unified_beauty_mode)
        out["is_unified_beauty_mode"] = bool(is_unified_beauty_mode)
    if object_locked is not None:
        mat["object_locked"] = bool(object_locked)
        out["object_locked"] = bool(object_locked)
    p = _save_content(name, content)
    return {"ok": True, "file": p, "detail": out,
            "note": "美颜强度是引擎侧实时参数，草稿只有总开关"}


RAW_KEYS_SEGMENT = {
    "template_id", "template_scene", "cartoon", "is_placeholder", "is_tone_modify",
    "track_attribute", "group_id", "enable_adjust", "enable_lut", "enable_color_curves",
    "enable_color_wheels", "enable_smart_color_adjust", "hdr_settings",
    "common_keyframes", "keyframe_refs", "uniform_scale",
}
RAW_KEYS_VIDEO = {
    "cartoon_path", "reverse_path", "intensifies_path", "intensifies_audio_path",
    "reverse_intensifies_path", "picture_set_category_id", "picture_set_category_name",
    "gameplay", "freeze", "matting", "stable", "video_algorithm", "crop",
    "crop_ratio", "crop_scale", "audio_fade", "is_unified_beauty_mode", "object_locked",
}
RAW_KEYS_AUDIO = {"wave_points", "music_id", "text_id", "video_id", "tone_speaker"}
RAW_KEYS_TEXT = {
    "language", "underline_offset", "shape_clip_x", "shape_clip_y", "sub_type",
    "font_url", "font_source_platform", "font_team_id", "style_name", "is_rich_text",
    "ktv_color", "recognize_type", "text_preset_resource_id", "preset_id", "preset_name",
    "preset_category", "preset_category_id", "preset_index", "preset_has_set_alignment",
    "tts_auto_update", "text_to_audio_ids", "fonts", "typesetting", "words", "styles",
    "has_shadow", "shadow_color", "shadow_alpha", "shadow_angle", "shadow_distance",
    "shadow_smoothing", "shadow_point", "global_alpha", "text_alpha",
    "fixed_width", "fixed_height", "layer_weight", "force_apply_line_max_width",
    "initial_scale", "relevance_segment", "underline_width", "use_effect_default_color",
}
RAW_KEYS_EFFECT = {"apply_time_range", "time_range"}

RAW_BY_TARGET = {
    "segment": RAW_KEYS_SEGMENT,
    "segments": RAW_KEYS_SEGMENT,
    "videos": RAW_KEYS_VIDEO,
    "video": RAW_KEYS_VIDEO,
    "audios": RAW_KEYS_AUDIO,
    "audio": RAW_KEYS_AUDIO,
    "texts": RAW_KEYS_TEXT,
    "text": RAW_KEYS_TEXT,
    "video_effects": RAW_KEYS_EFFECT,
}

RAW_WHITELIST = (RAW_KEYS_SEGMENT | RAW_KEYS_VIDEO | RAW_KEYS_AUDIO
                 | RAW_KEYS_TEXT | RAW_KEYS_EFFECT)


def set_raw_field(name: str, fields: dict, target: str = "segment",
                  track_type: str = "video", segment_index: int = 0) -> dict:
    if not isinstance(fields, dict) or not fields:
        raise ValueError("fields 不能为空")
    content, seg, mat, kind = _seg_and_mat(name, track_type, segment_index)
    # 目标归一化：material 时按素材类型判定
    tgt_name = target
    if target in ("material", "auto"):
        tgt_name = kind or "videos"
    allowed = RAW_BY_TARGET.get(tgt_name)
    if allowed is None:
        raise ValueError("target 只能是 segment / material / 素材类型名（videos/audios/texts/video_effects）")
    bad = [k for k in fields if k not in allowed]
    if bad:
        hints = {}
        for k in bad:
            for tn, ks in RAW_BY_TARGET.items():
                if k in ks:
                    hints[k] = tn
        raise ValueError("字段与目标不匹配: %s（正确目标: %s）"
                         % (", ".join(bad), hints or "不在白名单"))
    tgt = seg if tgt_name in ("segment", "segments") else mat
    if tgt is None:
        raise ValueError("目标素材不存在")
    old = {k: tgt.get(k) for k in fields}
    for k, v in fields.items():
        tgt[k] = v
    p = _save_content(name, content)
    return {"ok": True, "file": p, "target": tgt_name, "material_kind": kind,
            "旧值": old, "新值": {k: tgt.get(k) for k in fields},
            "note": "通用写入，不校验语义；回滚用返回的旧值再写一次"}


def call_tool(name: str, args: dict):
    if name == "jianying_locate":
        return locate()
    if name == "jianying_list_drafts":
        return {"drafts": list_drafts()}
    if name == "jianying_get_draft_detail":
        return draft_detail(args["name"])
    if name == "jianying_read_side_file":
        d = _draft_dir(args["name"])
        f = args["file"]
        if "/" in f or "\\" in f or ".." in f:
            raise ValueError("非法文件名")
        p = d / f
        if not p.exists():
            raise FileNotFoundError(f"{f} 不存在")
        return {"file": str(p), "content": p.read_text(encoding="utf-8", errors="replace")}
    if name == "jianying_backup_draft":
        return {"backup": backup_draft(args["name"], args.get("dest"))}
    if name == "jianying_create_draft":
        return create_draft(args["name"], args.get("width", 1920),
                            args.get("height", 1080), args.get("fps", 30))
    if name == "jianying_add_media_clip":
        return add_media_clip(args["name"], args["media_path"], args["start_us"],
                              args["duration_us"], args.get("ctype", "video"),
                              args.get("track_type"),
                              args.get("source_start_us", 0),
                              float(args.get("volume", 1.0)))
    if name == "jianying_add_text_clip":
        return add_text_clip(args["name"], args["text"], args["start_us"],
                             args["duration_us"], args.get("font_size", 20),
                             args.get("color", "#FFFFFF"))
    if name == "jianying_scan_effects":
        return scan_effects()
    if name == "jianying_add_adjust":
        kw = {k: float(args.get(k, 0)) for k in ("brightness", "contrast", "saturation",
                                                "temperature", "tone", "highlight",
                                                "shadow", "sharpen", "fade",
                                                "hue", "vignetting")}
        return add_adjust(args["name"], args.get("track_type", "video"),
                          int(args.get("segment_index", 0)), **kw)
    if name == "jianying_add_effect_from_template":
        return add_effect_from_template(args["name"], args["template_key"],
                                        args.get("kind", "filter"),
                                        args.get("track_type", "video"),
                                        int(args.get("segment_index", 0)))
    if name == "jianying_list_catalog":
        return list_catalog(args.get("kind", "filter"), args.get("keyword", ""),
                            int(args.get("limit", 40)))
    if name == "jianying_add_filter":
        return add_filter(args["name"], args["filter_name"], float(args.get("intensity", 1.0)),
                          int(args.get("segment_index", 0)))
    if name == "jianying_add_transition":
        return add_transition(args["name"], args["transition_name"],
                              int(args.get("segment_index", 0)),
                              int(args.get("duration_ms", 500)))
    if name == "jianying_add_video_effect":
        return add_video_effect(args["name"], args["effect_name"],
                                int(args.get("segment_index", 0)),
                                "video", bool(args.get("apply_all", False)))
    if name == "jianying_search_effects":
        return search_effects(args.get("keyword", ""), int(args.get("limit", 30)))
    if name == "jianying_add_effect_by_id":
        return add_effect_by_id(args["name"], args["effect_id"], args["resource_id"],
                                args.get("effect_name", ""),
                                int(args.get("segment_index", 0)), "video",
                                bool(args.get("apply_all", False)),
                                float(args.get("value", 1.0)),
                                args.get("effect_kind", "video_effect"))
    if name == "jianying_add_filter_by_id":
        return add_filter_by_id(args["name"], args["filter_effect_id"],
                                args["filter_resource_id"],
                                args.get("filter_name", ""),
                                float(args.get("intensity", 1.0)),
                                int(args.get("segment_index", 0)))
    if name == "jianying_search_catalog":
        return search_catalog(args.get("panel", "effects"), args.get("keyword", ""),
                              int(args.get("limit", 30)))
    if name == "jianying_add_transition_by_id":
        return add_transition_by_id(args["name"], args["effect_id"], args["resource_id"],
                                    args.get("transition_name", ""),
                                    int(args.get("segment_index", 0)),
                                    int(args.get("duration_ms", 500)))
    if name == "jianying_add_animation_by_id":
        return add_animation_by_id(args["name"], args["effect_id"], args["resource_id"],
                                   args.get("anim_type", "in"),
                                   args.get("anim_name", ""),
                                   args.get("target_kind", "text"),
                                   int(args.get("segment_index", 0)),
                                   int(args.get("duration_us", 500000)))
    if name == "jianying_set_text_font":
        return set_text_font(args["name"], args["font_resource_id"],
                             args.get("font_path", ""), args.get("font_name", ""),
                             int(args.get("text_segment_index", 0)))
    if name == "jianying_set_text_style":
        return set_text_style(args["name"], int(args.get("text_segment_index", 0)),
                              args.get("font_size"), args.get("text_color"),
                              args.get("bold_width"), args.get("italic"),
                              args.get("underline"), args.get("alignment"),
                              args.get("letter_spacing"), args.get("line_spacing"),
                              args.get("background_color"), args.get("background_alpha"),
                              args.get("stroke_color"), args.get("stroke_width"))
    if name == "jianying_prefetch_package":
        return prefetch_package(args.get("panel", "effects"), args.get("keyword", ""),
                                args.get("effect_id", ""), args.get("resource_id", ""))
    if name == "jianying_open_draft":
        return open_draft(args["name"])
    # ---------- 面板级参数 ----------
    if name == "jianying_get_segment_panel":
        return get_segment_panel(args["name"], args.get("track_type", "video"),
                                 int(args.get("segment_index", 0)))
    if name == "jianying_set_clip_transform":
        return set_clip_transform(args["name"], args.get("track_type", "video"),
                                  int(args.get("segment_index", 0)),
                                  args.get("x"), args.get("y"),
                                  args.get("scale_x"), args.get("scale_y"),
                                  args.get("uniform_scale"), args.get("lock_ratio"),
                                  args.get("rotation"), args.get("flip_h"),
                                  args.get("flip_v"), args.get("alpha"))
    if name == "jianying_set_segment_speed":
        return set_segment_speed(args["name"], args.get("track_type", "video"),
                                 int(args.get("segment_index", 0)),
                                 args.get("speed"), args.get("reverse"),
                                 args.get("curve_points"))
    if name == "jianying_set_segment_audio":
        return set_segment_audio(args["name"], args.get("track_type", "video"),
                                 int(args.get("segment_index", 0)),
                                 args.get("volume"), args.get("intensifies_audio"),
                                 args.get("fade_in_us"), args.get("fade_out_us"))
    if name == "jianying_set_segment_layer":
        return set_segment_layer(args["name"], args.get("track_type", "video"),
                                 int(args.get("segment_index", 0)),
                                 args.get("visible"), args.get("render_index"))
    if name == "jianying_set_matting":
        return set_matting(args["name"], args.get("track_type", "video"),
                           int(args.get("segment_index", 0)),
                           bool(args.get("enable", True)), args.get("flag"),
                           args.get("path", "") or "",
                           bool(args.get("clear_strokes", False)))
    if name == "jianying_set_video_algorithm":
        return set_video_algorithm(args["name"], args.get("track_type", "video"),
                                   int(args.get("segment_index", 0)),
                                   args.get("stable"), args.get("denoise"),
                                   args.get("deflicker"), args.get("motion_blur"))
    if name == "jianying_list_segment_effects":
        return list_segment_effects(args["name"], args.get("track_type", "video"),
                                    int(args.get("segment_index", 0)))
    if name == "jianying_remove_segment_effect":
        return remove_segment_effect(args["name"], args.get("track_type", "video"),
                                     int(args.get("segment_index", 0)),
                                     int(args.get("effect_index", 0)))
    if name == "jianying_set_adjust_values":
        kw = {k: args.get(k) for k in ADJUST_MAP}
        return set_adjust_values(args["name"], args.get("track_type", "video"),
                                 int(args.get("segment_index", 0)), **kw)
    if name == "jianying_set_crop":
        return set_crop(args["name"], args.get("track_type", "video"),
                        int(args.get("segment_index", 0)), args.get("mode", "reset"),
                        args.get("x1"), args.get("y1"), args.get("x2"), args.get("y2"),
                        args.get("ratio", "free"), args.get("scale"))
    if name == "jianying_set_color_switches":
        return set_color_switches(args["name"], args.get("track_type", "video"),
                                  int(args.get("segment_index", 0)),
                                  args.get("enable_adjust"), args.get("enable_lut"),
                                  args.get("enable_color_curves"), args.get("enable_color_wheels"),
                                  args.get("enable_smart_color_adjust"))
    if name == "jianying_set_hdr":
        return set_hdr(args["name"], args.get("track_type", "video"),
                       int(args.get("segment_index", 0)), args.get("enabled"),
                       args.get("convert_mode"), args.get("intensity"), args.get("nits"),
                       bool(args.get("reset", False)))
    if name == "jianying_set_keyframes":
        return set_keyframes(args["name"], args.get("track_type", "video"),
                             int(args.get("segment_index", 0)),
                             int(args.get("property_type", 2)), args.get("points"),
                             args.get("curve", "Line"), bool(args.get("clear", False)))
    if name == "jianying_set_text_background":
        return set_text_background(args["name"], int(args.get("text_segment_index", 0)),
                                   args.get("background_color"), args.get("background_alpha"),
                                   args.get("background_width"), args.get("background_height"),
                                   args.get("background_round_radius"),
                                   args.get("background_horizontal_offset"),
                                   args.get("background_vertical_offset"),
                                   args.get("background_style"))
    if name == "jianying_group_segments":
        return group_segments(args["name"], args.get("track_type", "video"),
                              args.get("segment_indices"), bool(args.get("clear", False)))
    if name == "jianying_set_segment_flags":
        return set_segment_flags(args["name"], args.get("track_type", "video"),
                                 int(args.get("segment_index", 0)), args.get("cartoon"),
                                 args.get("is_placeholder"), args.get("is_tone_modify"),
                                 args.get("track_attribute"))
    if name == "jianying_set_text_shadow":
        return set_text_shadow(args["name"], int(args.get("text_segment_index", 0)),
                               bool(args.get("enable", True)), args.get("shadow_color"),
                               args.get("shadow_alpha"), args.get("shadow_angle"),
                               args.get("shadow_distance"), args.get("shadow_smoothing"),
                               args.get("shadow_x"), args.get("shadow_y"))
    if name == "jianying_set_text_layout":
        return set_text_layout(args["name"], int(args.get("text_segment_index", 0)),
                               args.get("global_alpha"), args.get("text_alpha"),
                               args.get("fixed_width"), args.get("fixed_height"),
                               args.get("layer_weight"), args.get("force_apply_line_max_width"))
    if name == "jianying_set_audio_tone":
        return set_audio_tone(args["name"], int(args.get("audio_segment_index", 0)),
                              args.get("tone_type"), args.get("tone_category_id"),
                              args.get("tone_category_name"), args.get("tone_effect_id"),
                              args.get("tone_effect_name"), args.get("tone_speaker"),
                              bool(args.get("clear", False)))
    if name == "jianying_set_video_flags":
        return set_video_flags(args["name"], args.get("track_type", "video"),
                               int(args.get("segment_index", 0)),
                               args.get("is_unified_beauty_mode"), args.get("object_locked"))
    if name == "jianying_set_raw_field":
        return set_raw_field(args["name"], args.get("fields") or {},
                             args.get("target", "segment"),
                             args.get("track_type", "video"),
                             int(args.get("segment_index", 0)))
    raise ValueError(f"未知工具: {name}")


# --------------------------------------------------------------------------
# MCP stdio 传输（每行一个 JSON-RPC 消息）
# --------------------------------------------------------------------------
def _send(obj: dict):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _tool_result(result) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=1)}]}


def handle(msg: dict):
    method = msg.get("method")
    mid = msg.get("id")
    if method is None:
        return
    if method == "initialize":
        _send({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": (msg.get("params") or {}).get("protocolVersion", PROTOCOL_VERSION),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}}})
        return
    if method == "notifications/initialized":
        return
    if method == "ping":
        if mid is not None:
            _send({"jsonrpc": "2.0", "id": mid, "result": {}})
        return
    if method == "tools/list":
        _send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
        return
    if method == "tools/call":
        params = msg.get("params") or {}
        tname = params.get("name")
        try:
            result = call_tool(tname, params.get("arguments") or {})
            _send({"jsonrpc": "2.0", "id": mid, "result": _tool_result(result)})
        except Exception as ex:
            _send({"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": f"错误: {type(ex).__name__}: {ex}"}],
                "isError": True}})
        return
    if mid is not None:
        _send({"jsonrpc": "2.0", "id": mid,
               "error": {"code": -32601, "message": f"未知方法: {method}"}})


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            handle(msg)
        except Exception as ex:
            mid = msg.get("id") if isinstance(msg, dict) else None
            if mid is not None:
                _send({"jsonrpc": "2.0", "id": mid,
                       "error": {"code": -32603, "message": f"内部错误: {ex}"}})


if __name__ == "__main__":
    main()
