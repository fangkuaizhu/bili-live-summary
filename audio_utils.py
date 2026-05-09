"""
音频工具：纯 Python 替代 ffmpeg（基于 av + wave）

imageio_ffmpeg 在 Windows 上有初始化卡死问题，
这里用 av (PyAV) 库完成所有编解码、切片、合并操作。
"""

import io
import time
import wave
from pathlib import Path
from typing import Optional

import av
import requests

from config import TEMP_DIR


def extract_sample(audio_path: Path, duration: int = 30) -> Path:
    """截取音频前 N 秒作为校准样本（纯 Python wave 实现）"""
    sample_path = TEMP_DIR / "calibrate_sample.wav"
    if not audio_path.exists() or audio_path.stat().st_size < 10000:
        return audio_path

    try:
        with wave.open(str(audio_path), "rb") as src:
            rate = src.getframerate()
            frames_30s = rate * duration
            data = src.readframes(frames_30s)
            params = (src.getnchannels(), src.getsampwidth(), rate, 0, "NONE", "not compressed")

        with wave.open(str(sample_path), "wb") as dst:
            dst.setparams(params)
            dst.writeframes(data)

        if sample_path.stat().st_size > 1000:
            return sample_path
    except Exception:
        pass

    return audio_path


def download_stream_to_wav(
    url: str,
    output_path: Path,
    duration: Optional[int] = None,
    headers: Optional[dict] = None,
    timeout: int = 120,
) -> Path:
    """从 HTTP 流下载音频并转码为 16kHz mono WAV

    Args:
        url: 音频流 URL（FLV/MP4 等）
        output_path: 输出 WAV 路径
        duration: 限制时长（秒），None 为不限
        headers: HTTP 请求头（用于反爬）
        timeout: 下载超时（秒）
    """
    # 用 requests 流式下载
    h = headers or {"User-Agent": "Mozilla/5.0"}
    try:
        resp = requests.get(url, headers=h, stream=True, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"下载失败: {e}")

    # 写入临时文件（av 需要可 seek 的文件对象）
    temp = TEMP_DIR / f"_stream_dl_{output_path.stem}.tmp"
    downloaded = 0
    start = time.time()

    with open(temp, "wb") as f:
        for chunk in resp.iter_content(chunk_size=256 * 1024):
            if not chunk:
                break
            f.write(chunk)
            downloaded += len(chunk)
            if duration and (time.time() - start) > duration + 15:
                break  # 多给 15 秒余量

    if not temp.exists() or temp.stat().st_size < 1000:
        raise RuntimeError("下载的流数据为空")

    # 用 av 解码并转码
    try:
        _transcode_to_wav(temp, output_path)
    finally:
        temp.unlink(missing_ok=True)

    return output_path


def transcode_to_wav(input_path: Path, output_path: Path) -> Path:
    """将任意格式音视频转码为 16kHz mono WAV"""
    return _transcode_to_wav(input_path, output_path)


def _transcode_to_wav(src: Path, dst: Path) -> Path:
    """内部：av 转码为 16kHz mono WAV"""
    in_ctr = None
    out_ctr = None
    try:
        in_ctr = av.open(str(src))
        audio_stream = in_ctr.streams.audio[0]

        out_ctr = av.open(str(dst), "w")
        out_stream = out_ctr.add_stream("pcm_s16le", 16000)
        out_stream.layout = "mono"

        for packet in in_ctr.demux(audio_stream):
            for frame in packet.decode():
                if frame.samples > 0:
                    arr = frame.to_ndarray()
                    if len(arr.shape) > 1:
                        arr = arr.mean(axis=0)
                    arr = (arr * 32767).clip(-32768, 32767).astype("int16")
                    new_frame = av.AudioFrame.from_ndarray(
                        arr.reshape(1, -1), format="s16", layout="mono"
                    )
                    new_frame.sample_rate = 16000
                    for pkt in out_stream.encode(new_frame):
                        out_ctr.mux(pkt)

        for pkt in out_stream.encode(None):
            out_ctr.mux(pkt)
    finally:
        if out_ctr:
            out_ctr.close()
        if in_ctr:
            in_ctr.close()

    return dst


def merge_wav_segments(part_paths: list[Path], output_path: Path) -> Path:
    """合并多个 WAV 文件为单个（纯 Python wave）"""
    valid = [p for p in part_paths if p.exists() and p.stat().st_size > 1000]
    if not valid:
        raise RuntimeError("没有有效音频分段可合并")

    if len(valid) == 1:
        valid[0].rename(output_path)
    else:
        with wave.open(str(output_path), "wb") as dst:
            first = wave.open(str(valid[0]), "rb")
            dst.setparams(first.getparams())
            first.close()

            for p in valid:
                with wave.open(str(p), "rb") as src:
                    dst.writeframes(src.readframes(src.getnframes()))

    # 清理分段
    for p in part_paths:
        p.unlink(missing_ok=True)

    return output_path


def extract_local_video_audio(video_path: Path, output_path: Optional[Path] = None) -> Path:
    """从本地视频文件提取音频"""
    if not video_path.exists():
        raise FileNotFoundError(f"视频文件不存在: {video_path}")

    if output_path is None:
        output_path = TEMP_DIR / f"{video_path.stem}_audio.wav"

    print(f"[本地] 提取音频: {video_path.name}")
    start = time.time()
    transcode_to_wav(video_path, output_path)
    elapsed = time.time() - start
    mb = output_path.stat().st_size / 1024 / 1024
    print(f"[本地] 完成: {mb:.1f} MB, 耗时 {elapsed:.0f}s")

    return output_path
