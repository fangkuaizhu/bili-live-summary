"""
B站视频下载模块

支持: 完整链接 / 短链 / BV号 / 本地视频
多P下载使用 yt-dlp（更可靠的CDN鉴权与下载）
"""

import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import requests

from config import TEMP_DIR
from audio_utils import transcode_to_wav

BILI_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.bilibili.com",
}


def extract_bv(source: str) -> str:
    if re.match(r"^BV[\w]+$", source):
        return source
    if "b23.tv" in source:
        try:
            r = requests.head(source, allow_redirects=True, timeout=10, headers=BILI_HEADERS)
            source = r.url
        except Exception as e:
            raise RuntimeError(f"短链解析失败: {e}")
    match = re.search(r"(BV[\w]+)", source)
    if match:
        return match.group(1)
    raise ValueError(f"无法提取 BV 号: {source}")


def get_video_info(bv: str) -> dict:
    """获取视频基本信息（标题、UP主、第一P的cid、总时长）"""
    resp = requests.get(
        f"https://api.bilibili.com/x/web-interface/view?bvid={bv}",
        headers=BILI_HEADERS, timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"B站 API 错误: {data.get('message')}")
    d = data["data"]
    return {"title": d.get("title", ""), "uploader": d.get("owner", {}).get("name", ""),
            "cid": d.get("cid", 0), "duration": d.get("duration", 0)}


def get_video_pages(bv: str) -> list[dict]:
    """获取该BV下所有分P的 [{cid, page, part, duration}]"""
    resp = requests.get(
        f"https://api.bilibili.com/x/web-interface/view?bvid={bv}",
        headers=BILI_HEADERS, timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"B站 API 错误: {data.get('message')}")
    d = data["data"]
    pages = d.get("pages", [])
    if not pages:
        return [{"cid": d["cid"], "page": 1, "part": d.get("title", "P1"), "duration": d.get("duration", 0)}]
    return [
        {"cid": p["cid"], "page": p["page"], "part": p["part"], "duration": p["duration"]}
        for p in sorted(pages, key=lambda x: x["page"])
    ]


def download_page_audio(bv: str, cid: int, page: int, output_path: Path) -> Path:
    """
    下载指定分P的音频流到 output_path
    使用 B站原生 API 获取 CDN 地址，HTTP 下载后转码为 WAV
    """
    info = get_video_info(bv)

    print(f"[B站] 下载音频 P{page}: {info['title'][:30]}")
    start = time.time()

    # 获取播放地址
    resp = requests.get(
        "https://api.bilibili.com/x/player/playurl",
        params={"bvid": bv, "cid": cid, "qn": 32, "fnval": 0, "platform": "html5"},
        headers=BILI_HEADERS, timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取播放地址失败: {data.get('message')}")
    durls = data.get("data", {}).get("durl", [])
    if not durls:
        raise RuntimeError("未获取到视频流地址")

    # 下载原始视频（一次性 GET，避免流式连接中断）
    temp = TEMP_DIR / f"_dl_{bv}_p{page}.mp4"
    last_err = None
    for attempt, d in enumerate(durls[:3]):
        url = d["url"]
        expected_size = d.get("size", 0)
        try:
            r = requests.get(url, headers=BILI_HEADERS, timeout=120)
            r.raise_for_status()
            temp.write_bytes(r.content)
            # 验证大小
            actual_size = temp.stat().st_size
            if expected_size > 0 and actual_size < expected_size * 0.9:
                raise RuntimeError(f"下载不完整: {actual_size}/{expected_size} bytes")
            # 转码为 WAV（使用已修复的 _transcode_to_wav）
            from audio_utils import transcode_to_wav
            transcode_to_wav(temp, output_path)
            if output_path.exists() and output_path.stat().st_size > 1000:
                break
        except Exception as e:
            last_err = e
            print(f"  CDN {attempt+1} 失败: {str(e)[:80]}")
        finally:
            temp.unlink(missing_ok=True)
    else:
        raise RuntimeError(f"所有 CDN 下载失败: {last_err}")

    elapsed = time.time() - start
    mb = output_path.stat().st_size / 1024 / 1024
    print(f"[B站] 完成 P{page}: {mb:.1f} MB, 耗时 {elapsed:.0f}s")
    return output_path


def download_bilibili_audio(bv_or_url: str, output_path: Optional[Path] = None) -> tuple[Path, dict]:
    """下载单P视频的音频（兼容旧接口，使用 yt-dlp）"""
    bv = extract_bv(bv_or_url)
    info = get_video_info(bv)
    if output_path is None:
        output_path = TEMP_DIR / f"{bv}_audio.wav"
    return download_page_audio(bv, info["cid"], 1, output_path), info


def _download_url(url: str, dest: Path, headers: dict, max_retries: int = 3):
    """带重试的 HTTP 下载（保留供非B站使用）"""
    for retry in range(max_retries):
        try:
            r = requests.get(url, headers=headers, stream=True, timeout=(15, 60))
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=256 * 1024):
                    if not chunk:
                        break
                    f.write(chunk)
            return
        except (requests.RequestException, Exception) as e:
            if retry == max_retries - 1:
                raise
            time.sleep(2 ** retry)


def extract_local_video_audio(video_path: Path, output_path: Optional[Path] = None) -> Path:
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


def is_bilibili_url(url: str) -> bool:
    return bool(re.search(r"(bilibili\.com/video|b23\.tv|^BV)", url, re.IGNORECASE))
