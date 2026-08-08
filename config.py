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
WHISPER_COMPUTE_TYPE = "float32"  # Blackwell RTX 5070 Ti 需 float32 规避 cuBLAS 不兼容（12GB GDDR7 可容纳）
WHISPER_BEAM_SIZE = 3       # 解码搜索宽度，越小越快（3=快速, 5=平衡, 8=最准）
# 转写模式: "batch" = 下载完再转写 / "stream" = 边下边转写
WHISPER_MODE = "batch"
# 热词注入: 是否启用热词功能
HOTWORDS_ENABLED = True


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


# ========== 长文本分段总结 ==========
# 转写文本超过该长度（字符，中文约 1 字符 ≈ 1 token）时，按段总结再合并
SUMMARY_CHUNK_TOKENS = _get("summary.chunk_tokens", 15000)
# 相邻段落之间的原文重叠长度（字符），用于上下文衔接
SUMMARY_OVERLAP_TOKENS = _get("summary.overlap_tokens", 1500)
# 段间衔接方式: "summary"=前段摘要(方案A) / "overlap"=前段尾部原文(方案B) / "both"=两者结合
SUMMARY_CONTEXT_MODE = _get("summary.context_mode", "both")
# 总结 API 输出 token 上限（段总结与最终合并共用）
SUMMARY_MAX_OUTPUT_TOKENS = _get("summary.max_output_tokens", 4096)


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
            "你刚刚看了某位主播的直播回放。请用中文生成一份【时间线式直播简报】，"
            "重点讲清楚什么时候发生了什么。输出结构如下：\n"
            "1. 📍 直播概况：两三句话概括整场直播（主题、氛围、主播人设），不列点。\n"
            "2. 🕐 时间线（主体部分）：把直播按时间段分段（每段约 5-15 分钟，"
            "话题变化处可更细），每段用【[起始 - 结束] 段落小标题】开头（如 【[05:32 - 15:40] 第一局排位，心兽讨论】），"
            "段内按时间顺序逐条写发生了什么，重要事件必须锚定具体时间戳，格式 [mm:ss] 内容。"
            "段落小标题要概括该时间段的主线。\n"
            "3. 🔑 关键角色与术语：列出直播中出现的角色/术语/梗，每个标注首次提及的时间戳。\n"
            "4. 💥 高光时刻：按时间列出 3-6 个最精彩/最有趣/最重要的时刻，每个带时间戳和一句话描述。\n"
            "5. 🎙️ 氛围与观众互动：整体直播氛围、观众互动特点（礼物、弹幕梗、名场面）。\n\n"
            "语言轻松、有网感，像给错过直播的朋友按时间线口述。"
            "所有时间戳必须从转写文本中提取，不得编造。"
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


# ========== 领域术语表 ==========
# 纠错与总结时注入 LLM 提示，避免专有名词（角色名/术语）被改写或保持错误写法。
# 支持 config.local.json 的 "scene_terms.<scene>" 覆盖。
SCENE_TERMS = {
    "streamer": [
        # 第五人格（含 2026 IDENTITY 系列新监管）
        "第五人格求生者：幸运儿 医生 律师 慈善家 园丁 魔术师 冒险家 佣兵 空军 机械师 前锋 盲女 祭司 调香师 牛仔 舞女 先知 入殓师 勘探员 咒术师 野人 杂技演员 调酒师 邮差 守墓人 囚徒 昆虫学者 画家 击球手 玩具商 心理学家 病患 小说家 小女孩 哭泣小丑 教授 古董商 作曲家 记者 飞行家 拉拉队员 木偶师 火灾调查员 法罗女士 骑士 气象学家 弓箭手 逃脱大师 幻灯师",
        "第五人格监管者：厂长 小丑 鹿头 杰克 蜘蛛 红蝶 黄衣之主 宿伞之魂 约瑟夫 疯眼 梦之女巫 爱哭鬼 孽蜥 红夫人 邦邦 使徒 小提琴家 雕刻家 博士 破轮 渔女 蜡像师 噩梦 记录员 隐士 守夜人 歌剧演员 愚人金 时空之影 跛脚羊 喧嚣 台球手 牙医 心兽",
        "第五人格术语：破译 解码 OB 博弈 板区 监管者 求生者 排位 匹配 精华 皮肤 摸金 摸青",
    ],
}


def get_scene_terms(scene: str) -> str:
    """获取场景术语表（逗号分隔拼接），无则返回空串"""
    terms = _get(f"scene_terms.{scene}", "")
    if terms:
        return terms if isinstance(terms, str) else "\n".join(terms)
    base = SCENE_TERMS.get(scene, [])
    return "\n".join(base)
