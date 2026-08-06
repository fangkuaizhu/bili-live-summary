"""
核心处理流程：live/video/audio 三条完整 pipeline

供 main.py（CLI）和 queue_manager.py（Daemon）共用。
"""

import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from config import get_scene_config, OUTPUT_DIR, WHISPER_MODE, TEMP_DIR

# ocr-local 项目路径（跨项目集成）
OCR_LOCAL_DIR = r"H:/ocr-local"
OCR_PYTHON = OCR_LOCAL_DIR + r"/ocr_env/Scripts/python.exe"
OCR_CLI = OCR_LOCAL_DIR + r"/ocr_cli.py"
OCR_QUEUE_DONE = OCR_LOCAL_DIR + r"/queue/done"


def _status(mode: str, **kwargs) -> str:
    """输出机器可读状态行，供 Agent 解析"""
    # 结果描述
    detected = kwargs.get("voice_detected") or kwargs.get("text_detected")
    if mode == "whisper":
        result = "voice_ok" if detected == "true" else "voice_low"
    else:
        result = "text_ok" if detected == "true" else "text_none"

    parts = [f"mode={mode}", f"result={result}"]
    for k, v in kwargs.items():
        parts.append(f"{k}={v}")
    line = "[STATUS] " + " | ".join(parts)
    print(line, flush=True)
    return line
from live_capture import (
    capture_live as _capture_live,
    extract_room_id,
    download_video_audio,
    get_live_status,
)
from transcriber import transcribe, transcribe_video_streaming, transcribe_live_streaming, get_model
from summarizer import create_session_dir, save_transcript, save_summary, generate_summary
from corrector import correct_transcript


def process_live(
    url: str,
    duration: Optional[int] = None,
    scene: str = "general",
    screenshot: bool = False,
    no_summarize: bool = False,
    correct: bool = True,
) -> Path:
    """B站直播间：录制 → 转写 → 总结，返回 session_dir
    duration=None 即跟播到结束"""
    room_id = extract_room_id(url)

    # 获取直播间信息
    try:
        status = get_live_status(room_id)
        live_title = status.get("title", room_id)
        session_dir = create_session_dir(
            live_title,
            status.get("uploader", ""),
            room_id,
            duration,
        )
    except Exception:
        live_title = room_id
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

    # 二轮纠错
    if correct:
        print(f"\n--- LLM 二轮纠错 ---")
        corrected = correct_transcript(text, scene=scene, title=live_title, use_api=True)
        corrected_path = session_dir / "transcript_corrected.txt"
        corrected_path.write_text(corrected, encoding="utf-8")
        print(f"[纠错] 保存: {corrected_path}")

    # 总结
    if not no_summarize:
        print(f"\n--- 生成简报 ---")
        enriched = _enrich_with_danmaku(text, session_dir)
        summary = generate_summary(enriched, scene=scene, title="", use_api=True)
        summary_path = save_summary(summary, session_dir)
        print(f"[保存] 简报: {summary_path}")

    print(f"\n{'=' * 50}\n 完成！\n 输出目录: {session_dir}")
    _status("whisper", voice_detected=str(len(text) > 50).lower(),
            chars=str(len(text)), source="live")
    return session_dir


def process_video(
    url: str,
    scene: str = "general",
    no_summarize: bool = False,
    correct: bool = True,
) -> Path:
    """视频链接：下载 → 转写 → 总结（>30分钟自动切流式），返回 session_dir
    
    B站视频使用原生 API，其他平台走 yt-dlp。
    """
    from bilibili_downloader import (
        is_bilibili_url, download_bilibili_audio,
        get_video_info, extract_bv,
    )

    print(f"{'=' * 50}\n 视频音频提取\n{'=' * 50}")

    if is_bilibili_url(url):
        # B站原生 API：先获取信息（不下载），判断是否需要流式
        bv = extract_bv(url)
        info = get_video_info(bv)
        video_title = info["title"]
        video_uploader = info["uploader"]
        duration = info.get("duration", 0)
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
        duration = info.get("duration", 0)
        print(f"[标题]   {video_title}\n[UP主]   {video_uploader}")

    session_dir = create_session_dir(video_title, video_uploader, f"video_{hash(url) & 0xFFFFFFFF:08x}", 0)
    session_dir.mkdir(parents=True, exist_ok=True)
    print(f"[输出] {session_dir}")

    # 转写：B站超 30 分钟走流式，其余走 batch
    if is_bilibili_url(url) and duration > 1800:
        print(f"\n[切换] 视频时长 {duration // 60} 分钟 (>30 分钟)，使用流式转写")
        text = transcribe_video_streaming(url, scene)
    else:
        if is_bilibili_url(url):
            audio_path, _ = download_bilibili_audio(url)
        else:
            try:
                audio_path = download_video_audio(url)
            except RuntimeError as e:
                print(f"\n[错误] 音频下载失败: {e}")
                raise

        target = session_dir / "audio.wav"
        shutil.move(str(audio_path), str(target))
        print(f"\n--- 开始转写 ---")
        text = transcribe(target, scene, hotwords=None, title=video_title)

    save_transcript(text, session_dir)

    # 二轮纠错
    if correct:
        print(f"\n--- LLM 二轮纠错 ---")
        corrected = correct_transcript(text, scene=scene, title=video_title, use_api=True)
        corrected_path = session_dir / "transcript_corrected.txt"
        corrected_path.write_text(corrected, encoding="utf-8")
        print(f"[纠错] 保存: {corrected_path}")

    # 总结
    if not no_summarize:
        print(f"\n--- 生成简报 ---")
        summary = generate_summary(text, scene=scene, title="", use_api=True)
        summary_path = save_summary(summary, session_dir)
        print(f"[保存] 简报: {summary_path}")

    print(f"\n{'=' * 50}\n 完成！\n 输出目录: {session_dir}")
    _status("whisper", voice_detected=str(len(text) > 50).lower(),
            chars=str(len(text)), source="video")
    return session_dir


def process_local_video(
    video_path: Path,
    scene: str = "general",
    no_summarize: bool = False,
    correct: bool = True,
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

    # 二轮纠错
    if correct:
        print(f"\n--- LLM 二轮纠错 ---")
        corrected = correct_transcript(text, scene=scene, title=video_path.stem, use_api=True)
        corrected_path = session_dir / "transcript_corrected.txt"
        corrected_path.write_text(corrected, encoding="utf-8")
        print(f"[纠错] 保存: {corrected_path}")

    if not no_summarize:
        print(f"\n--- 生成简报 ---")
        summary = generate_summary(text, scene=scene, title="", use_api=True)
        summary_path = save_summary(summary, session_dir)
        print(f"[保存] 简报: {summary_path}")

    print(f"\n{'=' * 50}\n 完成！\n 输出目录: {session_dir}")
    return session_dir


def process_video_ocr(
    url: str,
    frame_interval: int = 12,
    scene: str = "general",
) -> Path:
    """视频链接：下载 → 抽帧 → OCR（无配音视频专用），返回 session_dir"""
    from bilibili_downloader import (
        is_bilibili_url, extract_bv, get_video_info,
        download_video_for_frames, extract_frames,
    )

    print(f"{'=' * 50}\n 视频 OCR 管道\n{'=' * 50}")

    if not is_bilibili_url(url):
        raise RuntimeError("OCR 管道目前仅支持 B站视频（BV号/完整链接/b23短链）")

    bv = extract_bv(url)
    info = get_video_info(bv)
    video_title = info["title"]
    video_uploader = info["uploader"]
    total_sec = info.get("duration", 0)
    print(f"[标题]   {video_title}\n[UP主]   {video_uploader}\n[时长]   {total_sec//60}分{total_sec%60}秒")

    # 创建输出目录
    session_dir = create_session_dir(video_title, video_uploader, bv, 0)
    ocr_dir = session_dir / "ocr"
    ocr_dir.mkdir(parents=True, exist_ok=True)
    print(f"[输出]   {session_dir}")

    # 下载视频
    mp4_path = TEMP_DIR / f"{bv}_ocr_video.mp4"
    frames_dir = TEMP_DIR / f"{bv}_ocr_frames"
    total_frames = 0
    raw_results = []
    try:
        download_video_for_frames(url, mp4_path)

        # 抽帧
        frames = extract_frames(mp4_path, frames_dir, interval=frame_interval)
        if not frames:
            raise RuntimeError("未抽取出任何帧")
        total_frames = len(frames)

        # 调用 ocr-local daemon 识别每一帧
        print(f"\n--- OCR 识别（排队模式） ---")
        import subprocess as _sp
        import os as _os

        # 第1步：批量提交所有帧
        job_ids = []
        for i, frame_path in enumerate(frames):
            frame_sec = i * frame_interval
            try:
                r = _sp.run(
                    [OCR_PYTHON, OCR_CLI,
                     "--image", str(frame_path),
                     "--lang", "ch",
                     "--enqueue"],
                    capture_output=True, encoding="utf-8", errors="replace", timeout=10,
                    cwd=OCR_LOCAL_DIR,
                    env={**_os.environ, "PYTHONIOENCODING": "utf-8"},
                )
                for line in r.stdout.split("\n"):
                    if "任务ID:" in line:
                        jid = line.split("任务ID:")[-1].strip()
                        job_ids.append((frame_sec, jid))
                        break
                if frame_sec % 30 == 0:
                    print(f"  [{frame_sec:4d}s] 已提交 {len(job_ids)}/{total_frames}", flush=True)
            except Exception as e:
                print(f"  [{frame_sec:4d}s] 提交失败: {e}")

        if not job_ids:
            raise RuntimeError("所有帧提交失败，请确认 ocr daemon 已运行")

        # 第2步：轮询等待全部完成
        print(f"\n  共 {len(job_ids)} 个任务，等待处理...")
        raw_results = []
        remaining = dict(job_ids)
        poll_interval = 3
        waited = 0
        max_wait = 900

        ocr_queue_done = Path(OCR_QUEUE_DONE)
        ocr_queue_done.mkdir(parents=True, exist_ok=True)

        while remaining and waited < max_wait:
            time.sleep(poll_interval)
            waited += poll_interval
            done = []
            for frame_sec, jid in list(remaining.items()):
                done_file = ocr_queue_done / f"{jid}.json"
                if done_file.exists():
                    try:
                        result = json.loads(done_file.read_text(encoding="utf-8"))
                        blocks_raw = result.get("result", [])
                        blocks = [b["text"].strip() for b in blocks_raw if len(b.get("text", "").strip()) >= 2]
                        if blocks:
                            raw_results.append((frame_sec, blocks))
                    except Exception:
                        pass
                    done.append(frame_sec)
            for sec in done:
                del remaining[sec]
            if done:
                print(f"  [{waited}s] 完成 {len(done)}，剩余 {len(remaining)}")

        if remaining:
            print(f"  ⚠ {len(remaining)} 个任务超时未完成")
        print(f"  OCR 完成: {len(raw_results)} 帧有效")

        # 按时间排序
        raw_results.sort(key=lambda x: x[0])

        # 去重合并
        print(f"\n--- 去重合并 ---")
        deduped = []
        prev_texts = None
        for frame_sec, texts in raw_results:
            joined = " | ".join(texts)
            if joined != prev_texts:
                mm, ss = divmod(frame_sec, 60)
                deduped.append((mm, ss, joined))
                prev_texts = joined
        print(f"  {len(raw_results)} 帧 → {len(deduped)} 帧（去重后）")

        # 写出 OCR 文本
        ocr_text_path = ocr_dir / "ocr_text.txt"
        lines = []
        lines.append(f"# OCR 识别结果")
        lines.append(f"# 视频: {video_title}")
        lines.append(f"# UP主: {video_uploader}")
        lines.append(f"# BV号: {bv}")
        lines.append(f"# 总时长: {total_sec//60}分{total_sec%60}秒")
        lines.append(f"# 抽帧间隔: {frame_interval}s | 总帧数: {total_frames}")
        lines.append(f"# 有效帧: {len(deduped)}（去重后）")
        lines.append("")
        for mm, ss, text in deduped:
            lines.append(f"[{mm:02d}:{ss:02d}] {text}")
        lines.append("")
        # 纯文本附录
        lines.append("--- 纯文本拼接 ---")
        lines.append("".join(t for _, _, t in deduped).replace(" | ", ""))

        ocr_text_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"[保存] OCR 文本: {ocr_text_path}")

    finally:
        # 清理临时文件
        mp4_path.unlink(missing_ok=True)
        if frames_dir.exists():
            shutil.rmtree(frames_dir, ignore_errors=True)

    print(f"\n{'=' * 50}\n 完成！\n 输出目录: {session_dir}\n OCR 文本: {ocr_dir / 'ocr_text.txt'}")
    _status("ocr", text_detected=str(len(raw_results) >= 1).lower(),
            frames=str(total_frames), effective=str(len(raw_results)),
            chars=str(sum(len(t) for _, t in raw_results)))
    return session_dir


def process_audio(
    audio_path: Path,
    scene: str = "general",
    no_summarize: bool = False,
    correct: bool = True,
) -> Path:
    """本地音频文件：转写 → 总结，返回 session_dir"""
    if not audio_path.exists():
        raise FileNotFoundError(f"音频文件不存在: {audio_path}")

    print(f"[模式] 本地音频: {audio_path.name}")
    text = transcribe(audio_path, scene)
    session_dir = create_session_dir(audio_path.stem, "", "local", 0)
    session_dir.mkdir(parents=True, exist_ok=True)
    save_transcript(text, session_dir)

    # 二轮纠错
    if correct:
        print(f"\n--- LLM 二轮纠错 ---")
        corrected = correct_transcript(text, scene=scene, title=audio_path.stem, use_api=True)
        corrected_path = session_dir / "transcript_corrected.txt"
        corrected_path.write_text(corrected, encoding="utf-8")
        print(f"[纠错] 保存: {corrected_path}")

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
