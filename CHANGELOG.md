# Changelog

## v1.2.0 — 2026-05-07

### 🐛 Bug Fixes

- **直播流获取不稳定** — `get_fresh_stream_url` 原通过 yt-dlp `-g` 获取 m3u8 流地址，B站直播的 HLS 流时常超时或报 "End of file"。改为调用 B站官方 API `api.live.bilibili.com/room/v1/Room/playUrl` 获取 flv 格式流，稳定性大幅提升。
- 影响范围：`--url` 模式（定时录制/跟播/截图），以及流式转写模式（`transcriber.py` 中 `transcribe_live_streaming`）

### 📝 Documentation

- `SKILL.md`：新增"直播流获取方式"章节记录技术决策；Troubleshooting 更新
- `HANDOVER.md`：修正所有引用 yt-dlp 的说明，同步为 B站 API 方案

### 🔧 Internal

- `live_capture.py`：模块 docstring 同步更新
- `version.py`：新增，版本号 1.2.0

---

## v1.1.0

初始可用版本。支持直播录制、视频下载、本地音频三种输入模式，Whisper GPU 加速转写，AI 总结简报，弹幕实时采集。
