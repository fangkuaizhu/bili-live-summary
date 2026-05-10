#!/usr/bin/env python3
"""
测试脚本：只处理合集A的前5P，验证管道通顺

用法:
  python tools/test_5p.py
  python tools/test_5p.py --dry-run

输出:
  output/test_5p/collection_A/ 目录，含5份transcript+summary
  完成后自动触发比对
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from bilibili_downloader import get_video_pages, get_video_info
from transcriber import _setup_cuda_paths

OUTPUT = _PROJECT_ROOT / "output" / "test_5p"


def main():
    parser = argparse.ArgumentParser(description="测试：合集A前5P管道验证")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划")
    args = parser.parse_args()

    _setup_cuda_paths()

    # 获取元数据
    bv = "BV1DE421M7AZ"
    info = get_video_info(bv)
    all_pages = get_video_pages(bv)
    pages = all_pages[:5]  # 只取前5P

    print(f"\n合集: {info['title']}")
    print(f"全部: {len(all_pages)}P, 测试: {len(pages)}P")
    for p in pages:
        print(f"  P{p['page']}: {p['part'][:40]} ({p['duration']//60}min)")

    if args.dry_run:
        print("\n[Dry-Run] 完成")
        return

    # 清理旧测试输出
    import shutil
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    (OUTPUT / "collection_A").mkdir(parents=True, exist_ok=True)

    # 逐P处理
    from transcriber import transcribe
    from tools.comparison_prompt import summarize_math_lecture

    timings = []
    for idx, p in enumerate(pages, 1):
        page_num = p["page"]
        part_name = p.get("part", f"P{page_num}")
        page_dir = OUTPUT / "collection_A" / f"P{page_num:03d}"
        page_dir.mkdir(exist_ok=True)

        print(f"\n[{idx}/{len(pages)}] P{page_num}: {part_name[:40]}")

        t0 = time.time()

        # 下载
        audio_path = page_dir / "audio.wav"
        if not audio_path.exists() or audio_path.stat().st_size < 1000:
            from bilibili_downloader import download_page_audio
            download_page_audio(bv, p["cid"], page_num, audio_path)
        else:
            print(f"  音频已存在 ({audio_path.stat().st_size/1024/1024:.0f} MB)")

        # 转写
        transcript_path = page_dir / "transcript.txt"
        if not transcript_path.exists():
            print(f"  转写中...")
            transcript = transcribe(audio_path, "lecture")
            transcript_path.write_text(transcript, encoding="utf-8")
        else:
            transcript = transcript_path.read_text(encoding="utf-8")
            print(f"  转录已存在 ({len(transcript)} chars)")

        # AI 分析
        summary_path = page_dir / "summary.md"
        if not summary_path.exists():
            print(f"  AI分析中...")
            summary = summarize_math_lecture(transcript, title=part_name)
            summary_path.write_text(summary, encoding="utf-8")
        else:
            summary = summary_path.read_text(encoding="utf-8")
            print(f"  分析已存在 ({len(summary)} chars)")

        # 标记完成
        (page_dir / ".done").write_text(datetime.now().isoformat())

        elapsed = time.time() - t0
        timings.append((page_num, elapsed))
        print(f"  [完成] 耗时 {elapsed/60:.1f}min")

        # 验证前5P完成进度
        completed = len([d for d in (OUTPUT/"collection_A").iterdir() if d.is_dir() and (d/".done").exists()])
        print(f"  [进度] {completed}/{len(pages)}P 完成")

    # 汇总
    print(f"\n{'='*50}")
    print(f"  前{len(pages)}P 处理完成!")
    for pn, t in timings:
        print(f"  P{pn}: {t/60:.1f}min")
    total = sum(t for _, t in timings)
    print(f"  总计: {total/60:.1f}min ({total/3600:.1f}h)")
    print(f"  输出: {OUTPUT}")

    # 估算剩余时间
    remaining = len(all_pages) - len(pages)
    avg_time = total / len(pages)
    est_remaining = remaining * avg_time
    print(f"  剩余 {remaining}P, 预计 {est_remaining/3600:.1f}h")


if __name__ == "__main__":
    main()
