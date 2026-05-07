# BiliLive 直播助手

给 AI 装一双能"听"B站直播的耳朵。输入直播间链接，自动录制音频 → 转写文字 → 生成简报。

## 快速上手

### 安装

```bash
# 1. 安装 Python 依赖
pip install -r requirements.txt

# 2. （可选）GPU 加速
pip install nvidia-cublas-cu12
```

### 配置 API Key

首次使用需要配置 API Key。项目支持多个 AI 平台，选一个你有的就行：

```bash
cp config.example.json config.local.json
```

编辑 `config.local.json`，在对应平台填入你的 Key：

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

**如果你没有 API Key 怎么办？** 有几种方式：

| 方式 | 说明 |
|------|------|
| 注册 DeepSeek | 去 platform.deepseek.com 注册，免费额度够用很久 |
| 注册 MiniMax | 去 platform.minimaxi.com 注册 |
| **不加 Key，让 AI 总结** | 运行加 `--no-summarize` 参数，脚本只生成转写文本，把文本交给你的 AI 助手让它总结就行 |
| **你自己的方式** | 只要实现了 `summarizer.py` 中的接口，可以接入任意平台 |

**推荐**：注册 DeepSeek 最简单，免费额度大，效果也好。

### 使用

```bash
# 快速看直播间（录5分钟 → 转写 → 出简报）
python main.py --url https://live.bilibili.com/xxx --check

# 跟播到结束
python main.py --url https://live.bilibili.com/xxx --until-end --scene streamer

# 处理视频链接（B站 / YouTube 等）
python main.py --video https://www.bilibili.com/video/BVxxx

# 处理本地音频文件
python main.py --audio recording.wav

# 只要转写，不调用 AI 总结（让你自己的 AI 助手来总结）
python main.py --url https://live.bilibili.com/xxx --check --no-summarize
```

## 输出文件

每次运行的结果保存在 `output/` 目录：

```
output/{直播间标题}_{主播}/
  └── {时间戳}_{时长}/
      ├── transcript.txt    ← 转写原文（带时间戳）
      ├── summary.md        ← AI 总结简报
      ├── audio.wav         ← 录音文件
      └── danmaku.txt       ← 弹幕记录
```

每个直播间最多保留最近 5 次记录，旧的自动清理。

## 作为 AI Skill 使用

### 从 GitHub 安装

```
install_skill github_url="https://github.com/fangkuaizhu/bili-live-summary"
```

安装后，AI 助手会自动学习这个技能的使用方式。当你说"看看这个直播间在播什么"时，它会调用本工具。

### 从 Release 下载

去 [Releases 页面](https://github.com/fangkuaizhu/bili-live-summary/releases) 下载最新的 `agentskill.zip`，解压到 skills 目录。

## 场景参数

| 参数 | 适用场景 | 效果 |
|------|---------|------|
| `general`（默认） | 通用聊天 | 概括核心内容 |
| `lecture` | 讲座/课程 | 对学术术语更敏感，输出结构化的讲座笔记 |
| `streamer` | 游戏直播 | 对口语/网络用语更敏感，保留直播氛围 |

## 技术原理

```
直播间链接
    ↓ B站官方 API 获取 flv 流地址
    ↓ ffmpeg 分段录制音频（每30秒刷新地址，防止断流）
    ↓ faster-whisper GPU 转写（large-v3-turbo）
    ↓ AI 总结（DeepSeek / MiniMax / OpenAI）
    ↓ 输出简报
```

同时并行采集弹幕，弹幕数据会附加到转写文本中一起送给 AI 总结，让简报更完整。

## 目录结构

```
bili-live-summary/
├── SKILL.md              ← 技能元数据（供 AI 助手识别）
├── main.py               ← 入口
├── live_capture.py       ← 音频采集（B站 API + ffmpeg）
├── transcriber.py        ← 转写（faster-whisper）
├── summarizer.py         ← AI 总结
├── danmaku.py            ← 弹幕采集
├── config.py             ← 配置
├── config.example.json   ← API Key 模板
├── config.local.json     ← 你的 API Key（不提交到 git）
├── requirements.txt
├── output/               ← 结果保存
└── temp/                 ← 临时文件
```

## 常见问题

**Q: whisper 报错 cublas64_12.dll 找不到？**
```bash
pip install nvidia-cublas-cu12
```

**Q: 转写质量不好？**
在 `config.py` 中把 `WHISPER_BEAM_SIZE` 从 3 改成 5 或 8，更准但更慢。

**Q: 直播流获取失败？**
工具已内置使用 B站官方 API 获取流地址，如果持续失败，检查网络或直播间是否在线。

**Q: 不想装 GPU 驱动？**
在 `config.py` 中把 `WHISPER_DEVICE` 改为 `"cpu"`，`WHISPER_COMPUTE_TYPE` 改为 `"int8"`，速度会慢一些但不需要 GPU。
