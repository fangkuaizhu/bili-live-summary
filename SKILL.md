---
name: bili-live-summary
description: Bilibili直播/视频内容采集、转写与AI总结。输入直播间链接自动录制音频→Whisper转写→AI总结简报。支持视频链接（B站/YouTube等）和本地音频文件。弹幕采集、多平台总结API、GPU加速。
version: 1.2.0
source: https://github.com/fangkuaizhu/bili-live-summary
install: |
  git clone https://github.com/fangkuaizhu/bili-live-summary.git
  cd bili-live-summary
  pip install -r requirements.txt
  cp config.example.json config.local.json
  # 编辑 config.local.json 填入 API key
---

# BiliLive 直播/视频 总结助手

## Overview

给 AI 装一双能"听"直播的耳朵。支持三条输入路径，输出结构化简报。

## When to Use

- 用户想"看看这个直播间在播什么" → `--url` 模式
- 用户给了一个视频链接（B站/YouTube/抖音等） → `--video` 模式
- 用户发了一个音频文件 → `--audio` 模式
- 用户说"盯一下这个直播直到结束" → `--until-end` 模式

**视频链接匹配规则**：必须使用用户提供的精确 BV 号或完整链接，不允许自行搜索标题匹配。

## Quick Start

```bash
# 进入项目目录（技能安装后自动定位）
cd {skill_dir}

# 配置 API Key（首次使用）
cp config.example.json config.local.json
# 编辑 config.local.json 填入 API Key

# 快速查看直播间（5分钟）
python main.py --url https://live.bilibili.com/xxx --check

# 跟播到结束
python main.py --url https://live.bilibili.com/xxx --until-end

# 任意视频链接
python main.py --video https://www.bilibili.com/video/BVxxx
python main.py --video https://www.youtube.com/watch?v=xxx

# 本地音频文件
python main.py --audio recording.wav
```

## Architecture

### 三路输入
- `--url`：B站直播间 → 分段录制音频（30秒/段，自动刷新流地址）+ 弹幕采集 → 合并 → 校准 → 转写 → 总结
- `--video`：任意视频 → yt-dlp 仅下音频流（`-f bestaudio`） → 校准 → 转写 → 总结
- `--audio`：本地文件 → 转写 → 总结

### 输出结构
```
output/{标题}_{上传者}/
  └── {YYYYMMDD_HHMM}_{时长}/
      ├── transcript.txt    转写原文（含时间戳）
      ├── summary.md        AI 总结简报
      ├── audio.wav         源音频（仅直播/视频模式）
      ├── danmaku.txt       弹幕记录（仅直播模式）
      ├── frame.jpg         直播画面截图（可选）
      └── cover.jpg         房间封面（可选）
```

每个父目录保留最近 5 次 session，超出的自动清理。

### 直播流获取方式
原方案通过 yt-dlp `-g` 获取 m3u8 流地址，但 B站直播的 m3u8 流不稳定，经常出现 ffmpeg 下载超时或 "End of file" 错误。

**已修复（2026-05-07）**：改用 B站官方 API 获取 flv 流地址：
```
https://api.live.bilibili.com/room/v1/Room/playUrl?cid={room_id}&platform=web&qn=80
```
flv 格式更稳定，分段录制成功率大幅提升。

### 动态校准引擎
转录前自动截取前 30 秒音频测试：
- 0 段 → 关 VAD + 清提示词 + 自动语言检测
- 1-2 段 → 回退模式（VAD off）
- 3-10 段 → 降 VAD 阈值到 0.3
- >10 段 → 默认参数运行

### 双模式转写
```python
# config.py
WHISPER_MODE = "batch"     # 下载/录制完再转写（保留音频文件）
WHISPER_MODE = "stream"    # 边下边转/边录边转（不保留音频，更快）
```

### 弹幕采集
直播模式下通过 WebSocket 协议实时采集弹幕，与音频并行运行。弹幕数据附加到转写文本后送入 AI 总结。

## Configuration

`config.local.json`（不提交到版本控制）：

```json
{
  "api": {
    "platform": "deepseek",
    "minimax": { "api_key": "", "model": "MiniMax-M2.7" },
    "deepseek": { "api_key": "", "model": "deepseek-v4-flash", "base_url": "https://api.deepseek.com" },
    "openai": { "api_key": "", "model": "gpt-4o-mini", "base_url": "https://api.openai.com" }
  }
}
```

API Key 读取优先级：`config.local.json` > 环境变量 > 系统默认（mmx 配置等）。

## Key Components

| 文件 | 职责 |
|------|------|
| `main.py` | CLI 入口，三路输入路由 |
| `live_capture.py` | 音频采集（分段录制/视频下载/截图）+ 弹幕集成 |
| `transcriber.py` | 校准引擎 + GPU 转写（large-v3-turbo）+ 流式转写 |
| `summarizer.py` | 保存 + 总结（MiniMax/DeepSeek/OpenAI） |
| `danmaku.py` | WebSocket 弹幕采集器 |
| `config.py` | 配置（Whisper、API 平台、场景提示词） |
| `config.local.json` | API Key 和平台选择（用户自行填写） |
| `config.example.json` | 配置模板 |

## CLI Reference

```
--url, -u      B站直播间链接
--video, -v    视频链接（任意平台）
--audio, -a    本地音频文件
--duration, -t  录制时长（秒）
--check, -c    快速查看：录制5分钟
--until-end, -e 跟播到结束
--scene, -s    场景（general/lecture/streamer）
--screenshot   截取直播画面（仅--url）
--no-summarize 不自动总结
```

## Troubleshooting

- **cublas64_12.dll 报错**：`pip install nvidia-cublas-cu12`
- **转录质量差**：校准会自动调整，也可手动改 `config.py` 的 `WHISPER_BEAM_SIZE`（3/5/8）
- **视频无法获取**：检查 BV 号是否正确、视频是否已被删除或区域限制
- **直播流获取超时/EOF**：B站直播 m3u8 流不稳定，已内置使用 B站官方 API 获取 flv 流（`api.live.bilibili.com/room/v1/Room/playUrl`），若仍然失败可检查网络或直播间是否在线
- **直播断流**：30秒分段录制 + 自动重连，某一段失败不影响其他段
- **弹幕未采集**：确认 `websocket-client` 已安装（`pip install websocket-client`）
