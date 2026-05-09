"""
B站视频下载模块：使用 B站官方 API 绕过 yt-dlp

支持: 完整链接 / 短链 / BV号 / 本地视频
"""

import re
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


def download_bilibili_audio(bv_or_url: str, output_path: Optional[Path] = None) -> tuple[Path, dict]:
    bv = extract_bv(bv_or_url)
    info = get_video_info(bv)
    if output_path is None:
        output_path = TEMP_DIR / f"{bv}_audio.wav"

    resp = requests.get(
        "https://api.bilibili.com/x/player/playurl",
        params={"bvid": bv, "cid": info["cid"], "qn": 0, "fnval": 16, "platform": "html5"},
        headers=BILI_HEADERS, timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取播放地址失败: {data.get('message')}")
    durls = data.get("data", {}).get("durl", [])
    if not durls:
        raise RuntimeError("未获取到视频流地址")

    print(f"[B站] 下载音频: {info['title'][:30]} ({info['duration']}s)")
    start = time.time()

    # 多 CDN 重试
    temp = TEMP_DIR / f"_dl_{bv}.flv"
    last_err = None
    for attempt, d in enumerate(durls[:3]):
        url = d["url"]
        if attempt > 0:
            print(f"  重试 CDN {attempt+1}...")
        try:
            _download_url(url, temp, BILI_HEADERS)
            transcode_to_wav(temp, output_path)
            if output_path.exists() and output_path.stat().st_size > 1000:
                break
        except Exception as e:
            last_err = e
        finally:
            temp.unlink(missing_ok=True)
    else:
        raise RuntimeError(f"所有 CDN 下载失败: {last_err}")
    elapsed = time.time() - start
    mb = output_path.stat().st_size / 1024 / 1024 if output_path.exists() else 0
    print(f"[B站] 完成: {mb:.1f} MB, 耗时 {elapsed:.0f}s")
    return output_path, info


def _download_url(url: str, dest: Path, headers: dict, max_retries: int = 3):
    """带重试的 HTTP 下载"""
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
