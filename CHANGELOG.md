# Changelog

## v1.7.0 — 2026-08-07

### ✨ New Features

- **多点校准采样** — 校准样本从前 30 秒单点改为前/中/后三点采样（音频 <120s 退化单点），三点密度投票取多数决，避免视频开头铺垫慢导致误判 sparse。
- **热词注入** — 从视频标题/简介提取关键词注入 initial_prompt，并尝试 faster-whisper 原生 hotwords 特性（不支持时自动降级）。可通过 `config.py` 的 `HOTWORDS_ENABLED` 关闭。
- **LLM 二轮纠错** — 新增 `corrector.py`：转写完成后调用 LLM（默认 DeepSeek，可切 MiniMax/OpenAI）纠正专有名词/术语/同音错字，输出 `transcript_corrected.txt`。无 API key 或调用失败时自动降级返回原文，不中断流程。可用 `--no-correct` 关闭。
- **长视频流式切换** — B站视频时长 >30 分钟自动切换流式转写（`transcribe_video_streaming`），避免整片下载后 batch 转写的临时空间占用。

### 🐛 Bug Fixes

- **总结使用纠错后文本** — 原总结吃的是原始转写，导致 summary.md 残留「黄金龟脂」类错误；现纠错可用时总结优先使用纠错版本。
- **Windows 控制台中文乱码** — `main.py` 入口对 stdout/stderr 执行 `reconfigure(encoding="utf-8")`，修复 PowerShell GBK 控制台下中文输出乱码及工具层输出目录解析失败。
- **直播状态查询依赖 yt-dlp** — `get_live_status` 原通过 yt-dlp 获取直播间信息，在 SSL 报错时导致整个进程崩溃；改为调用 B站官方 API `get_info` + `get_anchor_in_room`。
- **预检误判在播** — playUrl 在主播下播时也会返回 404 占位流，原预检只看是否有流地址导致误判；现会实际请求流 URL 验证 HTTP 200，下播房间秒级识别并快速失败。
- **B站 API 偶发 SSL 错误导致跟播中断** — API 请求统一改用 requests + 指数退避重试（3 次）；跟播模式状态检查失败不再判定直播结束（API 抖动不中断，直播结束靠分段录制连续失败兜底）。

### 🔧 Internal

- `transcriber.py`：`transcribe()` 新增 `hotwords`/`title` 参数，旧调用方式完全兼容。
- `pipeline.py`：四个流程函数新增 `correct` 参数。
- `config.py`：新增 `HOTWORDS_ENABLED` 常量。

---

## v1.8.0 — 2026-08-09

### ✨ New Features

- **长文本分段总结** — `summarize_with_api` 对超长转写自动分段总结再合并：
  - **分段**：每段 ≤ `SUMMARY_CHUNK_TOKENS`（默认 15000，中文约 1 字符 ≈ 1 token），按行切分不切断句。
  - **上下文衔接**（`SUMMARY_CONTEXT_MODE`，默认 `both`）：
    - `overlap`：每段开头内嵌上一段尾部 `SUMMARY_OVERLAP_TOKENS`（默认 1500）字符的原文，解决口语指代（“那个板子”“刚才说的”）
    - `summary`：每段携带上一段 LLM 生成的摘要，保持话题级连贯
  - **两级合并**：逐段总结（Map）→ 全部要点合并去重生成最终完整简报（Reduce），不遗漏任何段落独有信息。
  - 短文本（≤阈值）走原单次调用路径，零回归。
- **总结输出上限提升** — 段总结与最终合并 `max_tokens` 从 2048 提升至 `SUMMARY_MAX_OUTPUT_TOKENS`（默认 4096），防详细总结被截断。

### 🔧 Internal

- `config.py`：新增 `SUMMARY_CHUNK_TOKENS` / `SUMMARY_OVERLAP_TOKENS` / `SUMMARY_CONTEXT_MODE` / `SUMMARY_MAX_OUTPUT_TOKENS`（可经 config.local.json 的 `summary.*` 覆盖）。
- `summarizer.py`：新增 `_split_chunks_with_overlap` / `_summarize_chunk_with_context` / `_merge_chunk_summaries` / `_summarize_long`。

---

## v1.7.1 — 2026-08-09

### 🐛 Bug Fixes

- **长文本纠错被截断（严重）** — `corrector.py` 固定 `max_tokens=4096`，长直播转写（>8000 字符）单批发送时 LLM 输出被截断，只纠错前 20 分钟、后半段丢失（如 110 分钟直播 corrected 文件停在中途且末尾残缺）。修复：`max_tokens` 提升至 8192，分批阈值从 20000 降到 8000 字符、每批上限从 15000 降到 6000，确保每批输出在安全范围内；重跑后长文本按 3 批完整纠错。

---

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
