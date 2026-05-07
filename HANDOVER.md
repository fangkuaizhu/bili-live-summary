# BiliLive 直播助手 · 维护手册

## 项目定位

给 AI 安一双能"听"B站直播的耳朵。输入直播间链接，自动录制音频 → Whisper 转写 → 生成简报。

三种工作模式：
- **定时查看**：录 N 秒，立即出总结
- **跟播跟踪**：分段录制自动续连，直播结束出完整转写
- **截图模式**：截取直播画面存为 jpg，可结合图像识别做多模态总结

## 文件结构

```
bili-live-summary/
├── main.py              # CLI 入口，唯一需要调用的文件
├── live_capture.py      # 音频采集：B站 API + ffmpeg 双模式
├── transcriber.py       # ASR 转写：faster-whisper + GPU 加速
├── summarizer.py        # 总结生成（mmx CLI / 手动模式）
├── config.py            # 配置：场景提示词、模型、路径
├── README.md            # 用户文档
├── requirements.txt     # 依赖清单
├── temp/                # 临时音频片段（自动清理）
└── output/              # 转写文本和总结简报（保留）
```

## 核心架构

### 一句话原理

```
直播链接 → B站官方 API 取 flv 流地址 → ffmpeg 分段录音频 → faster-whisper 转写 → 生成简报
```

### 断流问题的解法

B 站直播的 HLS 流地址有时效性（`expires` 参数），直接拿来用很快断流。

**解法**：分段录制 + 每段刷新地址。

跟播模式下，每 30 秒为一"段"：
1. 调用 `get_fresh_stream_url()` 通过 B站官方 API 获取全新 flv 流地址
2. ffmpeg 拉取 30 秒音频
3. 存为 `part_N.wav`
4. 回第 1 步

B站 HLS 流（m3u8）有时效性和稳定性问题，改为 B站官方 `playUrl` API 获取 flv 流后稳定性大幅提升。flv 格式对 ffmpeg 兼容性最好，断流/超时问题显著减少。

### 为什么不直接用 yt-dlp 下载

尝试过（`yt-dlp -o file.ts`），但 B 站直播用 fMP4 格式，yt-dlp 的安全检查会拒绝下载（"The extracted extension 'fmp4' is unusual"）。`--compat-options allow-unsafe-ext` 无效，`--allow-unsafe-ext` 不存在。最稳定的方案就是 B站 API + 分段 ffmpeg。

### GPU 加速

faster-whisper 依赖 cuBLAS 12 的 DLL。通过 `pip install nvidia-cublas-cu12` 安装，然后在 `transcriber.py` 的模块初始化中自动将 DLL 路径加入 `os.environ["PATH"]`：

```python
def _setup_cuda_paths():
    import nvidia
    nv_dir = nvidia.__path__[0]
    for sub in ["cublas", "cuda_nvrtc"]:
        bin_dir = os.path.join(nv_dir, sub, "bin")
        if os.path.isdir(bin_dir) and bin_dir not in os.environ.get("PATH", ""):
            os.environ["PATH"] = bin_dir + ";" + os.environ.get("PATH", "")
```

如果换机器或重装系统后 `cublas64_12.dll` 报错，重新执行 `pip install nvidia-cublas-cu12`。

## CLI 用法

```bash
# 快速查看（录5分钟）
python main.py --url https://live.bilibili.com/xxx --check

# 定时录制（如1小时讲座）
python main.py --url https://live.bilibili.com/xxx --duration 3600 --scene lecture

# 跟播到结束（适合完整跟踪）
python main.py --url https://live.bilibili.com/xxx --until-end --scene streamer

# 只录不总结（省一次API调用）
python main.py --url https://live.bilibili.com/xxx --check --no-summarize

# 本地已有音频文件
python main.py --audio recording.wav --scene lecture
```

### `--scene` 参数影响

| 场景 | Whisper 提示词偏重 | 总结风格 |
|------|-------------------|---------|
| `general` | 通用中文 | 概括核心内容 |
| `lecture` | 学术术语、专业名词 | 讲座简报：主题、观点、概念 |
| `streamer` | 口语、网络用语 | 直播简报：内容、氛围、趣事 |

提示词定义在 `config.py` 的 `SCENE_PROMPTS` 字典中。

## 典型工作流

### 跟播一场直播

```bash
python main.py --url https://live.bilibili.com/xxx --until-end --scene streamer
```

终端会每 30 秒输出一段进度：
```
[00:30] 段1: 0.9 MB | 总计: 0.9 MB | 速率: 31 KB/s
[01:00] 段2: 0.9 MB | 总计: 1.9 MB | 速率: 32 KB/s
...
```

直播结束后（或按 `Ctrl+C` 手动停止），自动合并所有分段，启动 Whisper 转写。转写完成后生成简报，保存在 `output/` 目录。

### 跟踪过程中突然想看进展

按 `Ctrl+C` 会停止录制，进入转写+总结阶段。不会丢失已录制的音频。

## 常见问题

### `cublas64_12.dll is not found`

GPU 模式下报这个错，重装 CUDA 库：
```bash
pip install --force-reinstall nvidia-cublas-cu12
```

### 流地址获取失败

脚本通过 B站官方 API (`api.live.bilibili.com/room/v1/Room/playUrl`) 获取 flv 流地址。如果持续失败，检查：
1. 直播间是否真的在播
2. 网络是否能访问 api.live.bilibili.com
3. 如果 B站 API 调整版本号，检查 API 返回格式是否变化

### Whisper 模型首次加载慢

small 模型首次需要下载约 1.5GB，后续缓存到 `~/.cache/huggingface/`。网络差时可以手动下载到本地，用绝对路径加载（需修改 `transcriber.py`）。

### 输出文件在哪

- 转写文本：`output/{room_id}_{datetime}_{duration}s.txt`
- 简报：`output/{room_id}_{datetime}_{duration}s.summary.md`
- 临时分段音频：`temp/{room_id}_part_*.wav`（合并后自动删除）

## 配置修改

`config.py` 中最常改的几项：

- `WHISPER_MODEL`：`tiny` / `base` / `small` / `medium` / `large-v3` / `large-v3-turbo`
- `WHISPER_DEVICE`：`cuda` / `cpu`
- `WHISPER_COMPUTE_TYPE`：GPU `float16`，CPU `int8`
- `WHISPER_BEAM_SIZE`：`3`（快速）/ `5`（平衡）/ `8`（最准）
- `WHISPER_MODE`：`batch`（下载完转写）/ `stream`（边下边转写，仅视频模式）
- `SCENE_PROMPTS`：场景初始提示词和总结指令

## 需要处理的视频链接时

当用户要求分析视频（`--video` 模式），必须要求用户提供**精确的 BV 号或完整链接**。

不允许自行搜索标题来匹配视频，因为同名的讲解/评测视频非常多（例如 DeepSeek V4 讲解就有多个不同 UP 主的不同版本），搜标题会拿错。

如果用户没有给 BV 号或链接，应当先询问，而不是自己去搜。

## 视频报错处理

当 yt-dlp 探测视频失败时（已删除/私密/区域限制），输出友好的中文提示，列出可能原因，不清除任何已有数据。

探测成功但下载失败时，清理刚创建的空 session 目录，避免留下垃圾文件夹。

## 输出目录管理

每个 session（直播/视频/本地音频）都通过 `create_session_dir()` 创建唯一时间戳目录，永不覆盖。

三种模式的目录结构：
```
output/
├── {直播标题}_{主播}/
│   └── {YYYYMMDD_HHMM}_{时长}/
├── {视频标题}_video_{BV}/
│   └── {YYYYMMDD_HHMM}_full/
└── {文件名}_local/
    └── {YYYYMMDD_HHMM}_full/
```

每个父目录最多保留最近 **5 次** session，超出的旧记录会在新建时自动清理。
