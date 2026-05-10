"""
比对模块：AI 逐维度评分 + 生成数据型比对报告

复用 summarizer.py 的 API 调用机制（_call_openai / _call_minimax）
与现有项目的 API Key 配置保持完全一致。
"""

import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import time

from summarizer import summarize_with_api, _get_api_key

# =============================================
#  比对维度定义
# =============================================

DIMENSIONS = [
    {
        "key": "coverage",
        "label": "知识覆盖面",
        "description": "是否覆盖高数（下）全部重要章节（向量与空间几何、多元函数微分、重积分、曲线曲面积分、无穷级数、微分方程）",
        "weight": 1.0,
    },
    {
        "key": "depth",
        "label": "概念溯源深度",
        "description": "是否讲解'为什么'——讲公式的同时是否推导来源、解释数学直觉，还是只讲'怎么用'",
        "weight": 1.2,
    },
    {
        "key": "examples",
        "label": "例题梯度与实战性",
        "description": "例题数量、难度递进（基础→综合→考试难度）、与实际考题的关联度",
        "weight": 1.0,
    },
    {
        "key": "coherence",
        "label": "体系连贯性",
        "description": "各讲之间的前后衔接是否自然，知识点之间的逻辑关系是否清晰，有没有跳跃或断层",
        "weight": 1.0,
    },
    {
        "key": "intuition",
        "label": "直觉辅助手段",
        "description": "运用几何图形、类比、实际案例等帮助建立数学直觉的程度",
        "weight": 0.8,
    },
    {
        "key": "density",
        "label": "讲解密度",
        "description": "单位时间内的有效信息量——是否注水、重复、废话多",
        "weight": 0.8,
    },
    {
        "key": "exam_fit",
        "label": "考试适配度",
        "description": "是否直接对应考点和常考题型，对期末/考研等应试场景的针对性",
        "weight": 1.0,
    },
]


def _extract_fields(summary_text: str) -> dict:
    """从结构化摘要中提取关键字段"""
    fields = {
        "concepts": "",
        "depth": "",
        "method": "",
        "tools": "",
        "evaluation": "",
    }
    for line in summary_text.split("\n"):
        line = line.strip()
        if line.startswith("【核心知识点】"):
            fields["concepts"] = line[7:].strip()[:100]
        elif line.startswith("【概念深度】"):
            fields["depth"] = line[6:].strip()[:80]
        elif line.startswith("【讲解方式】"):
            fields["method"] = line[6:].strip()[:80]
        elif line.startswith("【辅助理解工具】"):
            fields["tools"] = line[8:].strip()[:80]
        elif line.startswith("【整体评价】"):
            fields["evaluation"] = line[6:].strip()[:100]
    return fields


def _build_compact_table(summaries: list[dict]) -> str:
    """将逐P摘要压缩为紧凑表格"""
    rows = []
    rows.append("P | 标题 | 时长 | 核心知识 | 深度 | 讲解方式 | 辅助手段 | 评价")
    rows.append("--|------|------|----------|------|----------|----------|------")
    for s in summaries:
        page = s.get("page", "?")
        part = s.get("part", "")
        dur = s.get("duration_str", "")
        text = s.get("summary_text", "")
        f = _extract_fields(text)
        part_short = part[:25].replace("|", "/")
        rows.append(
            f"P{page}|{part_short}|{dur}|"
            f"{f['concepts'][:40]}|{f['depth'][:20]}|"
            f"{f['method'][:20]}|{f['tools'][:15]}|"
            f"{f['evaluation'][:40]}"
        )
    return "\n".join(rows)


def _build_comparison_prompt(
    summaries_a: list[dict],
    summaries_b: list[dict],
    meta_a: dict,
    meta_b: dict,
) -> str:
    """构造紧凑型比对 prompt（先提取结构化字段，再送LLM）"""
    table_a = _build_compact_table(summaries_a)
    table_b = _build_compact_table(summaries_b)

    prompt = (
        f"# 双合集AI横向比对\n\n"
        f"你是数学教育评估专家。基于以下两个高数(下)视频合集的数据表格，"
        f"进行7维度量化比对（1-10分），输出 JSON。\n\n"
        f"### 合集A：{meta_a['title'][:40]}\n"
        f"{meta_a['total_pages']}P / {meta_a['total_duration_str']} / 精讲课\n"
        f"```\n{table_a}\n```\n\n"
        f"### 合集B：{meta_b['title'][:40]}\n"
        f"{meta_b['total_pages']}P / {meta_b['total_duration_str']} / 速成课\n"
        f"```\n{table_b}\n```\n\n"
        f"## 评分维度\n"
    )
    for d in DIMENSIONS:
        prompt += f"- {d['label']}: {d['description']}\n"

    prompt += (
        f"\n## 输出JSON格式\n"
        f"{{\"dimension_scores\":[{{\"key\":\"coverage\",\"label\":\"知识覆盖面\","
        f"\"score_a\":8,\"score_b\":6,\"reason_a\":\"...\",\"reason_b\":\"...\","
        f"\"gap_analysis\":\"...\"}}, ...(7个)],"
        f"\"summary\":{{\"total_score_a\":56,\"total_score_b\":48,"
        f"\"recommendation\":\"...\",\"best_for\":{{\"collection_a\":\"...\","
        f"\"collection_b\":\"...\"}}}},"
        f"\"radar_data\":{{\"labels\":[\"知识覆盖面\",...],\"scores_a\":[8,...],\"scores_b\":[6,...]}}}}\n\n"
        f"评分严苛，8=优秀，6=中等，4=不足。"
    )
    return prompt


def _parse_json_from_response(response: str) -> dict:
    """从 LLM 响应中提取 JSON 对象"""
    # 尝试直接解析
    response = response.strip()
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        pass

    # 尝试从 ```json 块中提取
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # 尝试从 ``` 块中提取
    m = re.search(r"```\s*(\{.*?\})\s*```", response, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # 尝试找第一个 { 和最后一个 }
    start = response.find("{")
    end = response.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(response[start : end + 1])
        except json.JSONDecodeError:
            pass

    raise ValueError(f"无法从响应中提取 JSON:\n{response[:500]}")


def _generate_report_from_json(result: dict, meta_a: dict, meta_b: dict) -> str:
    """将 JSON 比对结果渲染为可读的 Markdown 报告"""
    lines = [
        f"# 双合集AI横向比对报告",
        f"",
        f"**生成时间:** {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"",
        f"---",
        f"",
        f"## 合集概览",
        f"",
        f"| 维度 | 合集A | 合集B |",
        f"|---|---|---|",
        f"| **标题** | {meta_a['title'][:50]} | {meta_b['title'][:50]} |",
        f"| **UP主** | {meta_a['uploader']} | {meta_b['uploader']} |",
        f"| **分P数** | {meta_a['total_pages']} | {meta_b['total_pages']} |",
        f"| **总时长** | {meta_a['total_duration_str']} | {meta_b['total_duration_str']} |",
        f"| **风格定位** | 精讲课程 | 期末速成冲刺 |",
        f"",
        f"---",
        f"",
        f"## 维度评分",
        f"",
        f"| 维度 | 合集A | 合集B | 差距 |",
        f"|---|---|---|---|",
    ]

    dim_scores = result.get("dimension_scores", [])

    # 按分数差距排序
    sorted_dims = sorted(dim_scores, key=lambda d: abs(d.get("score_a", 0) - d.get("score_b", 0)), reverse=True)

    for d in sorted_dims:
        label = d.get("label", d.get("key", "?"))
        sa = d.get("score_a", "?")
        sb = d.get("score_b", "?")
        gap = sa - sb if isinstance(sa, (int, float)) and isinstance(sb, (int, float)) else "?"
        gap_str = f"+{gap}" if isinstance(gap, (int, float)) and gap > 0 else str(gap)
        lines.append(f"| **{label}** | **{sa}/10** | **{sb}/10** | {gap_str} |")

    lines.extend(["", "---", "", "## 评分依据明细", ""])
    for d in sorted_dims:
        label = d.get("label", d.get("key", "?"))
        sa = d.get("score_a", "?")
        sb = d.get("score_b", "?")
        reason_a = d.get("reason_a", "无")
        reason_b = d.get("reason_b", "无")
        gap_analysis = d.get("gap_analysis", "")
        lines.extend([
            f"### {label}（A={sa}/10, B={sb}/10）",
            f"",
            f"**合集A 依据:** {reason_a}",
            f"",
            f"**合集B 依据:** {reason_b}",
            f"",
            f"**差距分析:** {gap_analysis}",
            f"",
            f"---",
            f"",
        ])

    # 总结
    summary = result.get("summary", {})
    lines.extend([
        f"## 综合结论",
        f"",
        f"| 项目 | 合集A | 合集B |",
        f"|---|---|---|",
        f"| **总分** | **{summary.get('total_score_a', '?')}/70** | **{summary.get('total_score_b', '?')}/70** |",
        f"| **适合人群** | {summary.get('best_for', {}).get('collection_a', '?')} | {summary.get('best_for', {}).get('collection_b', '?')} |",
        f"",
        f"**综合推荐:** {summary.get('recommendation', '?')}",
        f"",
    ])

    # 雷达图数据（原始 JSON 嵌入以供可视化使用）
    radar = result.get("radar_data", {})
    lines.extend([
        f"---",
        f"",
        f"## 雷达图数据（可用于可视化）",
        f"",
        f"```json",
        json.dumps(radar, ensure_ascii=False, indent=2),
        f"```",
        f"",
    ])

    return "\n".join(lines)


# =============================================
#  主入口
# =============================================

# =============================================
#  数学视频专用结构化摘要
# =============================================

MATH_SUMMARY_INSTRUCTION = (
    "你是一位数学教育评估专家。以下是一节高等数学(下)教学视频的转写文本。"
    "请用中文生成一份结构化分析摘要，必须包含以下内容：\n"
    "1. 本节核心知识点（列出具体的概念、定理、公式名称）\n"
    "2. 讲解方式（公式推导/几何直观/例题驱动/纯板书/其他）\n"
    "3. 概念深度判断（基础概念引入/公式推导证明/综合应用/应试技巧）\n"
    "4. 是否使用了辅助理解的工具（几何图形/类比案例/实际应用场景/完全没有）\n"
    "5. 本讲在你已学内容中的定位（新知识引入/知识点串联/综合复习）\n"
    "6. 整体评价（一至两句话，包括信息密度、适合人群）\n\n"
    "格式要求：每个点用【】标记，简洁直接，不要超过600字。"
)


MAX_SUMMARY_RETRIES = 3


def summarize_math_lecture(transcript: str, title: str = "") -> str:
    """
    对数学教学视频的转写文本，生成结构化摘要。
    使用现有 API 通道，带自动重试。
    """
    text_for_api = f"## 视频标题\n{title}\n\n## 转写文本\n{transcript}"

    for attempt in range(1, MAX_SUMMARY_RETRIES + 1):
        try:
            # 复用现有的 summarize_with_api，但通过 general scene
            # 我们在 instruction 中已经包含了所有要求，scene 不重要
            from config import SUMMARIZER_API
            from summarizer import _call_minimax, _call_openai

            if SUMMARIZER_API == "minimax":
                # 创建自定义调用
                api_key = _get_api_key("minimax")
                if not api_key:
                    raise RuntimeError("未找到 MiniMax API Key")

                from config import MINIMAX_MODEL
                import urllib.request
                import json as _json

                def _api_post(url, payload, api_key):
                    data = _json.dumps(payload).encode("utf-8")
                    req = urllib.request.Request(
                        url, data=data,
                        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=180) as resp:
                        return _json.loads(resp.read().decode("utf-8"))

                result = _api_post(
                    "https://api.minimaxi.com/anthropic/v1/messages",
                    {
                        "model": MINIMAX_MODEL,
                        "max_tokens": 2048,
                        "system": "你是一个数学教育分析专家。用中文回复，简洁有条理。",
                        "messages": [
                            {"role": "user", "content": f"{MATH_SUMMARY_INSTRUCTION}\n\n{text_for_api}"},
                        ],
                        "stream": False,
                    },
                    api_key,
                )
                texts = [b["text"] for b in result.get("content", []) if b.get("type") == "text"]
                if texts:
                    return texts[0].strip()
                raise RuntimeError(f"MiniMax 返回异常: {result}")

            else:
                # OpenAI 兼容接口
                from config import DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, OPENAI_BASE_URL, OPENAI_MODEL
                import urllib.request
                import json as _json

                api_key = _get_api_key(SUMMARIZER_API)
                if not api_key:
                    raise RuntimeError(f"未配置 {SUMMARIZER_API} 的 API Key")

                if SUMMARIZER_API == "deepseek":
                    base_url = DEEPSEEK_BASE_URL.rstrip("/")
                    model = DEEPSEEK_MODEL
                else:
                    base_url = OPENAI_BASE_URL.rstrip("/")
                    model = OPENAI_MODEL

                def _api_post(url, payload, api_key):
                    data = _json.dumps(payload).encode("utf-8")
                    req = urllib.request.Request(
                        url, data=data,
                        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=180) as resp:
                        return _json.loads(resp.read().decode("utf-8"))

                result = _api_post(
                    f"{base_url}/v1/chat/completions",
                    {
                        "model": model,
                        "max_tokens": 2048,
                        "messages": [
                            {"role": "system", "content": "你是一个数学教育分析专家。用中文回复，简洁有条理。"},
                            {"role": "user", "content": f"{MATH_SUMMARY_INSTRUCTION}\n\n{text_for_api}"},
                        ],
                        "stream": False,
                        "temperature": 0.5,
                    },
                    api_key,
                )
                choices = result.get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", "").strip()
                raise RuntimeError(f"API 返回异常: {result}")

        except Exception as e:
            print(f"    [API重试] 第{attempt}次失败: {e}")
            if attempt < MAX_SUMMARY_RETRIES:
                wait_time = 2 ** attempt
                print(f"    [API重试] {wait_time}s后重试...")
                time.sleep(wait_time)
            else:
                print(f"    [API重试] 已达最大重试次数，返回空摘要")
                return (
                    f"【本节核心知识点】摘要生成失败\n"
                    f"【讲解方式】-\n"
                    f"【概念深度判断】-\n"
                    f"【辅助工具】-\n"
                    f"【本讲定位】-\n"
                    f"【整体评价】AI摘要生成失败，请检查转录文本或API配置"
                )

    return ""


def compare_collections(
    summaries_a: list[dict],
    summaries_b: list[dict],
    meta_a: dict,
    meta_b: dict,
) -> str:
    """
    AI 横向比对两个合集，返回完整 Markdown 报告。

    Args:
        summaries_a: 合集A的逐P摘要列表
        summaries_b: 合集B的逐P摘要列表
        meta_a: 合集A的元数据
        meta_b: 合集B的元数据

    Returns:
        Markdown 格式的比对报告
    """
    print(f"  [比对] 构建 prompt...")
    prompt = _build_comparison_prompt(summaries_a, summaries_b, meta_a, meta_b)

    # 估算 token 量
    total_chars = len(prompt)
    print(f"  [比对] prompt 长度: {total_chars} 字符 (~{total_chars // 4} tokens)")

    print(f"  [比对] 调用 AI API (max_tokens=4096)...")
    try:
        response = summarize_with_api(prompt, scene="general", max_tokens=4096)
    except RuntimeError as e:
        print(f"  [比对] API 调用失败: {e}")
        print(f"  [比对] 将使用本地 fallback（无AI评分）")
        return _fallback_report(summaries_a, summaries_b, meta_a, meta_b)

    print(f"  [比对] 解析结果...")
    response_len = len(response)
    print(f"  [比对] 响应长度: {response_len} 字符")

    try:
        result = _parse_json_from_response(response)
        print(f"  [比对] JSON 解析成功，含 {len(result.get('dimension_scores', []))} 个维度评分")
        report = _generate_report_from_json(result, meta_a, meta_b)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"  [比对] JSON 解析失败: {e}")
        print(f"  [比对] 将原始响应保存为报告")
        report = (
            f"# 双合集AI横向比对报告\n\n"
            f"**注意：AI 返回了非结构化文本，以下是原始响应：**\n\n"
            f"{response}"
        )

    return report


def _fallback_report(summaries_a, summaries_b, meta_a, meta_b) -> str:
    """API 不可用时的降级报告"""
    lines = [
        f"# 双合集横向比对报告 [降级模式]",
        f"",
        f"**生成时间:** {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"",
        f"## 说明",
        f"AI API 暂时不可用，以下为基于元数据的客观对比：",
        f"",
        f"## 基础数据对比",
        f"",
        f"| 维度 | 合集A | 合集B |",
        f"|---|---|---|",
        f"| **BV** | {meta_a['bv']} | {meta_b['bv']} |",
        f"| **标题** | {meta_a['title'][:50]} | {meta_b['title'][:50]} |",
        f"| **UP主** | {meta_a['uploader']} | {meta_b['uploader']} |",
        f"| **分P数** | {meta_a['total_pages']} | {meta_b['total_pages']} |",
        f"| **总时长** | {meta_a['total_duration_str']} | {meta_b['total_duration_str']} |",
        f"| **平均每P时长** | {meta_a['total_duration']//meta_a['total_pages']}s | {meta_b['total_duration']//meta_b['total_pages']}s |",
        f"| **风格定位** | 精讲课程 | 期末速成冲刺 |",
        f"",
        f"## 每P摘要",
        f"",
    ]
    for label, summaries, meta in [("合集A", summaries_a, meta_a), ("合集B", summaries_b, meta_b)]:
        lines.append(f"### {label}")
        for s in summaries:
            page = s.get("page", "?")
            part = s.get("part", "")
            dur = s.get("duration_str", "")
            summary_text = s.get("summary_text", "")
            lines.append(f"\n**P{page} ({dur})** {part}")
            lines.append(f"> {summary_text[:200].replace(chr(10), ' ').strip()}")
    lines.append("")
    return "\n".join(lines)
