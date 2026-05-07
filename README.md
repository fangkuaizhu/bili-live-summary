# BiliLive 直播助手

给 AI 装一双能"听"B站直播的耳朵。输入直播间链接，自动录制音频 → 转写文字 → 生成简报。

## 前置要求

- **Python 3.10 以上**
- **网络能访问 B站**
- （可选）**NVIDIA GPU** — 有 GPU 转写速度快 10 倍；没有也能跑，只是慢一点

## 快速上手

### 1. 安装

```bash
cd bili-live-summary
pip install -r requirements.txt
```

首次运行时会自动下载 Whisper 语音模型（约 1.5GB），只需一次。

### 2. 配置 API Key

工具要用 AI 来总结内容，需要一个 API Key。支持多个平台，选一个你有的：

```bash
cp config.example.json config.local.json
```

编辑 `config.local.json`，在你用的平台填入 Key：

```json
{
  "api": {
    "platform": "deepseek",
    "deepseek": { "api_key": "sk-你的key", "model": "deepseek-v4-flash" },
    "minimax":  { "api_key": "", "model": "MiniMax-M2.7" },
    "openai":   { "api_key": "", "model": "gpt-4o-mini" }
  }
}
```

**没有 API Key？** 几个选择：

| 做法 | 难度 | 说明 |
|------|------|------|
| 注册 DeepSeek | ⭐ 最简单 | 去 [platform.deepseek.com](https://platform.deepseek.com)，注册即送免费额度 |
| 注册 MiniMax | ⭐ 简单 | 去 [platform.minimaxi.com](https://platform.minimaxi.com) |
| **不加 Key，让 AI 助手总结** | ⭐ 最简单 | 运行加 `--no-summarize`，脚本只输出转写文本，交给你的 AI 助手来总结（见下方示例） |

**推荐**：注册 DeepSeek，免费额度大，一步到位。

### 3. 跑起来

```bash
# 快速看一眼直播间在播什么（录5分钟 → 出简报）
python main.py --url https://live.bilibili.com/xxx --check

# 跟播到结束（适合完整跟踪一场直播）
python main.py --url https://live.bilibili.com/xxx --until-end --scene streamer

# 处理视频链接
python main.py --video https://www.bilibili.com/video/BVxxx

# 处理本地音频文件
python main.py --audio recording.wav

# 只要转写，不要 AI 总结（让你自己的 AI 助手来总结）
python main.py --url https://live.bilibili.com/xxx --check --no-summarize
```

---

## 作为 AI Skill 使用

如果你是 AI 助手的使用者，可以直接让 AI 安装这个技能：

```
install_skill github_url="https://github.com/fangkuaizhu/bili-live-summary"
```

安装后，AI 助手会自动学会怎么用它。你说"看看这个直播间在播什么"，它就会调这个工具去录一段、转写、出结果。

> `install_skill` 是 OpenClaw / Claude Code 等 AI 终端内置的命令，用于从 GitHub 加载技能。如果你的 AI 平台不支持，也可以从 [Releases 页面](https://github.com/fangkuaizhu/bili-live-summary/releases) 下载 `agentskill.zip` 手动放置。

---

## 输出文件

每次运行的结果保存在 `output/` 目录：

```
output/{直播间标题}_{主播}/
  └── {时间戳}_{时长}/
      ├── transcript.txt   转写原文（带时间戳）
      ├── summary.md       AI 总结简报（如果没加 --no-summarize）
      ├── audio.wav        录音文件
      └── danmaku.txt      弹幕记录
```

每个直播间最多保留最近 5 次记录，超出自动清理。

---

## 场景参数

| 参数 | 适用场景 | 效果 |
|------|---------|------|
| `general`（默认） | 通用聊天/杂谈 | 概括核心内容 |
| `lecture` | 讲座/课程 | 对学术术语更敏感，输出结构化笔记 |
| `streamer` | 游戏直播/娱乐 | 对口语和网络用语更敏感，保留氛围 |

---

## 目录结构

```
bili-live-summary/
├── SKILL.md              技能元数据（供 AI 助手识别）
├── main.py               入口
├── live_capture.py       音频采集（B站 API + ffmpeg）
├── transcriber.py        转写（faster-whisper）
├── summarizer.py         AI 总结
├── danmaku.py            弹幕采集
├── config.py             配置
├── config.example.json   API Key 模板
├── config.local.json     你的 API Key（有 .gitignore 保护）
├── version.py            版本号
├── requirements.txt      Python 依赖
├── .gitignore            排除敏感文件
├── output/               结果保存
└── temp/                 临时文件（自动清理）
```

---

## 技术原理

```
直播间链接
    ↓ B站官方 API 获取 flv 流地址
    ↓ ffmpeg 分段录制音频（每30秒刷新地址，防止断流）
    ↓ faster-whisper GPU 转写（large-v3-turbo）
    ↓ AI 总结（DeepSeek / MiniMax / OpenAI）
    ↓ 输出简报
```

弹幕通过 WebSocket 实时采集，与音频并行运行，弹幕数据会附加到转写文本中一起送给 AI，让简报更完整。

---

## 常见问题

**Q: 报错 `cublas64_12.dll` 找不到？**
```bash
pip install nvidia-cublas-cu12
```

**Q: 没有 GPU 怎么跑？**
编辑 `config.py`，把 `WHISPER_DEVICE` 改为 `"cpu"`，`WHISPER_COMPUTE_TYPE` 改为 `"int8"`。速度会慢一些，但不需要显卡。

**Q: 转写质量不好？**
编辑 `config.py`，把 `WHISPER_BEAM_SIZE` 从 `3` 改成 `5` 或 `8`。数值越大越准，但越慢。

**Q: 直播流获取失败？**
已内置使用 B站官方 API 获取流地址。如果持续失败，检查网络或直播间是否在线。

**Q: 不想装任何依赖，就想试试？**
有 GPU 加速依赖（nvidia-cublas-cu12）是可选的，核心功能只需要 `pip install -r requirements.txt` 就能跑。
