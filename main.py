#!/usr/bin/env python3
"""
Bilibili 直播助手 - CLI 入口

用法:
  python main.py --url https://live.bilibili.com/xxx --check
  python main.py --video https://www.bilibili.com/video/BVxxx
  python main.py --audio recording.wav
"""

import argparse
import sys
from pathlib import Path

from pipeline import process_live, process_video, process_audio, process_local_video


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
    input_group.add_argument("--url", "-u",    help="Bilibili 直播间链接")
    input_group.add_argument("--video", "-v",  help="视频链接（任意平台）")
    input_group.add_argument("--audio", "-a",  type=Path, help="本地音频文件")
    input_group.add_argument("--local-video", type=Path, help="本地视频文件（提取音频后转写）")
    input_group.add_argument("--daemon",       action="store_true", help="启动队列守护进程（模型常驻，排队处理）")
    input_group.add_argument("--enqueue",      action="store_true", help="提交任务到队列并退出（需配合 --url/--video/--audio）")
    input_group.add_argument("--queue-status", action="store_true", help="查看队列状态（pending/running/done/failed）")
    parser.add_argument("--duration", "-t",    type=int, default=None, help="录制时长（秒），仅 --url 模式")
    parser.add_argument("--check", "-c",       action="store_true", help="快速查看：录制 5 分钟")
    parser.add_argument("--until-end", "-e",   action="store_true", help="跟播到结束")
    parser.add_argument("--scene", "-s",       default="general", choices=["general", "lecture", "streamer"], help="场景")
    parser.add_argument("--screenshot",        action="store_true", help="截取直播画面（仅 --url）")
    parser.add_argument("--no-summarize",      action="store_true", help="不自动总结")
    parser.add_argument("--wait",              action="store_true", help="提交队列任务后等待完成（需 --enqueue）")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    scene = args.scene if args.scene in ["general", "lecture", "streamer"] else "general"

    # ── 队列状态 ──
    if args.queue_status:
        from queue_manager import queue_status
        s = queue_status()
        def _icon(v): return "🟢" if v else "⚫"
        print(f"  队列状态")
        print(f"  pending: {s['pending']} | running: {s['running']} | done: {s['done']} | failed: {s['failed']}")
        print(f"  daemon:  {_icon(s['daemon_alive'])} {'运行中' if s['daemon_alive'] else '未启动'}")
        return

    # ── 队列守护进程 ──
    if args.daemon:
        from queue_manager import QueueDaemon
        daemon = QueueDaemon()
        try:
            daemon.run()
        except KeyboardInterrupt:
            print("\n[Daemon] 收到终止信号，正在退出...")
            daemon.stop()
        return

    # ── 提交到队列 ──
    if args.enqueue:
        from queue_manager import queue_enqueue, queue_enqueue_wait

        # 确定源类型和值
        if args.url:
            src_type, src_val = "url", args.url
        elif args.video:
            src_type, src_val = "video", args.video
        elif args.audio:
            src_type, src_val = "audio", str(args.audio.resolve())
        else:
            parser.error("--enqueue 需配合 --url / --video / --audio")

        duration = None
        extra = {
            "scene": scene,
            "screenshot": args.screenshot,
            "no_summarize": args.no_summarize,
        }
        if args.check:
            extra["duration"] = 300
        elif args.duration:
            extra["duration"] = args.duration

        if args.wait:
            result = queue_enqueue_wait(src_type, src_val, scene=scene, **extra)
            status = result.get("status", "unknown")
            if status == "done":
                print(f"[完成] 输出目录: {result.get('result_dir', '?')}")
            elif status == "failed":
                print(f"[失败] {result.get('error', '?')}")
            else:
                print(f"[{status}] {result.get('error', '')}")
        else:
            job_id = queue_enqueue(src_type, src_val, scene=scene, **extra)
            print(f"[队列] 已提交任务: {job_id}")
            print(f"[队列] 查看结果: queue/done/job_{job_id}.json")
        return

    # ── 模式A1：本地视频 ──
    if args.local_video:
        try:
            process_local_video(args.local_video, scene, no_summarize=args.no_summarize)
        except (FileNotFoundError, RuntimeError) as e:
            print(f"[错误] {e}")
            sys.exit(1)

    # ── 模式A2：本地音频 ──
    if args.audio:
        try:
            process_audio(args.audio, scene, no_summarize=args.no_summarize)
        except FileNotFoundError as e:
            print(f"[错误] {e}")
            sys.exit(1)

    # ── 模式B：视频链接 ──
    elif args.video:
        try:
            process_video(args.video, scene, no_summarize=args.no_summarize)
        except RuntimeError as e:
            print(f"[错误] {e}")
            sys.exit(1)

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
        if duration:
            print(f" {'时长: ' + str(duration) + ' 秒'}")
        else:
            print(f" {'模式: 跟播到结束'}")
        print(f"{'=' * 50}")

        try:
            process_live(
                args.url,
                duration=duration,
                scene=scene,
                screenshot=args.screenshot,
                no_summarize=args.no_summarize,
            )
        except RuntimeError as e:
            print(f"[错误] {e}")
            sys.exit(1)

    else:
        parser.error("请指定 --url / --video / --audio / --daemon / --enqueue")


if __name__ == "__main__":
    main()
