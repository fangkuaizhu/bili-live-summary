"""
核心处理流程：live/video/audio 三条完整 pipeline

供 main.py（CLI）和 queue_manager.py（Daemon）共用。
"""

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from config import get_scene_config, OUTPUT_DIR, WHISPER_MODE, TEMP_DIR
from live_capture import (
    capture_live as _capture_live,
    extract_room_id,
    download_video_audio,
    get_live_status,
)
from transcriber import transcribe, transcribe_video_streaming, transcribe_live_streaming, get_model
from summarizer import create_session_dir, save_transcript, save_summary, generate_summary


def process_live(
    url: str,
    duration: Optional[int] = None,
    scene: str = "general",
    screenshot: bool = False,
    no_summarize: bool = False,
) -> Path:
    """B站直播间：录制 → 转写 → 总结，返回 session_dir
    duration=None 即跟播到结束"""
    room_id = extract_room_id(url)

    # 获取直播间信息
    try:
        status = get_live_status(room_id)
        session_dir = create_session_dir(
            status.get("title", room_id),
            status.get("uploader", ""),
            room_id,
            duration,
        )
    except Exception:
        session_dir = create_session_dir(room_id, "", room_id, duration)
    session_dir.mkdir(parents=True, exist_ok=True)
    print(f"[输出] {session_dir}")

    # 采集 + 转写
    if WHISPER_MODE == "stream":
        dur = duration or 3600
        print(f"\n--- 流式转写（边录边转） ---")
        text = transcribe_live_streaming(room_id, scene, duration=dur)
    else:
        try:
            cap = _capture_live(
                url,
                duration=duration,          # None = 跟播到结束
                session_dir=session_dir,
                take_screenshot=screenshot,
            )
        except RuntimeError as e:
            print(f"[错误] {e}")
            raise
        print(f"\n--- 开始转写 ---")
        text = transcribe(cap["audio_path"], scene)

    save_transcript(text, session_dir)

    # 总结
    if not no_summarize:
        print(f"\n--- 生成简报 ---")
        enriched = _enrich_with_danmaku(text, session_dir)
        summary = generate_summary(enriched, scene=scene, title="", use_api=True)
        summary_path = save_summary(summary, session_dir)
        print(f"[保存] 简报: {summary_path}")

    print(f"\n{'=' * 50}\n 完成！\n 输出目录: {session_dir}")
    return session_dir


def process_video(
    url: str,
    scene: str = "general",
    no_summarize: bool = False,
) -> Path:
    """视频链接：下载 → 转写 → 总结，返回 session_dir
    
    B站视频使用原生 API，其他平台走 yt-dlp。
    """
    from bilibili_downloader import is_bilibili_url, download_bilibili_audio

    print(f"{'=' * 50}\n 视频音频提取\n{'=' * 50}")

    if is_bilibili_url(url):
        # B站原生 API 路径
        audio_path, info = download_bilibili_audio(url)
        video_title = info["title"]
        video_uploader = info["uploader"]
        print(f"[标题]   {video_title}\n[UP主]   {video_uploader}")
    else:
        # yt-dlp 路径（YouTube 等）
        probe = subprocess.run(
            [sys.executable, "-m", "yt_dlp", "--dump-json", "--no-playlist", url],
            capture_output=True, text=False, timeout=30,
        )
        if probe.returncode != 0:
            err = probe.stderr.decode("utf-8", errors="replace").strip()[:300]
            print(f"\n[错误] 无法获取该视频\n       可能原因：视频已被删除 / 私密 / 区域限制\n       yt-dlp: {err}")
            raise RuntimeError(f"视频获取失败: {err}")

        info = json.loads(probe.stdout.decode("utf-8"))
        video_title = info.get("title", "视频")
        video_uploader = info.get("uploader", info.get("channel", ""))
        print(f"[标题]   {video_title}\n[UP主]   {video_uploader}")

        try:
            audio_path = download_video_audio(url)
        except RuntimeError as e:
            print(f"\n[错误] 音频下载失败: {e}")
            raise

    session_dir = create_session_dir(video_title, video_uploader, f"video_{hash(url) & 0xFFFFFFFF:08x}", 0)
    session_dir.mkdir(parents=True, exist_ok=True)
    print(f"[输出] {session_dir}")

    # 转写
    target = session_dir / "audio.wav"
    shutil.move(str(audio_path), str(target))
    print(f"\n--- 开始转写 ---")
    text = transcribe(target, scene)

    save_transcript(text, session_dir)

    # 总结
    if not no_summarize:
        print(f"\n--- 生成简报 ---")
        summary = generate_summary(text, scene=scene, title="", use_api=True)
        summary_path = save_summary(summary, session_dir)
        print(f"[保存] 简报: {summary_path}")

    print(f"\n{'=' * 50}\n 完成！\n 输出目录: {session_dir}")
    return session_dir


def process_local_video(
    video_path: Path,
    scene: str = "general",
    no_summarize: bool = False,
) -> Path:
    """本地视频文件：提取音频 → 转写 → 总结，返回 session_dir"""
    from bilibili_downloader import extract_local_video_audio

    if not video_path.exists():
        raise FileNotFoundError(f"视频文件不存在: {video_path}")

    print(f"{'=' * 50}\n 本地视频提取\n{'=' * 50}")
    print(f"[文件]   {video_path.name}")
    print(f"[大小]   {video_path.stat().st_size / 1024 / 1024:.1f} MB")

    audio_path = extract_local_video_audio(video_path)

    session_dir = create_session_dir(video_path.stem, "", "local", 0)
    session_dir.mkdir(parents=True, exist_ok=True)
    print(f"[输出] {session_dir}")

    target = session_dir / "audio.wav"
    shutil.move(str(audio_path), str(target))

    print(f"\n--- 开始转写 ---")
    text = transcribe(target, scene)
    save_transcript(text, session_dir)

    if not no_summarize:
        print(f"\n--- 生成简报 ---")
        summary = generate_summary(text, scene=scene, title="", use_api=True)
        summary_path = save_summary(summary, session_dir)
        print(f"[保存] 简报: {summary_path}")

    print(f"\n{'=' * 50}\n 完成！\n 输出目录: {session_dir}")
    return session_dir


def process_audio(
    audio_path: Path,
    scene: str = "general",
    no_summarize: bool = False,
) -> Path:
    """本地音频文件：转写 → 总结，返回 session_dir"""
    if not audio_path.exists():
        raise FileNotFoundError(f"音频文件不存在: {audio_path}")

    print(f"[模式] 本地音频: {audio_path.name}")
    text = transcribe(audio_path, scene)
    session_dir = create_session_dir(audio_path.stem, "", "local", 0)
    session_dir.mkdir(parents=True, exist_ok=True)
    save_transcript(text, session_dir)

    if not no_summarize:
        print(f"\n--- 生成简报 ---")
        summary = generate_summary(text, scene=scene, title="", use_api=True)
        summary_path = save_summary(summary, session_dir)
        print(f"[保存] 简报: {summary_path}")

    print(f"\n{'=' * 50}\n 完成！\n 输出目录: {session_dir}")
    return session_dir


def _enrich_with_danmaku(transcript: str, session_dir: Path) -> str:
    """如果 session_dir 下有弹幕文件，拼入转写文本"""
    danmaku_path = session_dir / "danmaku.txt"
    if danmaku_path.exists():
        danmaku_text = danmaku_path.read_text(encoding="utf-8")
        return transcript + "\n\n【弹幕记录】\n" + danmaku_text
    return transcript
