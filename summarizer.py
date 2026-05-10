"""
总结模块：转写保存 + 简报生成

支持多种 AI 平台：
- minimax（默认）：通过 Anthropic 兼容接口
- deepseek / openai：通过 OpenAI 兼容接口（可自定义 base_url）
"""

import json
import os
import re
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import get_scene_config, OUTPUT_DIR


def _safe_path(text: str, max_len: int = 40) -> str:
    clean = re.sub(r'[\\/:*?"<>|]', "", text).strip()
    if len(clean) > max_len:
        clean = clean[:max_len]
    return clean if clean else "untitled"


def _cleanup_old_sessions(parent_dir: Path, max_sessions: int = 5):
    """保留最近 max_sessions 次记录，删除更旧的"""
    if not parent_dir.exists():
        return
    sessions = sorted([
        d for d in parent_dir.iterdir()
        if d.is_dir() and d.name[:8].isdigit()  # 按时间戳命名的文件夹
    ], reverse=True)
    for old in sessions[max_sessions:]:
        import shutil
        shutil.rmtree(old, ignore_errors=True)


def create_session_dir(
    title: str, uploader: str, room_id: str,
    duration: Optional[int] = None,
    parent: Optional[Path] = None,
    max_sessions: int = 5,
) -> Path:
    slug_title = _safe_path(title if title else room_id, 40)
    slug_uploader = _safe_path(uploader, 20) if uploader else room_id[:20]
    room_dir = (parent or OUTPUT_DIR) / f"{slug_title}_{slug_uploader}"
    room_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now().strftime("%Y%m%d_%H%M")
    dur_str = (
        f"{duration//60}min_{duration%60}s" if duration and duration >= 60
        else f"{duration}s" if duration
        else "full"
    )
    session_dir = room_dir / f"{now}_{dur_str}"
    session_dir.mkdir(parents=True, exist_ok=True)

    counter = 1
    orig = session_dir
    while session_dir.exists() and any(session_dir.iterdir()):
        session_dir = room_dir / f"{now}_{dur_str}_{counter}"
        counter += 1
    if session_dir != orig:
        session_dir.mkdir(parents=True, exist_ok=True)

    # 滚动清理旧记录
    _cleanup_old_sessions(room_dir, max_sessions)
    return session_dir


def save_transcript(text: str, session_dir: Path, header: str = "") -> Path:
    transcript_path = session_dir / "transcript.txt"
    if not header:
        header = f"# Bilibili 直播转写\n录制时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n{'=' * 40}\n\n"
    transcript_path.write_text(header + text, encoding="utf-8")
    return transcript_path


def save_summary(summary: str, session_dir: Path) -> Path:
    path = session_dir / "summary.md"
    path.write_text(summary, encoding="utf-8")
    return path


# ============================================
#   API Key 获取
# ============================================

def _get_api_key(platform: str) -> str:
    """获取指定平台的 API Key（优先本地配置文件，环境变量兜底）"""
    if platform == "minimax":
        from config import MINIMAX_API_KEY
        if MINIMAX_API_KEY:
            return MINIMAX_API_KEY
        # 兼容旧的 mmx 配置
        mmx_cfg = Path.home() / ".mmx" / "config.json"
        if mmx_cfg.exists():
            try:
                return json.loads(mmx_cfg.read_text(encoding="utf-8")).get("api_key", "")
            except Exception:
                pass
        return os.environ.get("MINIMAX_API_KEY", "") or os.environ.get("MMX_API_KEY", "")

    elif platform == "deepseek":
        from config import DEEPSEEK_API_KEY
        return DEEPSEEK_API_KEY or os.environ.get("DEEPSEEK_API_KEY", "")

    else:  # openai
        from config import OPENAI_API_KEY
        return OPENAI_API_KEY or os.environ.get("OPENAI_API_KEY", "")


# ============================================
#   公共 HTTP 请求层
# ============================================

def _api_post(url: str, payload: dict, api_key: str) -> dict:
    """通用 API POST 请求（JSON in / JSON out）"""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ============================================
#   MiniMax API（Anthropic 兼容接口）
# ============================================

def _call_minimax(text: str, instruction: str, max_tokens: int = 2048) -> str:
    from config import MINIMAX_MODEL

    api_key = _get_api_key("minimax")
    if not api_key:
        raise RuntimeError("未找到 MiniMax API Key")

    result = _api_post(
        "https://api.minimaxi.com/anthropic/v1/messages",
        {
            "model": MINIMAX_MODEL,
            "max_tokens": max_tokens,
            "system": "你是一个直播内容总结助手。用中文回复，简洁、有条理。",
            "messages": [
                {"role": "user",
                 "content": f"{instruction}\n\n以下是直播转写文本：\n\n{text}"},
            ],
            "stream": False,
        },
        api_key,
    )
    texts = [b["text"] for b in result.get("content", []) if b.get("type") == "text"]
    if texts:
        return texts[0].strip()
    raise RuntimeError(f"MiniMax 返回异常: {result}")


# ============================================
#   OpenAI 兼容接口（DeepSeek / 自定义）
# ============================================

def _call_openai(text: str, instruction: str, max_tokens: int = 2048) -> str:
    from config import (
        SUMMARIZER_API,
        DEEPSEEK_BASE_URL, DEEPSEEK_MODEL,
        OPENAI_BASE_URL, OPENAI_MODEL,
    )

    api_key = _get_api_key(SUMMARIZER_API)
    if not api_key:
        raise RuntimeError(f"未配置 {SUMMARIZER_API} 的 API Key")

    if SUMMARIZER_API == "deepseek":
        base_url = DEEPSEEK_BASE_URL.rstrip("/")
        model = DEEPSEEK_MODEL
    else:
        base_url = OPENAI_BASE_URL.rstrip("/")
        model = OPENAI_MODEL

    result = _api_post(
        f"{base_url}/v1/chat/completions",
        {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system",
                 "content": "你是一个直播/视频内容总结助手。用中文回复，简洁、有条理。"},
                {"role": "user",
                 "content": f"{instruction}\n\n以下是转写文本：\n\n{text}"},
            ],
            "stream": False,
            "temperature": 0.7,
        },
        api_key,
    )
    choices = result.get("choices", [])
    if choices:
        return choices[0].get("message", {}).get("content", "").strip()
    raise RuntimeError(f"API 返回异常: {result}")


# ============================================
#   统一入口
# ============================================

def summarize_with_api(text: str, scene: str = "general", max_tokens: int = 2048) -> str:
    """根据 config.SUMMARIZER_API 自动选择平台"""
    from config import SUMMARIZER_API

    scene_cfg = get_scene_config(scene)
    instruction = scene_cfg["summary_instruction"]

    if SUMMARIZER_API == "minimax":
        return _call_minimax(text, instruction, max_tokens)
    else:
        return _call_openai(text, instruction, max_tokens)


def generate_summary(text: str, scene: str = "general",
                     title: str = "", use_api: bool = False) -> str:
    if use_api:
        try:
            return summarize_with_api(text, scene)
        except RuntimeError as e:
            print(f"[警告] API 总结失败: {e}")
            print("[提示] 将使用手动总结模式")

    config = get_scene_config(scene)
    lines = [
        f"## 直播简报（待 AI 总结）",
        f"",
        f"场景: {scene} - {config['description']}",
        f"标题: {title}",
        f"",
        f"已将 {len(text)} 字符的转写文本保存至 output/ 目录。",
        f"",
        f"如需 AI 自动总结，运行脚本时不要加 --no-summarize 参数；",
        f"或者将转写文件发送给 Hanako，让她帮你总结。",
    ]
    return "\n".join(lines)
