"""
配置模块：路径、提示词、场景设置

API Key 和平台选择优先从 config.local.json 读取，
找不到则 fallback 到环境变量或系统默认配置。
"""

import json
import os
from pathlib import Path

# ========== 基础路径 ==========
PROJECT_DIR = Path(__file__).parent
TEMP_DIR = PROJECT_DIR / "temp"
OUTPUT_DIR = PROJECT_DIR / "output"

TEMP_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)


# ========== 本地配置文件加载 ==========

_LOCAL_CONFIG = {}
_local_config_path = PROJECT_DIR / "config.local.json"
if _local_config_path.exists():
    try:
        _LOCAL_CONFIG = json.loads(_local_config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError):
        pass


def _get(key: str, default=""):
    """从本地配置取值，支持点号路径如 'api.minimax.api_key'"""
    parts = key.split(".")
    val = _LOCAL_CONFIG
    for p in parts:
        if isinstance(val, dict):
            val = val.get(p, {})
        else:
            return default
    return val if val else default


# ========== Whisper 设置 ==========
WHISPER_MODEL = "large-v3-turbo"
WHISPER_DEVICE = "cuda"
WHISPER_COMPUTE_TYPE = "float16"
WHISPER_BEAM_SIZE = 3       # 解码搜索宽度，越小越快（3=快速, 5=平衡, 8=最准）
# 转写模式: "batch" = 下载完再转写 / "stream" = 边下边转写
WHISPER_MODE = "batch"


# ========== 总结 API 设置 ==========

SUMMARIZER_API = _get("api.platform", "minimax")

# MiniMax
MINIMAX_MODEL = _get("api.minimax.model", "MiniMax-M2.7")
MINIMAX_API_KEY = _get("api.minimax.api_key", "")

# DeepSeek / OpenAI 兼容平台
DEEPSEEK_API_KEY = _get("api.deepseek.api_key", "")
DEEPSEEK_BASE_URL = _get("api.deepseek.base_url", "https://api.deepseek.com")
DEEPSEEK_MODEL = _get("api.deepseek.model", "deepseek-chat")

OPENAI_API_KEY = _get("api.openai.api_key", "")
OPENAI_BASE_URL = _get("api.openai.base_url", "https://api.openai.com")
OPENAI_MODEL = _get("api.openai.model", "gpt-4o-mini")


# ========== 场景预设 ==========
SCENE_PROMPTS = {
    "lecture": {
        "description": "讲座/课堂/学术直播",
        "initial_prompt": (
            "以下是中文讲座、课堂或学术报告的录音转写。"
            "内容涉及专业知识、学术讨论。"
            "说话人语速适中，使用正式、专业的语言。"
            "可能出现专业术语、英文缩写、公式描述。"
            "请准确转写包含专业术语的语句，保留英文术语原样。"
        ),
        "summary_instruction": (
            "你刚刚听了一场讲座直播。请用中文生成一份简报，包含：\n"
            "1. 讲座主题与主讲人身份推断\n"
            "2. 核心观点与重点内容（分点列出）\n"
            "3. 涉及的关键概念和术语\n"
            "4. 互动环节的问答要点（如有）\n"
            "5. 整体评价（信息密度、适合什么人群）\n\n"
            "语言简洁，直击重点，像给没去听的人看的一份速记。"
        ),
    },
    "streamer": {
        "description": "游戏主播/VTB/娱乐直播",
        "initial_prompt": (
            "以下是中文直播的录音转写。"
            "内容可能涉及游戏实况、日常聊天、唱歌、读弹幕等。"
            "说话人语气随意，可能有方言、网络用语、语速变化。"
            "可能有BGM、音效等背景噪音。"
            "请准确转写对话内容。"
        ),
        "summary_instruction": (
            "你刚刚看了某位主播的直播回放片段。请用中文生成一份直播简报，包含：\n"
            "1. 直播内容摘要（在干什么、播了什么内容）\n"
            "2. 主播提到的重要话题或趣事\n"
            "3. 直播氛围（轻松/激烈/翻车/整活等）\n"
            "4. 观众互动情况（弹幕反应、礼物等）\n"
            "5. 是否有什么重大/有趣的时刻\n\n"
            "语言轻松、有网感，像给错过直播的朋友口述。"
        ),
    },
    "general": {
        "description": "通用场景",
        "initial_prompt": (
            "以下是中文录音转写。请准确转写所有内容，"
            "保持标点合理，保留口语表达中的语气词。"
        ),
        "summary_instruction": (
            "请根据以下音频转写内容，用中文生成一份简要总结，"
            "概括核心内容。分点列出关键信息。"
        ),
    },
}


def get_scene_config(scene: str) -> dict:
    """获取场景配置，回退到 general"""
    if scene == "":
        return SCENE_PROMPTS
    return SCENE_PROMPTS.get(scene, SCENE_PROMPTS["general"])
