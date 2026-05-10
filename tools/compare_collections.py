#!/usr/bin/env python3
"""
双合集AI比对系统 — 编排脚本

全量下载→转录→逐集总结→AI横向比对

用法:
  python tools/compare_collections.py \\
      --collection-a BV1DE421M7AZ \\
      --collection-b BV1qXN9z5E8V \\
      --output output/comparison_20260508
  
  python tools/compare_collections.py --dry-run  # 只打印计划不做实际操作
  python tools/compare_collections.py --resume    # 从上次中断处继续

依赖:
  - bilibili_downloader.get_video_pages / download_page_audio
  - transcriber.transcribe
  - summarizer.generate_summary
  - comparison_prompt
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

# ── 添加项目根目录到 sys.path ──
_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from bilibili_downloader import get_video_pages, get_video_info, download_page_audio
from config import TEMP_DIR


def _safe_name(text: str, max_len: int = 50) -> str:
    """清理非法文件名字符"""
    clean = re.sub(r'[\\/:*?"<>|]', "", text).strip()
    if len(clean) > max_len:
        clean = clean[:max_len]
    return clean if clean else "untitled"


def _format_duration(seconds: int) -> str:
    h, r = divmod(seconds, 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    return f"{m}m{s:02d}s"


def _load_summary(page_dir: Path) -> dict | None:
    """读取一P的摘要，返回结构化 dict"""
    summary_file = page_dir / "summary.md"
    if not summary_file.exists():
        return None
    part_name = page_dir.name
    # 从目录名提取 page 编号
    m = re.match(r"P(\d+)_", part_name)
    page_num = int(m.group(1)) if m else 0
    return {
        "page": page_num,
        "part": part_name,
        "summary_text": summary_file.read_text(encoding="utf-8"),
    }


# =============================================
#  第一阶段：获取元数据
# =============================================

def fetch_metadata(bv: str) -> dict:
    """获取合集元数据"""
    info = get_video_info(bv)
    pages = get_video_pages(bv)
    total_duration = sum(p["duration"] for p in pages)
    return {
        "bv": bv,
        "title": info["title"],
        "uploader": info["uploader"],
        "total_pages": len(pages),
        "total_duration": total_duration,
        "total_duration_str": _format_duration(total_duration),
        "pages": pages,
    }


# =============================================
#  第二阶段：处理单个合集的所有分P
# =============================================

def process_collection(
    meta: dict,
    output_dir: Path,
    dry_run: bool = False,
    scene: str = "lecture",
) -> list[dict]:
    """
    处理一个合集：逐P下载→转录→总结
    返回所有分P的结构化摘要列表
    """
    bv = meta["bv"]
    pages = meta["pages"]
    print(f"\n{'='*60}")
    print(f"  合集: {meta['title']}")
    print(f"  BV: {bv} | {len(pages)}P | {meta['total_duration_str']}")
    print(f"{'='*60}")

    # 创建输出目录并写元数据
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 设置 CUDA DLL 路径（必须在导入 faster_whisper 前）
    try:
        from transcriber import _setup_cuda_paths
        _setup_cuda_paths()
    except Exception:
        pass

    from transcriber import transcribe

    summaries = []
    completed = 0
    skipped = 0
    failed = 0

    for idx, p in enumerate(pages, 1):
        page_num = p["page"]
        part_name = p.get("part", f"第{page_num}讲")
        safe_part = _safe_name(part_name)
        page_dir = output_dir / f"P{page_num:03d}_{safe_part}"
        marker = page_dir / ".done"

        # ── 断点续传检查 ──
        if marker.exists():
            print(f"  [{idx}/{len(pages)}] [SKIP]  P{page_num} {part_name[:30]} 已完成，跳过")
            skipped += 1
            summary = _load_summary(page_dir)
            if summary:
                summaries.append(summary)
            continue

        duration_str = _format_duration(p["duration"])

        if dry_run:
            print(f"  [{idx}/{len(pages)}] [DRY] P{page_num}: {part_name[:40]} ({duration_str})")
            continue

        print(f"\n  [{idx}/{len(pages)}] [>]  P{page_num}: {part_name[:40]} ({duration_str})")
        page_dir.mkdir(parents=True, exist_ok=True)

        try:
            # ── 1. 下载音频 ──
            audio_path = page_dir / "audio.wav"
            if not audio_path.exists() or audio_path.stat().st_size < 1000:
                print(f"    下载中...")
                download_page_audio(bv, p["cid"], page_num, audio_path)
            else:
                print(f"    音频已存在，复用")

            # ── 2. 转写 ──
            transcript_path = page_dir / "transcript.txt"
            if not transcript_path.exists():
                print(f"    转写中...")
                transcript = transcribe(audio_path, scene)
                transcript_path.write_text(transcript, encoding="utf-8")
            else:
                transcript = transcript_path.read_text(encoding="utf-8")
                print(f"    转录已存在，复用 ({len(transcript)} chars)")

            # ── 3. AI 总结（数学专用结构化摘要） ──
            summary_path = page_dir / "summary.md"
            if not summary_path.exists():
                print(f"    分析中（数学专用摘要）...")
                from comparison_prompt import summarize_math_lecture
                summary = summarize_math_lecture(transcript, title=part_name)
                summary_path.write_text(summary, encoding="utf-8")
            else:
                summary = summary_path.read_text(encoding="utf-8")
                print(f"    总结已存在，复用")

            # ── 标记完成 ──
            marker.write_text(f"done {datetime.now().isoformat()}")
            completed += 1

            summary_entry = {
                "page": page_num,
                "part": part_name,
                "duration": p["duration"],
                "duration_str": duration_str,
                "summary_text": summary,
                "transcript_path": str(transcript_path),
                "summary_path": str(summary_path),
            }
            summaries.append(summary_entry)

            # 保存进度
            _save_progress(output_dir, summaries, completed, skipped, failed)

        except Exception as e:
            print(f"    [FAIL]  失败: {e}")
            failed += 1
            # 写失败标记
            (page_dir / ".failed").write_text(f"{datetime.now().isoformat()}\n{str(e)}")
            _save_progress(output_dir, summaries, completed, skipped, failed)
            # 继续下一P
            continue

    # 汇总结果
    print(f"\n  [STATS]  {meta['title'][:30]}")
    print(f"    完成: {completed} | 跳过: {skipped} | 失败: {failed}")
    return summaries


def _save_progress(collection_dir: Path, summaries: list, completed: int, skipped: int, failed: int):
    """保存中间进度到 JSON"""
    progress = {
        "completed": completed,
        "skipped": skipped,
        "failed": failed,
        "total_summaries": len(summaries),
        "updated_at": datetime.now().isoformat(),
    }
    progress_path = collection_dir / "_progress.json"
    progress_path.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")

    # 同时更新 summaries json
    summaries_path = collection_dir.parent / f"{collection_dir.name}_summaries.json"
    summaries_path.write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# =============================================
#  主入口
# =============================================

def main():
    parser = argparse.ArgumentParser(
        description="双合集AI比对系统 — 全量下载→转录→总结→比对",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--collection-a", default="BV1DE421M7AZ", help="合集A的BV号")
    parser.add_argument("--collection-b", default="BV1qXN9z5E8V", help="合集B的BV号")
    parser.add_argument("--output", "-o", default=None,
                        help="输出目录（默认: output/comparison_YYYYMMDD）")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不做实际操作")
    parser.add_argument("--scene", default="lecture", choices=["general", "lecture", "streamer"],
                        help="转写场景（默认 lecture）")
    parser.add_argument("--compare-only", action="store_true",
                        help="仅执行比对（假设转录+总结已完成）")

    args = parser.parse_args()
    dry_run = args.dry_run

    # ── 确定输出目录 ──
    if args.output:
        output_root = Path(args.output)
    else:
        today = datetime.now().strftime("%Y%m%d")
        output_root = _PROJECT_ROOT / "output" / f"comparison_{today}"
    output_root.mkdir(parents=True, exist_ok=True)
    print(f"\n[OUT]  输出目录: {output_root}")

    # ── 阶段1：获取元数据 ──
    print(f"\n{'='*60}")
    print("  阶段1：获取合集元数据")
    print(f"{'='*60}")

    # 网络容错：获取元数据失败时使用默认值
    try:
        meta_a = fetch_metadata(args.collection_a)
    except Exception as e:
        print(f"  [WARN] 获取合集A元数据失败: {e}")
        print(f"  [WARN] 使用默认值继续")
        meta_a = {"bv": args.collection_a, "title": args.collection_a,
                   "uploader": "?", "total_pages": 0, "total_duration": 0,
                   "total_duration_str": "?", "pages": []}

    try:
        meta_b = fetch_metadata(args.collection_b)
    except Exception as e:
        print(f"  [WARN] 获取合集B元数据失败: {e}")
        print(f"  [WARN] 使用默认值继续")
        meta_b = {"bv": args.collection_b, "title": args.collection_b,
                   "uploader": "?", "total_pages": 0, "total_duration": 0,
                   "total_duration_str": "?", "pages": []}

    for label, m in [("A", meta_a), ("B", meta_b)]:
        print(f"  [{label}] {m['title'][:50]}")
        print(f"       BV: {m['bv']} | {m['total_pages']}P | {m['total_duration_str']}")
        print(f"       UP主: {m['uploader']}")

    if meta_a["total_duration"] > 0 and meta_b["total_duration"] > 0:
        print(f"\n  [TIME]  预计GPU转写时间: ~{int((meta_a['total_duration'] + meta_b['total_duration']) / 3600 * 1.5)}h")
    if dry_run:
        print(f"  [DRY]  [DRY RUN] 模式，不会执行实际操作")

    if dry_run and not args.compare_only:
        print()
        return

    # ── 阶段2：批处理两个合集 ──
    if not args.compare_only:
        col_a_dir = output_root / f"collection_A_{args.collection_a}"
        col_b_dir = output_root / f"collection_B_{args.collection_b}"

        print(f"\n{'='*60}")
        print("  阶段2：处理合集A")
        print(f"{'='*60}")
        summaries_a = process_collection(meta_a, col_a_dir, dry_run=dry_run, scene=args.scene)

        print(f"\n{'='*60}")
        print("  阶段3：处理合集B")
        print(f"{'='*60}")
        summaries_b = process_collection(meta_b, col_b_dir, dry_run=dry_run, scene=args.scene)
    else:
        # --compare-only 模式：从已有文件读取
        summaries_a_path = output_root / f"collection_A_{args.collection_a}_summaries.json"
        summaries_b_path = output_root / f"collection_B_{args.collection_b}_summaries.json"
        
        if not summaries_a_path.exists() or not summaries_b_path.exists():
            print(f"[错误] --compare-only 模式但未找到摘要文件")
            print(f"  需要: {summaries_a_path}")
            print(f"  需要: {summaries_b_path}")
            sys.exit(1)
        
        summaries_a = json.loads(summaries_a_path.read_text(encoding="utf-8"))
        summaries_b = json.loads(summaries_b_path.read_text(encoding="utf-8"))
        print(f"\n[--compare-only] 已读取 {len(summaries_a)} + {len(summaries_b)} 份摘要")

    # ── 阶段4：AI横向比对 ──
    if dry_run:
        print(f"\n{'='*60}")
        print("  阶段4：AI横向比对 [DRY RUN - 跳过]")
        print(f"{'='*60}")
        return

    print(f"\n{'='*60}")
    print("  阶段4：AI横向比对...")
    print(f"{'='*60}")

    try:
        from comparison_prompt import compare_collections
        report = compare_collections(
            summaries_a=summaries_a,
            summaries_b=summaries_b,
            meta_a=meta_a,
            meta_b=meta_b,
        )
        report_path = output_root / "comparison_report.md"
        report_path.write_text(report, encoding="utf-8")
        print(f"\n  [OK]  比对报告已保存: {report_path}")
    except ImportError:
        print(f"\n  [WARN]  比对模块 (comparison_prompt.py) 尚未就绪")
        print(f"     请先实现 tools/comparison_prompt.py")
        print(f"     摘要数据已保存，可以后续手动比对")
    except Exception as e:
        print(f"\n  [FAIL]  比对失败: {e}")
        import traceback
        traceback.print_exc()

    print(f"\n{'='*60}")
    print(f"  完成！")
    print(f"  输出目录: {output_root}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
