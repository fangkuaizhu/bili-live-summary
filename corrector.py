"""
LLM 二轮纠错模块

对 Whisper 转写文本进行 AI 纠正，修复专有名词、术语等识别错误。
不改动句式结构、不删改内容、不润色文风。
"""

import requests

from config import (
    SUMMARIZER_API,
    MINIMAX_API_KEY, MINIMAX_MODEL,
    DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL,
    OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL,
)


def _get_correction_api_key(platform: str) -> str:
    """获取指定平台的 API Key"""
    if platform == "minimax":
        return MINIMAX_API_KEY
    elif platform == "deepseek":
        return DEEPSEEK_API_KEY
    else:  # openai
        return OPENAI_API_KEY


CORRECTION_PROMPT = """你是一个 Whisper 语音转写纠错助手。请对以下转写文本进行同音/近音错字的纠正。

允许修改的项目（仅限上下文证据明确的错误，不确定时保留原文，宁缺毋滥）：
1. 专有名词（人名、地名、机构名、品牌名）
2. 医学术语、科技术语
3. 产品名、型号名
4. 专业缩写、英文术语被错误音译的情况
5. 明显的同音错字：结合上下文判断，包括量词、动词、常用搭配中的同音字错误
   示例：「优化了一楼方案」→「优化了一版方案」、「换紧焦虑」→「缓解焦虑」、「重疗风险」→「重疾风险」

严格禁止：
- 改动原有句式结构
- 删除或缩短任何内容
- 润色文风、调整语序
- 添加原文本中没有的信息
- 修正语法错误（除非是同音错字导致的）

输出要求：
- 仅返回纠正后的完整文本
- 不要添加任何说明、标记或注释
- 文本结构、段落、标点保持与原文本完全一致"""


def _split_text(text: str, max_chunk: int = 6000) -> list:
    """按段落将长文本分成多个批次，每批不超过 max_chunk 字符

    max_chunk=6000：保证每批纠错输出（与原文本等长）在
    max_tokens=8192 的安全范围内，避免 LLM 输出被截断。
    """
    paragraphs = text.split("\n")
    chunks = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 1 > max_chunk and current:
            chunks.append(current)
            current = para
        else:
            if current:
                current += "\n" + para
            else:
                current = para
    if current:
        chunks.append(current)
    return chunks


def _call_minimax_correction(text: str) -> str:
    """调用 MiniMax API 进行纠错（Anthropic 兼容接口）"""
    api_key = _get_correction_api_key("minimax")
    if not api_key:
        print("[纠错] 未配置 API key，跳过纠错")
        return text

    try:
        resp = requests.post(
            "https://api.minimaxi.com/anthropic/v1/messages",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": MINIMAX_MODEL,
                "max_tokens": 8192,
                "system": CORRECTION_PROMPT,
                "messages": [
                    {"role": "user", "content": text}
                ],
                "stream": False,
            },
            timeout=60,
        )
        if resp.status_code != 200:
            print(f"[纠错] API 返回非 200: {resp.status_code}，跳过纠错")
            return text
        result = resp.json()
        texts = [b["text"] for b in result.get("content", []) if b.get("type") == "text"]
        if texts:
            return texts[0].strip()
        print(f"[纠错] MiniMax 返回异常: {result}")
        return text
    except Exception as e:
        print(f"[纠错] API 调用失败: {e}，跳过纠错")
        return text


def _call_openai_compatible_correction(text: str) -> str:
    """调用 OpenAI 兼容接口（DeepSeek / OpenAI）进行纠错"""
    platform = SUMMARIZER_API
    api_key = _get_correction_api_key(platform)
    if not api_key:
        print("[纠错] 未配置 API key，跳过纠错")
        return text

    if platform == "deepseek":
        base_url = DEEPSEEK_BASE_URL.rstrip("/")
        model = DEEPSEEK_MODEL
    else:
        base_url = OPENAI_BASE_URL.rstrip("/")
        model = OPENAI_MODEL

    try:
        resp = requests.post(
            f"{base_url}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 8192,
                "messages": [
                    {"role": "system", "content": CORRECTION_PROMPT},
                    {"role": "user", "content": text},
                ],
                "stream": False,
                "temperature": 0.3,
            },
            timeout=60,
        )
        if resp.status_code != 200:
            print(f"[纠错] API 返回非 200: {resp.status_code}，跳过纠错")
            return text
        result = resp.json()
        choices = result.get("choices", [])
        if choices:
            return choices[0].get("message", {}).get("content", "").strip()
        print(f"[纠错] API 返回异常: {result}")
        return text
    except Exception as e:
        print(f"[纠错] API 调用失败: {e}，跳过纠错")
        return text


def correct_transcript(
    text: str,
    scene: str = "general",
    title: str = "",
    use_api: bool = True,
) -> str:
    """对转写文本进行 LLM 二轮纠错

    只纠正专有名词、人名、机构名、医学术语、产品名、专业缩写等 Whisper 转写错误。
    不改动句式结构、不删改内容、不润色文风。

    Args:
        text: 转写文本
        scene: 场景类型（保留接口，当前未在 prompt 中使用）
        title: 视频/直播标题（保留接口，当前未在 prompt 中使用）
        use_api: 是否调用 API。False 时直接返回原文。

    Returns:
        纠正后的文本。API 不可用或调用失败时返回原文，不抛异常。
    """
    if not use_api or not text or not text.strip():
        return text

    # 超长文本分批处理（长于 8000 字符即分批，每批 6000，
    # 防止 max_tokens 截断导致后半段丢失）
    if len(text) > 8000:
        chunks = _split_text(text)
        corrected_chunks = []
        for i, chunk in enumerate(chunks):
            print(f"[纠错] 批次 {i + 1}/{len(chunks)} ({len(chunk)} 字符)")
            if SUMMARIZER_API == "minimax":
                corrected = _call_minimax_correction(chunk)
            else:
                corrected = _call_openai_compatible_correction(chunk)
            corrected_chunks.append(corrected)
        return "\n".join(corrected_chunks)

    # 正常长度直接调用
    if SUMMARIZER_API == "minimax":
        return _call_minimax_correction(text)
    else:
        return _call_openai_compatible_correction(text)
