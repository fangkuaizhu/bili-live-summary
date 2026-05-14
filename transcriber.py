"""
转写模块：使用 faster-whisper 将音频转为文字

核心功能：
1. 动态校准：先试转写一小段，评估质量，自动调参后再全量转写
2. 场景提示词：lecture / streamer / general 各有不同的 initial_prompt
"""

import os
import sys
import subprocess
from pathlib import Path
from typing import Optional

# imageio_ffmpeg 仅流式转写模式使用（已替换为 av）


def _setup_cuda_paths():
    """Windows 下将 pip 安装的 CUDA DLL 路径加入 DLL 搜索路径

    遍历 nvidia 命名空间下所有子包，将其 bin/ 目录同时加入 DLL 搜索路径
    (os.add_dll_directory) 和 PATH 环境变量，以确保 ctranslate2 的 C++ 绑定
    能够在 encode 时正确找到 cublas/cudnn/cudart 等运行时库。
    """
    if sys.platform != "win32":
        return
    try:
        import nvidia
        nv_dir = nvidia.__path__[0]
        bin_dirs = []
        for entry in os.scandir(nv_dir):
            if entry.is_dir():
                bin_dir = os.path.join(entry.path, "bin")
                if os.path.isdir(bin_dir):
                    bin_dirs.append(bin_dir)
        # 同时使用两种方式注册 DLL 路径
        for d in bin_dirs:
            os.add_dll_directory(d)
            if d not in os.environ.get("PATH", ""):
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
        # 预加载核心 CUDA DLL，提前解析依赖链
        import ctypes
        for d in bin_dirs:
            for f in os.scandir(d):
                if f.is_file() and f.name.endswith(".dll") and f.name.startswith(("cublas", "cudart", "cudnn")):
                    try:
                        ctypes.CDLL(str(f.path))
                    except Exception:
                        pass
    except (ImportError, AttributeError, OSError):
        pass


_setup_cuda_paths()

from faster_whisper import WhisperModel

from config import (
    WHISPER_MODEL,
    WHISPER_DEVICE,
    WHISPER_COMPUTE_TYPE,
    WHISPER_BEAM_SIZE,
    TEMP_DIR,
    get_scene_config,
)


_model: Optional[WhisperModel] = None


def get_model() -> WhisperModel:
    """懒加载 Whisper 模型（首次调用时下载，后续复用）"""
    global _model
    if _model is None:
        print(f"[Whisper] 加载模型: {WHISPER_MODEL} (device={WHISPER_DEVICE})")
        _model = WhisperModel(
            WHISPER_MODEL,
            device=WHISPER_DEVICE,
            compute_type=WHISPER_COMPUTE_TYPE,
        )
    return _model


# ==============================
#   校准引擎
# ==============================

def _extract_sample(audio_path: Path, duration: int = 30) -> Path:
    """截取音频前 N 秒作为校准样本（纯 Python wave）"""
    import wave
    sample_path = TEMP_DIR / "calibrate_sample.wav"
    if not audio_path.exists() or audio_path.stat().st_size < 10000:
        return audio_path

    try:
        with wave.open(str(audio_path), "rb") as src:
            rate = src.getframerate()
            params = (src.getnchannels(), src.getsampwidth(), rate, 0, "NONE", "not compressed")
            data = src.readframes(rate * duration)

        with wave.open(str(sample_path), "wb") as dst:
            dst.setparams(params)
            dst.writeframes(data)

        if sample_path.stat().st_size > 1000:
            return sample_path
    except Exception:
        pass

    return audio_path


def _evaluate_quality(
    segments: list,
    initial_prompt: str,
) -> dict:
    """评估转写质量，返回诊断结果"""
    texts = [s.text.strip() for s in segments if s.text.strip()]
    num = len(texts)
    joined = " ".join(texts)

    # 1. 检查是否在重复提示词（VAD 漏杀导致的 prompt leak）
    prompt_leak = False
    if initial_prompt and num > 0:
        prompt_words = set(initial_prompt.split("，"))
        leaked = sum(1 for p in prompt_words if p in joined and len(p) > 4)
        if leaked > 2:  # 提示词片段出现多次
            prompt_leak = True

    # 2. 评估密度
    if num == 0:
        density = "silent"
    elif num < 3:
        density = "sparse"
    elif num < 10:
        density = "light"
    else:
        density = "normal"

    # 3. 检查语言
    fixed_lang = segments.llm_detected_language if hasattr(segments, "llm_detected_language") else None

    return {
        "num_segments": num,
        "density": density,
        "prompt_leak": prompt_leak,
        "avg_len": len(joined) / max(num, 1),
    }


def calibrate(
    audio_path: Path,
    scene: str = "general",
) -> dict:
    """对音频采样并校准 Whisper 参数
    
    返回优化后的参数字典。
    """
    config = get_scene_config(scene)
    initial_prompt = config["initial_prompt"]
    model = get_model()

    # 提取样本
    sample = _extract_sample(audio_path)
    sample_mb = sample.stat().st_size / 1024 / 1024

    # --- 第一轮：默认配置 ---
    print(f"[校准] 采样 {sample_mb:.1f} MB 进行质量评估...")
    segs, info = model.transcribe(
        str(sample),
        language="zh",
        initial_prompt=initial_prompt,
        beam_size=5,
        vad_filter=True,
        vad_parameters=dict(threshold=0.5, min_speech_duration_ms=250,
                            max_speech_duration_s=30, min_silence_duration_ms=500),
    )
    segments_list = list(segs)
    quality = _evaluate_quality(segments_list, initial_prompt)

    detected_lang = info.language
    detected_prob = info.language_probability

    # --- 诊断 ---
    params = {
        "language": "zh",
        "vad_filter": True,
        "vad_threshold": 0.5,
        "beam_size": WHISPER_BEAM_SIZE,
        "initial_prompt": initial_prompt,
    }

    reasons = []

    should_fallback = False

    if quality["density"] == "silent" or quality["prompt_leak"]:
        # 没人声 或 提示词泄漏 → 关 VAD，清提示词，保留原语言
        should_fallback = True
        reasons.append("VAD 未检出有效人声 → 回退模式：关 VAD、清提示词，保留 zh")

    elif quality["density"] == "sparse":
        # 段太少：样本 30 秒只有 1-2 段，大概率 VAD 太激进
        should_fallback = True
        reasons.append("人声稀疏 (30s 仅 1-2 段) → 回退模式：关 VAD、清提示词")

    elif quality["density"] == "light":
        # 略少 → 轻微调低 VAD
        params["vad_threshold"] = 0.3
        params["beam_size"] = 8
        reasons.append(f"人声偏少 ({quality['num_segments']} 段/30s) → VAD 阈值降至 0.3")

    if should_fallback:
        params["vad_filter"] = False
        params["initial_prompt"] = None
        params["beam_size"] = 8
        # 静默回退：先试原语言 zh，如果仍无声再切 auto
        if quality["density"] == "silent":
            params["language"] = "zh"  # 保留中文，避免 BGM 被误判为英文
            # 用 zh + 关 VAD 再测一次
            segs2, _ = model.transcribe(
                str(sample), language="zh",
                vad_filter=False, beam_size=5, initial_prompt=None,
            )
            segs2_list = list(segs2)
            q2 = _evaluate_quality(segs2_list, "")
            if q2["num_segments"] == 0:
                # 确认真无声 → 切 auto
                params["language"] = None
                reasons.append("回退后仍无声 → 启用自动语言检测")
            else:
                reasons.append(f"回退后改善: {q2['num_segments']} 段/30s (保留 zh)")
        else:
            params["language"] = None
            # 用 auto 再测一次
            segs2, _ = model.transcribe(
                str(sample), language=None,
                vad_filter=False, beam_size=5, initial_prompt=None,
            )
            segs2_list = list(segs2)
            q2 = _evaluate_quality(segs2_list, "")
            if q2["num_segments"] > quality["num_segments"]:
                reasons.append(f"回退后改善: {q2['num_segments']} 段/30s")

    if detected_prob < 0.5 and params["language"] is not None:
        params["language"] = None
        reasons.append(f"语言置信度低 ({detected_prob:.0%}) → 启用自动检测")

    print(f"[校准] 检测语言: {detected_lang} ({detected_prob:.0%})")
    print(f"[校准] {quality['num_segments']} 段, 密度={quality['density']}")
    for r in reasons:
        print(f"[校准]   → {r}")

    # 清理样本
    if sample.exists():
        sample.unlink()

    return params


def transcribe(
    audio_path: Path,
    scene: str = "general",
    language: Optional[str] = None,
    calibrate_params: Optional[dict] = None,
) -> str:
    """转写音频文件为文字
    
    默认启用动态校准（除非显式传入 calibrate_params）。
    
    Args:
        audio_path: WAV 音频文件路径
        scene: 场景标识
        language: 语言代码，None = 自动检测
        calibrate_params: 直接指定校准参数（跳过校准步骤）
    
    Returns:
        转写后的完整文本
    """
    if not audio_path.exists():
        raise FileNotFoundError(f"音频文件不存在: {audio_path}")

    # 动态校准
    if calibrate_params is None:
        calibrate_params = calibrate(audio_path, scene)

    model = get_model()
    file_size_mb = audio_path.stat().st_size / 1024 / 1024
    print(f"[Whisper] 转写 ({file_size_mb:.1f} MB, {calibrate_params['language'] or 'auto'})...")

    vad_params = None
    if calibrate_params["vad_filter"]:
        vad_params = dict(
            threshold=calibrate_params["vad_threshold"],
            min_speech_duration_ms=250,
            max_speech_duration_s=30,
            min_silence_duration_ms=500,
        )

    segments, info = model.transcribe(
        str(audio_path),
        language=calibrate_params["language"],
        initial_prompt=calibrate_params["initial_prompt"],
        beam_size=calibrate_params["beam_size"],
        vad_filter=calibrate_params["vad_filter"],
        vad_parameters=vad_params,
    )

    lines = []
    for seg in segments:
        ts_min = int(seg.start // 60)
        ts_sec = int(seg.start % 60)
        lines.append(f"[{ts_min:02d}:{ts_sec:02d}] {seg.text.strip()}")

    full_text = "\n".join(lines)
    print(f"[Whisper] 完成: {len(lines)} 段, {len(full_text)} 字符")
    return full_text


def transcribe_video_streaming(
    video_url: str,
    scene: str = "general",
    progress_callback=None,
) -> str:
    import numpy as np
    config = get_scene_config(scene)
    model = get_model()
    import imageio_ffmpeg; ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    print("[流式]  启动 yt-dlp -> ffmpeg -> Whisper 流水线...")
    yt_proc = subprocess.Popen(
        [sys.executable, "-m", "yt_dlp", "-f", "bestaudio", "-o", "-", video_url],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    ff_proc = subprocess.Popen(
        [ffmpeg, "-i", "-", "-f", "s16le", "-ar", "16000", "-ac", "1", "-"],
        stdin=yt_proc.stdout, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    yt_proc.stdout.close()
    CHUNK_SEC = 30
    CHUNK_BYTES = 16000 * CHUNK_SEC * 2
    all_segments = []
    chunk_index = 0
    total_bytes = 0
    try:
        while True:
            raw = ff_proc.stdout.read(CHUNK_BYTES)
            if not raw:
                break
            if len(raw) < 16000:
                break
            audio_array = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            offset = chunk_index * CHUNK_SEC
            segments, _ = model.transcribe(
                audio_array,
                language="zh",
                beam_size=WHISPER_BEAM_SIZE,
                vad_filter=True,
                vad_parameters=dict(threshold=0.5, min_speech_duration_ms=250,
                                    max_speech_duration_s=30, min_silence_duration_ms=500),
            )
            for seg in segments:
                ts = offset + seg.start
                mins, secs = int(ts // 60), int(ts % 60)
                all_segments.append(f"[{mins:02d}:{secs:02d}] {seg.text.strip()}")
            chunk_index += 1
            total_bytes += len(raw)
            mb = total_bytes / 1024 / 1024
            if progress_callback:
                progress_callback(mb)
            if chunk_index % 5 == 0:
                print(f"[流式]  已处理 {chunk_index * CHUNK_SEC // 60} 分钟 ({mb:.0f} MB)...")
    finally:
        if ff_proc.stdout:
            ff_proc.stdout.close()
        ff_proc.wait()
        yt_proc.wait()
    full_text = "\n".join(all_segments)
    print(f"[Whisper] 流式完成: {len(all_segments)} 段, {len(full_text)} 字符")
    return full_text





def transcribe_live_streaming(
    room_id: str,
    scene: str = "general",
    duration: int = 300,
    progress_callback=None,
) -> str:
    import numpy as np
    from live_capture import get_fresh_stream_url
    config = get_scene_config(scene)
    model = get_model()
    import imageio_ffmpeg; ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    
    stream_url = get_fresh_stream_url(room_id)
    print("[流式]  启动 ffmpeg -> Whisper 流水线...")
    
    ff_proc = subprocess.Popen(
        [ffmpeg, "-i", stream_url, "-f", "s16le", "-ar", "16000", "-ac", "1", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    
    import time
    CHUNK_SEC = 30
    CHUNK_BYTES = 16000 * CHUNK_SEC * 2
    all_segments = []
    chunk_index = 0
    total_bytes = 0
    start_time = time.time()
    
    try:
        while True:
            elapsed = time.time() - start_time
            if elapsed > duration:
                print(f"[流式]  达到录制时长 {duration} 秒")
                break
            
            raw = ff_proc.stdout.read(CHUNK_BYTES)
            if not raw:
                print("[流式]  直播流结束")
                break
            if len(raw) < 16000:
                break
            
            audio_array = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            offset = chunk_index * CHUNK_SEC
            segments, _ = model.transcribe(
                audio_array,
                language="zh",
                beam_size=WHISPER_BEAM_SIZE,
                vad_filter=True,
                vad_parameters=dict(threshold=0.5, min_speech_duration_ms=250,
                                    max_speech_duration_s=30, min_silence_duration_ms=500),
            )
            for seg in segments:
                ts = offset + seg.start
                mins, secs = int(ts // 60), int(ts % 60)
                all_segments.append(f"[{mins:02d}:{secs:02d}] {seg.text.strip()}")
            chunk_index += 1
            total_bytes += len(raw)
            mb = total_bytes / 1024 / 1024
            if progress_callback:
                progress_callback(mb)
            if chunk_index % 5 == 0:
                print(f"[流式]  已录制 {int(elapsed//60)} 分 {int(elapsed%60)} 秒 ({mb:.0f} MB)...")
    finally:
        if ff_proc.stdout:
            ff_proc.stdout.close()
        ff_proc.wait()
    
    full_text = "\n".join(all_segments)
    print(f"[Whisper] 流式完成: {len(all_segments)} 段, {len(full_text)} 字符")
    return full_text


def transcribe_file(
    audio_path: Path,
    scene: str = "general",
    output_path: Optional[Path] = None,
) -> Path:
    """转写并保存到文件"""
    text = transcribe(audio_path, scene)
    if output_path is None:
        output_path = audio_path.with_suffix(".txt")
    output_path.write_text(text, encoding="utf-8")
    print(f"[保存]   转写结果: {output_path}")
    return output_path


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: python transcriber.py <音频文件> [scene]")
        sys.exit(1)
    audio = Path(sys.argv[1])
    scene = sys.argv[2] if len(sys.argv) > 2 else "general"
    result = transcribe(audio, scene)
    print()
    print("=== 转写结果 ===")
    print()
    print(result)
