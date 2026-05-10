#!/usr/bin/env python3
"""
分阶段管线：下载→转录(5P批)→摘要→全量比对

用法:
  python tools/run_pipeline.py --stage download    # 下载全部音频
  python tools/run_pipeline.py --stage transcribe  # 5P一批转写
  python tools/run_pipeline.py --stage summarize   # DeepSeek结构化摘要
  python tools/run_pipeline.py --stage compare     # 全量比对
  python tools/run_pipeline.py --stage all         # 全部（默认）
"""

import argparse
import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from bilibili_downloader import get_video_pages, get_video_info
from config import TEMP_DIR

OUTPUT = _PROJECT_ROOT / "output" / "pipeline"
AUDIO_DIR = OUTPUT / "audio"      # 音频仓库
TRANS_DIR = OUTPUT / "transcripts" # 转录文本
SUMM_DIR = OUTPUT / "summaries"    # 摘要
META_FILE = OUTPUT / "meta.json"  # 合集元数据

BATCH_SIZE = 5


# =============================================
#  元数据
# =============================================

COLLECTIONS = {
    "A": {"bv": "BV1DE421M7AZ", "label": "框框老师精讲"},
    "B": {"bv": "BV1qXN9z5E8V", "label": "一高数速成"},
}


def load_meta() -> dict:
    """获取或缓存两个合集的元数据"""
    if META_FILE.exists():
        return json.loads(META_FILE.read_text(encoding="utf-8"))

    meta = {}
    for key, cfg in COLLECTIONS.items():
        bv = cfg["bv"]
        info = get_video_info(bv)
        pages = get_video_pages(bv)
        meta[key] = {
            "bv": bv,
            "title": info["title"],
            "uploader": info["uploader"],
            "pages": pages,
        }
        print(f"  [{key}] {info['title'][:40]} — {len(pages)}P")

    META_FILE.parent.mkdir(parents=True, exist_ok=True)
    META_FILE.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


# =============================================
#  阶段1：下载全部音频
# =============================================

def stage_download():
    """下载两个合集所有分P的音频（不转录）"""
    from bilibili_downloader import download_page_audio

    meta = load_meta()
    total = sum(len(m["pages"]) for m in meta.values())

    print(f"\n{'='*50}")
    print(f"  阶段1：下载全部 {total} 个音频")
    print(f"{'='*50}")

    completed = 0
    for key, m in meta.items():
        for p in m["pages"]:
            page_num = p["page"]
            bv = m["bv"]
            out_path = AUDIO_DIR / key / f"P{page_num:03d}.wav"

            if out_path.exists() and out_path.stat().st_size > 1000:
                print(f"  [{key}] P{page_num} 音频已存在，跳过")
                completed += 1
                continue

            out_path.parent.mkdir(parents=True, exist_ok=True)
            print(f"  [{key}] P{page_num} 下载中 ({p['duration']}s)...")
            try:
                download_page_audio(bv, p["cid"], page_num, out_path)
                completed += 1
            except Exception as e:
                print(f"    [FAIL] {e}")

    print(f"\n  下载完成: {completed}/{total}")


# =============================================
#  阶段2：5P一批转写
# =============================================

def stage_transcribe():
    """5P一批转写，每批结束后等待确认"""
    from transcriber import _setup_cuda_paths, transcribe as do_transcribe

    _setup_cuda_paths()
    meta = load_meta()

    print(f"\n{'='*50}")
    print(f"  阶段2：5P一批转写")
    print(f"{'='*50}")

    for key, m in meta.items():
        pages = m["pages"]
        total = len(pages)

        # 分批
        for batch_start in range(1, total + 1, BATCH_SIZE):
            batch = pages[batch_start - 1 : batch_start - 1 + BATCH_SIZE]
            print(f"\n  [{key}] 批次 P{batch[0]['page']}~P{batch[-1]['page']}")

            for p in batch:
                page_num = p["page"]
                audio_path = AUDIO_DIR / key / f"P{page_num:03d}.wav"
                trans_path = TRANS_DIR / key / f"P{page_num:03d}.txt"
                marker = TRANS_DIR / key / f"P{page_num:03d}.done"

                if marker.exists():
                    print(f"    P{page_num} 已转写，跳过")
                    continue

                if not audio_path.exists():
                    print(f"    P{page_num} 音频不存在，跳过")
                    continue

                print(f"    P{page_num} 转写中 ({p['duration']}s)...")
                t0 = time.time()
                try:
                    text = do_transcribe(audio_path, "lecture")
                    trans_path.parent.mkdir(parents=True, exist_ok=True)
                    trans_path.write_text(text, encoding="utf-8")
                    marker.write_text(datetime.now().isoformat())
                    elapsed = time.time() - t0
                    chars = len(text)
                    print(f"      [OK] {chars} chars, {elapsed:.1f}s")
                except Exception as e:
                    print(f"      [FAIL] {e}")

            # 本批次结束，汇报
            batch_done = sum(
                1 for p in batch
                if (TRANS_DIR / key / f"P{p['page']:03d}.done").exists()
            )
            print(f"    [{key}] 本批 {batch_done}/{len(batch)} 完成")


# =============================================
#  阶段3：DeepSeek结构化摘要
# =============================================

def stage_summarize():
    """逐P生成结构化摘要（使用DeepSeek，1M上下文）"""
    meta = load_meta()

    print(f"\n{'='*50}")
    print(f"  阶段3：DeepSeek结构化摘要")
    print(f"{'='*50}")

    for key, m in meta.items():
        pages = m["pages"]
        for p in pages:
            page_num = p["page"]
            trans_path = TRANS_DIR / key / f"P{page_num:03d}.txt"
            summ_path = SUMM_DIR / key / f"P{page_num:03d}.json"
            marker = SUMM_DIR / key / f"P{page_num:03d}.done"

            if marker.exists():
                print(f"  [{key}] P{page_num} 摘要已存在，跳过")
                continue

            if not trans_path.exists():
                print(f"  [{key}] P{page_num} 转录不存在，跳过")
                continue

            transcript = trans_path.read_text(encoding="utf-8")
            part_name = p.get("part", f"P{page_num}")

            print(f"  [{key}] P{page_num}: {part_name[:30]} ({len(transcript)} chars)")

            # 用DeepSeek生成结构化摘要
            try:
                from summarizer import summarize_with_api

                prompt = (
                    f"你是一位数学教育评估专家。分析以下高等数学(下)教学视频的转写文本。\n"
                    f"请用中文输出严格 JSON 格式（不要markdown代码块），包含以下字段：\n"
                    f"- concepts: 核心知识点列表（字符串）\n"
                    f"- depth: 概念深度（基础引入/公式推导/综合应用/应试技巧）\n"
                    f"- method: 讲解方式（板书/几何/类比/例题驱动等）\n"
                    f"- tools: 辅助理解工具（几何图形/类比/实际案例/无）\n"
                    f"- exam_relevance: 考试针对性（高/中/低）\n"
                    f"- evaluation: 整体评价（一句话，含信息密度和适合人群）\n\n"
                    f"视频标题: {part_name}\n\n"
                    f"转写文本:\n{transcript[:80000]}"
                )

                response = summarize_with_api(prompt, scene="general")
                summ_path.parent.mkdir(parents=True, exist_ok=True)

                # 尝试解析JSON
                try:
                    parsed = json.loads(response.strip())
                    summ_path.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
                except json.JSONDecodeError:
                    # 存原始文本
                    summ_path.write_text(response, encoding="utf-8")

                marker.write_text(datetime.now().isoformat())
                print(f"    [OK]")

            except Exception as e:
                print(f"    [FAIL] {e}")


# =============================================
#  阶段4：全量DeepSeek比对
# =============================================

def stage_compare():
    """将所有转录文本/摘要全量喂给DeepSeek，一次完成评价与比对"""
    from summarizer import summarize_with_api

    meta = load_meta()
    print(f"\n{'='*50}")
    print(f"  阶段4：全量DeepSeek评价与比对")
    print(f"{'='*50}")

    # 构建每个合集的完整内容
    collection_texts = {}
    for key, m in meta.items():
        pages = m["pages"]
        parts = []
        for p in pages:
            page_num = p["page"]
            summ_path = SUMM_DIR / key / f"P{page_num:03d}.json"
            trans_path = TRANS_DIR / key / f"P{page_num:03d}.txt"

            if summ_path.exists():
                summary = json.loads(summ_path.read_text(encoding="utf-8"))
                parts.append(f"--- P{page_num}: {p.get('part', '')} ---\n"
                             f"知识: {summary.get('concepts', '')}\n"
                             f"深度: {summary.get('depth', '')}\n"
                             f"方法: {summary.get('method', '')}\n"
                             f"考试: {summary.get('exam_relevance', '')}\n"
                             f"评价: {summary.get('evaluation', '')}\n")
            elif trans_path.exists():
                # 没有摘要就用转录文本（截取前2000字）
                text = trans_path.read_text(encoding="utf-8")
                parts.append(f"--- P{page_num}: {p.get('part', '')} ---\n"
                             f"{text[:2000]}\n")

        collection_texts[key] = "\n".join(parts)

    # 构建评价标准
    criteria = """
## 评价标准（每项1-10分）

1. 知识覆盖面：是否覆盖高数下全部重要章节
2. 讲解深度：是否讲解公式来源和数学直觉，还是只给结论
3. 例题梯度：从基础到综合的题目递进
4. 体系性：各讲之间的逻辑连贯性
5. 直观辅助：几何/类比等帮助建立数学直觉的程度
6. 信息密度：单位时间的有效内容量
7. 应试针对性：是否直接对应考点题型
"""

    # 构建比对prompt（全量内容）
    prompt = (
        f"你是一位数学教育评估专家。请对以下两个高等数学(下)视频合集进行评价和比对。\n"
        f"{criteria}\n"
        f"请按以下流程输出：\n"
        f"1. 首先给出评价标准的具体说明\n"
        f"2. 然后分别评价每个合集（引用具体讲次作为依据）\n"
        f"3. 最后做横向对比，输出包含以下字段的JSON：\n"
        f"  {{\n"
        f"    \"dimension_scores\": [{{\"key\":\"coverage\",\"label\":\"知识覆盖面\",\"score_a\":8,\"score_b\":6,\"reason_a\":\"...\",\"reason_b\":\"...\",\"gap_analysis\":\"...\"}}, ...(7个)],\n"
        f"    \"summary\": {{\"total_score_a\":56,\"total_score_b\":48,\"recommendation\":\"...\",\"best_for\":{{\"collection_a\":\"...\",\"collection_b\":\"...\"}}}},\n"
        f"    \"radar_data\": {{\"labels\":[\"知识覆盖面\",\"讲解深度\",\"例题梯度\",\"体系性\",\"直观辅助\",\"信息密度\",\"应试针对性\"],\"scores_a\":[8,7,...],\"scores_b\":[6,5,...]}}\n"
        f"  }}\n\n"
        f"========================================\n"
        f"合集A（框框老师精讲课）：\n"
        f"总览: {meta['A']['title']} | {len(meta['A']['pages'])}P\n\n"
        f"{collection_texts['A']}\n\n"
        f"========================================\n"
        f"合集B（一高数速成课）：\n"
        f"总览: {meta['B']['title']} | {len(meta['B']['pages'])}P\n\n"
        f"{collection_texts['B']}\n\n"
        f"========================================\n"
        f"请给出评价和比对。"
    )

    prompt_path = OUTPUT / "comparison_prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    print(f"  Prompt已保存 ({len(prompt)} chars, ~{len(prompt)//4} tokens)")

    print(f"  调用DeepSeek API...")
    try:
        response = summarize_with_api(prompt, scene="general", max_tokens=8192)
        report_path = OUTPUT / "comparison_report.md"
        report_path.write_text(response, encoding="utf-8")
        print(f"  报告已保存: {report_path}")
        print(f"  响应长度: {len(response)} chars")
    except Exception as e:
        print(f"  [FAIL] {e}")


# =============================================
#  主入口
# =============================================

def main():
    parser = argparse.ArgumentParser(description="分阶段管线")
    parser.add_argument("--stage", default="all",
                        choices=["all", "download", "transcribe", "summarize", "compare"])
    args = parser.parse_args()

    stages = []
    if args.stage == "all":
        stages = ["download", "transcribe", "summarize", "compare"]
    else:
        stages = [args.stage]

    for stage in stages:
        t0 = time.time()
        if stage == "download":
            stage_download()
        elif stage == "transcribe":
            stage_transcribe()
        elif stage == "summarize":
            stage_summarize()
        elif stage == "compare":
            stage_compare()
        elapsed = time.time() - t0
        print(f"\n  阶段 [{stage}] 完成，耗时 {elapsed/60:.1f}min\n")


if __name__ == "__main__":
    main()
