#!/usr/bin/env python3
"""
Bilibili 直播助手 - 音频录制 + AI 转写 + 智能总结

支持场景：
  --url      B站直播间链接（录制+转写+总结）
  --video    任意主流视频平台链接（下载音频+转写+总结）
  --audio    本地音频文件（转写+总结）
"""

import argparse
import sys
from pathlib import Path

from config import get_scene_config, OUTPUT_DIR, WHISPER_MODE
from live_capture import capture_live, extract_room_id, download_video_audio
from transcriber import transcribe, transcribe_video_streaming, transcribe_live_streaming
from summarizer import create_session_dir, save_transcript, save_summary, generate_summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bilibili 直播 / 视频 总结助手",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  %(prog)s --url https://live.bilibili.com/xxx --check
  %(prog)s --url https://live.bilibili.com/xxx --duration 3600 --scene lecture
  %(prog)s --url https://live.bilibili.com/xxx --until-end --scene streamer
  %(prog)s --video https://www.bilibili.com/video/BV1xx411c7mD --scene lecture
  %(prog)s --video https://www.youtube.com/watch?v=xxx --scene general
  %(prog)s --audio recording.wav --scene streamer
        """,
    )
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument("--url", "-u", help="Bilibili 直播间链接")
    input_group.add_argument("--video", "-v", help="视频链接（任意平台）")
    input_group.add_argument("--audio", "-a", type=Path, help="本地音频文件")
    parser.add_argument("--duration", "-t", type=int, default=None, help="录制时长（秒），仅 --url 模式")
    parser.add_argument("--check", "-c", action="store_true", help="快速查看：录制 5 分钟，仅 --url 模式")
    parser.add_argument("--until-end", "-e", action="store_true", help="跟播模式，仅 --url 模式")
    parser.add_argument("--scene", "-s", default="general", choices=["general", "lecture", "streamer"], help="场景")
    parser.add_argument("--screenshot", action="store_true", help="截取直播画面，仅 --url 模式")
    parser.add_argument("--no-summarize", action="store_true", help="不自动总结")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    scene = args.scene if args.scene in ["general", "lecture", "streamer"] else "general"
    session_dir = None

    # ── 模式A：本地音频 ──
    if args.audio:
        if not args.audio.exists():
            print(f"[错误] 音频文件不存在: {args.audio}")
            sys.exit(1)
        print(f"[模式] 本地音频: {args.audio.name}")
        text = transcribe(args.audio, scene)
        session_dir = create_session_dir(args.audio.stem, "", "local", 0)
        save_transcript(text, session_dir)

    # ── 模式B：视频链接 ──
    elif args.video:
        print(f"{'=' * 50}\n 视频音频提取\n{'=' * 50}")
        import json, subprocess as _sp
        probe = _sp.run(
            [sys.executable, "-m", "yt_dlp", "--dump-json", "--no-playlist", args.video],
            capture_output=True, text=False, timeout=30,
        )
        if probe.returncode != 0:
            err = probe.stderr.decode("utf-8", errors="replace").strip()[:300]
            print(f"\n[错误] 无法获取该视频\n       可能原因：视频已被删除 / 私密 / 区域限制\n       yt-dlp: {err}")
            sys.exit(1)

        info = json.loads(probe.stdout.decode("utf-8"))
        video_title = info.get("title", "视频")
        video_uploader = info.get("uploader", info.get("channel", ""))
        print(f"[标题]   {video_title}\n[UP主]   {video_uploader}")
        session_dir = create_session_dir(video_title, video_uploader, f"video_{args.video[-11:-1]}", 0)
        print(f"[输出] {session_dir}")

        if WHISPER_MODE == "stream":
            print(f"\n--- 流式转写（边下边转） ---")
            text = transcribe_video_streaming(args.video, scene)
        else:
            try:
                audio_path = download_video_audio(args.video)
            except RuntimeError as e:
                if session_dir.exists() and not any(session_dir.iterdir()):
                    session_dir.rmdir()
                print(f"\n[错误] 音频下载失败: {e}")
                sys.exit(1)
            target = session_dir / "audio.wav"
            import shutil
            shutil.move(str(audio_path), str(target))
            print(f"\n--- 开始转写 ---")
            text = transcribe(target, scene)
        save_transcript(text, session_dir)

    # ── 模式C：B站直播 ──
    elif args.url:
        if args.check:
            duration = 300
        elif args.duration:
            duration = args.duration
        elif args.until_end:
            duration = None
        else:
            parser.error("请指定 --check / --duration N / --until-end")

        print(f"{'=' * 50}\n Bilibili 直播助手")
        print(f" {'时长: ' + str(duration) + ' 秒' if duration else ' 模式: 跟播到结束'}")
        print(f"{'=' * 50}")

        from live_capture import get_live_status
        room_id = extract_room_id(args.url)
        try:
            status = get_live_status(room_id)
            session_dir = create_session_dir(status.get("title", room_id), status.get("uploader", ""), room_id, duration)
        except Exception:
            session_dir = create_session_dir(room_id, "", room_id, duration)
        print(f"[输出] {session_dir}")

        if WHISPER_MODE == "stream":
            dur = duration or 3600
            print(f"\n--- 流式转写（边录边转） ---")
            text = transcribe_live_streaming(room_id, scene, duration=dur)
        else:
            try:
                cap = capture_live(args.url, duration=duration, session_dir=session_dir, take_screenshot=args.screenshot)
            except RuntimeError as e:
                print(f"[错误] {e}")
                sys.exit(1)
            print(f"\n--- 开始转写 ---")
            text = transcribe(cap["audio_path"], scene)
        save_transcript(text, session_dir)

    else:
        parser.error("请指定 --url / --video / --audio")
        return

    # ── 总结 ──
    if not args.no_summarize:
        print(f"\n--- 生成简报 ---")
        # 如果有弹幕数据，全部附加到转写文本中
        danmaku_path = session_dir / "danmaku.txt"
        if danmaku_path.exists():
            danmaku_text = danmaku_path.read_text(encoding="utf-8")
            enriched = text + "\n\n【弹幕记录】\n" + danmaku_text
        else:
            enriched = text

        summary = generate_summary(enriched, scene=scene, title="", use_api=True)
        summary_path = save_summary(summary, session_dir)
        print(f"[保存] 简报: {summary_path}")
        safe = summary.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
        try:
            print(f"\n{safe[:800]}")
        except UnicodeEncodeError:
            pass
    else:
        print(f"\n[提示] 转写文本已保存至 {session_dir}")

    print(f"\n{'=' * 50}\n 完成！\n 输出目录: {session_dir}")


if __name__ == "__main__":
    main()
