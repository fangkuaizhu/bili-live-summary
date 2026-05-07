# BiliLive 直播助手

给你的 Bilibili 直播装一双 AI 耳朵。输入直播间链接，自动录制音频 → 转写文字 → 生成简报。

## 核心思想

直播音频比画面和弹幕都更有信息量。用 yt-dlp 拉流 + ffmpeg 录一段音频，再用 faster-whisper 转写成文字，最后交给 AI 总结。全程开销极低——只有一次本地 ASR + 一次小 API 调用。

## 安装

```bash
# 依赖已经在你的环境里了，但如果你要重装：
pip install -r requirements.txt
```

## 快速使用

### 场景1：主播在播什么？快速看一眼

```bash
python main.py --url https://live.bilibili.com/xxx --check
```

录 5 分钟音频 → 转写 → 生成简报。适合想知道主播在干什么但又不想蹲直播的场景。

### 场景2：讲座直播，我没时间看

```bash
python main.py --url https://live.bilibili.com/xxx --duration 3600 --scene lecture
```

录 1 小时 → 转写 → 生成讲座简报。

`--scene lecture` 参数会调整 Whisper 的初始提示词，让它对学术/专业术语更敏感。

### 场景3：已有音频文件

```bash
python main.py --audio recording.wav --scene streamer
```

跳过录制步骤，直接转写本地音频。

## 目录结构

```
bili-live-summary/
├── main.py              # CLI 入口
├── live_capture.py      # 音频采集（yt-dlp + ffmpeg）
├── transcriber.py       # 转写（faster-whisper）
├── summarizer.py        # 总结生成
├── config.py            # 配置与场景预设
├── requirements.txt
├── temp/                # 临时音频文件
└── output/              # 转写文本和总结简报
```

## 两种总结方式

1. **脚本自动总结**：`main.py` 默认调用 `mmx` CLI 生成简报。需要 `mmx` 已登录。
2. **手动交给 Hanako**：加 `--no-summarize` 参数，只保存转写文本，然后直接告诉我 "帮我看这份转写"。

## 关于 Whisper 模型

首次运行时 faster-whisper 会自动下载 "small" 模型（~1.5GB），缓存在 `~/.cache/huggingface/` 目录下。
之后再次运行就秒加载了。

你已有的 `ggml-small.bin` 是 whisper.cpp 格式，和 faster-whisper 不互通。但 faster-whisper 在 GPU 上跑得更快，所以推荐就用它的格式。
