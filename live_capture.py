"""
音频采集模块：从 Bilibili 直播间抓取音频 + 截图

核心设计：
- 每次录制前都通过 B站官方 API (playUrl) 获取最新 flv 流地址，比 yt-dlp m3u8 更稳定
- 所有录制都采用分段方式（每段 30 秒），保证长时稳定性
- 截图功能从直播流截取单帧画面

三种模式：
1. 定时录制 --duration N：分段录满指定时长后合并
2. 跟播录制 --until-end：分段录制直到直播结束
3. 截图：从直播流取一帧存为 jpg
"""

import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Callable

from config import TEMP_DIR, OUTPUT_DIR
from danmaku import DanmakuCollector
from audio_utils import merge_wav_segments


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """安全执行子进程，避免 GBK 编码崩溃
    
    默认不捕获输出（避免管道死锁）。需要输出时传 capture_output=True。
    """
    kwargs.setdefault("timeout", 300)
    kwargs["text"] = False  # bytes 模式避免自动 GBK 解码
    try:
        result = subprocess.run(cmd, **kwargs)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"命令超时 ({kwargs.get('timeout')}s): {' '.join(cmd[:3])}")
    return result


# ==============================
#   B 站 API 工具函数
# ==============================

def extract_room_id(url: str) -> str:
    """从 Bilibili 直播 URL 中提取房间号"""
    match = re.search(r"live\.bilibili\.com/(\d+)", url)
    if not match:
        raise ValueError(f"无法从 URL 中提取房间号: {url}")
    return match.group(1)


def get_live_status(room_id: str) -> dict:
    """获取直播间基本信息"""
    result = _run(
        [sys.executable, "-m", "yt_dlp", "--dump-json",
         f"https://live.bilibili.com/{room_id}"],
        capture_output=True, timeout=30,
    )
    if result.returncode != 0:
        err = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"获取直播间信息失败:\n{err}")
    import json
    info = json.loads(result.stdout.decode("utf-8").strip().split("\n")[0])
    return {
        "title": info.get("title", "未知"),
        "is_live": info.get("is_live", False),
        "viewer_count": info.get("view_count", 0),
        "uploader": info.get("uploader", ""),
    }


def get_fresh_stream_url(room_id: str) -> str:
    """通过 B站官方 API 获取最新直播流地址（flv 格式，更稳定）"""
    import json, urllib.request
    api_url = f"https://api.live.bilibili.com/room/v1/Room/playUrl?cid={room_id}&platform=web&qn=80"
    req = urllib.request.Request(
        api_url,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://live.bilibili.com/"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        raise RuntimeError(f"B站API请求失败: {e}")

    if data.get("code") != 0:
        raise RuntimeError(f"B站API返回错误: {data}")

    durls = data.get("data", {}).get("durl", [])
    if not durls:
        raise RuntimeError("未获取到直播流地址，主播可能已下播")

    return durls[0]["url"]


# ==============================
#   分段录制引擎
# ==============================

def _record_segment(
    room_id: str,
    duration: int,
    output_path: Path,
    retries: int = 2,
) -> bool:
    """录制单段音频（requests 下载 + av 转码）"""
    from audio_utils import transcode_to_wav

    backoff = [0, 2, 5]

    for attempt in range(retries):
        if attempt > 0:
            delay = backoff[min(attempt, len(backoff) - 1)]
            print(f"    重试 {attempt}/{retries-1}，{delay}s 后刷新流地址...")
            time.sleep(delay)

        try:
            stream_url = get_fresh_stream_url(room_id)
        except RuntimeError as e:
            err = str(e)
            if any(kw in err for kw in ("下播", "未获取到", "not live", "not living")):
                if attempt == 0:
                    print(f"  [停播] 直播间 {room_id} 已下播")
                return False
            continue

        # 流式下载 + 转码
        temp = TEMP_DIR / f"_seg_{room_id}_{int(time.time())}.flv"
        try:
            import requests
            r = requests.get(stream_url,
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://live.bilibili.com"},
                stream=True, timeout=max(30, duration + 15))
            r.raise_for_status()
            with open(temp, "wb") as f:
                deadline = time.time() + duration + 15
                for chunk in r.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        break
                    f.write(chunk)
                    if time.time() > deadline:
                        break
            if temp.stat().st_size > 1000:
                transcode_to_wav(temp, output_path)
                if output_path.exists() and output_path.stat().st_size > 1000:
                    return True
            output_path.unlink(missing_ok=True)
        except Exception:
            pass
        finally:
            temp.unlink(missing_ok=True)

    return False


def _merge_wav_segments(part_paths: list[Path], output_path: Path) -> Path:
    """合并 WAV 分段（委派给 audio_utils）"""
    return merge_wav_segments(part_paths, output_path)


# ==============================
#   模式1: 定时录制
# ==============================

def capture_fixed_duration(
    room_id: str,
    duration: int,
    output_path: Optional[Path] = None,
    progress_callback: Optional[Callable] = None,
    session_dir: Optional[Path] = None,
) -> Path:
    """分段录制指定时长的音频，每段刷新流地址"""
    if output_path is None:
        output_path = TEMP_DIR / f"{room_id}_{duration}s.wav"

    # 启动前预检：直播间是否在线
    try:
        status = get_live_status(room_id)
        if not status["is_live"]:
            raise RuntimeError(f"直播间 {room_id} 当前未开播")
    except RuntimeError:
        raise
    except Exception:
        pass  # API 不通就算了，继续尝试录制

    # 启动弹幕采集
    collector = DanmakuCollector(room_id)
    collector.start()

    SEGMENT_SEC = 30
    num_full = duration // SEGMENT_SEC
    remainder = duration % SEGMENT_SEC
    total_segments = num_full + (1 if remainder > 0 else 0)
    MAX_CONSECUTIVE_FAILS = 2  # 连续失败多少次就算流断了

    print(f"[分段] 拆分 {duration} 秒为 {total_segments} 段 (每段{SEGMENT_SEC}秒)")

    part_paths: list[Path] = []
    successes = 0
    consecutive_fails = 0
    start_time = time.time()

    for i in range(num_full):
        part = TEMP_DIR / f"{room_id}_seg_{i:03d}.wav"
        part_paths.append(part)

        ok = _record_segment(room_id, SEGMENT_SEC, part)
        if ok:
            successes += 1
            consecutive_fails = 0
        else:
            consecutive_fails += 1
            elapsed = time.time() - start_time
            print(f"  [段{i+1}/{total_segments}] 异常跳过 ({consecutive_fails}/{MAX_CONSECUTIVE_FAILS} 连续失败)")

            if consecutive_fails >= MAX_CONSECUTIVE_FAILS:
                print(f"\n[中断] 连续 {consecutive_fails} 段录制失败，推流可能已中断或直播间停播")
                break

        elapsed = time.time() - start_time
        if progress_callback:
            progress_callback(elapsed)

    if remainder > 0 and consecutive_fails < MAX_CONSECUTIVE_FAILS:
        part = TEMP_DIR / f"{room_id}_seg_last.wav"
        part_paths.append(part)
        ok = _record_segment(room_id, remainder, part)
        if ok:
            successes += 1

    actual_dur = successes * SEGMENT_SEC
    actual_min = actual_dur // 60
    if successes == total_segments:
        print(f"[分段] 录制完成: {successes}/{total_segments} 段有效 ({actual_min}分{actual_dur%60}秒)")
    elif successes > 0:
        print(f"[分段] 提前结束: {successes}/{total_segments} 段有效 ({actual_min}分{actual_dur%60}秒)，推流可能中断")
    else:
        print(f"[分段] 录制失败: 全部 {total_segments} 段无效，直播流不可用")

    collector.stop()
    if session_dir:
        collector.save(session_dir / "danmaku.txt")

    return _merge_wav_segments(part_paths, output_path)


# ==============================
#   模式2: 跟播到结束
# ==============================

def capture_until_end(
    room_id: str,
    output_path: Optional[Path] = None,
    progress_callback: Optional[Callable] = None,
    session_dir: Optional[Path] = None,
) -> Path:
    """跟播模式：分段录制直到直播结束
    
    每 30 秒刷新流地址。定期检查直播间状态。
    直播结束时或用户 Ctrl+C 后自动合并。
    """
    if output_path is None:
        output_path = TEMP_DIR / f"{room_id}_full.wav"

    print(f"[跟播] 开始跟随直播，保存至: {output_path.name}")
    print(f"[跟播] 每段30秒自动续连，Ctrl+C 可手动停止")

    # 启动前预检
    try:
        status = get_live_status(room_id)
        if not status["is_live"]:
            raise RuntimeError(f"直播间 {room_id} 当前未开播")
    except RuntimeError:
        raise
    except Exception:
        pass

    # 启动弹幕采集
    collector = DanmakuCollector(room_id)
    collector.start()

    SEGMENT_SEC = 30
    MAX_CONSECUTIVE_FAILS = 2
    part_paths: list[Path] = []
    consecutive_fails = 0
    start_time = time.time()

    try:
        seg_index = 0
        while True:
            # 检查是否还在播
            try:
                status = get_live_status(room_id)
                if not status["is_live"]:
                    elapsed = time.time() - start_time
                    print(f"\n[跟播] 直播已结束，共录制 {elapsed:.0f} 秒")
                    break
            except Exception:
                # API 报错（超时/连接失败），重试一次后仍失败则视为直播结束
                import time as _t
                _t.sleep(5)
                try:
                    status = get_live_status(room_id)
                    if not status["is_live"]:
                        break
                except Exception:
                    print(f"\n[跟播] 无法获取直播状态，直播可能已结束")
                    break

            part = TEMP_DIR / f"{room_id}_seg_{seg_index:04d}.wav"
            part_paths.append(part)
            ok = _record_segment(room_id, SEGMENT_SEC, part)

            elapsed = time.time() - start_time

            if ok:
                consecutive_fails = 0
                mb = part.stat().st_size / 1024 / 1024
                total_mb = sum(p.stat().st_size for p in part_paths if p.exists()) / 1024 / 1024
                rate = total_mb * 1024 / elapsed if elapsed > 0 else 0
                mins, secs = int(elapsed // 60), int(elapsed % 60)
                print(f"  [{mins:02d}:{secs:02d}] 段{seg_index+1}: {mb:.1f} MB | 总计: {total_mb:.1f} MB | {rate:.0f} KB/s")
                if progress_callback:
                    progress_callback(elapsed)
            else:
                consecutive_fails += 1
                mins, secs = int(elapsed // 60), int(elapsed % 60)
                print(f"  [{mins:02d}:{secs:02d}] 段{seg_index+1} 异常跳过 ({consecutive_fails}/{MAX_CONSECUTIVE_FAILS} 连续失败)")
                # 去掉无效文件
                part_paths.remove(part)

                if consecutive_fails >= MAX_CONSECUTIVE_FAILS:
                    print(f"\n[中断] 连续 {consecutive_fails} 段录制失败，推流可能已中断")
                    break

            seg_index += 1

    except KeyboardInterrupt:
        print("\n[跟播] 用户手动停止")

    # 停止弹幕采集并保存
    collector.stop()
    if session_dir:
        collector.save(session_dir / "danmaku.txt")

    if not part_paths:
        raise RuntimeError(
            "录制失败：直播流无法获取。"
            "可能原因：1) 直播已结束 2) 直播间被封禁/限流 3) 网络不通"
        )

    return _merge_wav_segments(part_paths, output_path)


# ==============================
#   截图功能
# ==============================

def capture_screenshot(room_id: str, output_path: Optional[Path] = None) -> Path:
    """从直播流截取一帧画面（av 实现）"""
    import av
    if output_path is None:
        output_path = TEMP_DIR / f"{room_id}_frame.jpg"

    stream_url = get_fresh_stream_url(room_id)
    try:
        container = av.open(stream_url, options={"timeout": "15000000"})
        video = container.streams.video[0]
        for frame in container.decode(video):
            img = frame.to_image()
            img.save(str(output_path), "JPEG", quality=90)
            break
        container.close()
    except Exception as e:
        raise RuntimeError(f"截图失败: {e}")

    return output_path


# ==============================
#   视频下载模式
# ==============================

def download_video_audio(
    url: str,
    output_path: Optional[Path] = None,
) -> Path:
    """从视频链接下载音频（不依赖浏览器）
    
    使用 yt-dlp 下载最佳音质，转换为 16kHz WAV。
    支持大多数主流平台（B站、YouTube、抖音、Twitter 等）。
    如果平台不被支持或无法获取内容，抛出 RuntimeError。
    """
    if output_path is None:
        output_path = TEMP_DIR / "video_audio.wav"

    # 先探测是否可获取
    probe = subprocess.run(
        [sys.executable, "-m", "yt_dlp", "--dump-json", url],
        capture_output=True, text=False, timeout=30,
    )
    if probe.returncode != 0:
        err = probe.stderr.decode("utf-8", errors="replace").strip()[:200]
        raise RuntimeError(f"无法获取视频信息: {err}")

    print(f"[视频]   正在下载音频...")
    start = time.time()

    # yt-dlp 自行查找 ffmpeg（不再依赖 imageio_ffmpeg）
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "-f", "bestaudio",
        "--audio-format", "wav",
        "--audio-quality", "5",
        "--postprocessor-args", "ffmpeg:-ac 1 -ar 16000",
        "--no-playlist",
        "-o", str(output_path),
        url,
    ]

    process = _run(cmd, capture_output=True, timeout=3600)

    if process.returncode != 0:
        err = process.stderr.decode("utf-8", errors="replace").strip()[:300]
        raise RuntimeError(f"视频下载失败: {err}")

    # yt-dlp 可能加了扩展名，查找实际文件
    actual = Path(str(output_path) + ".wav")
    if not actual.exists():
        actual = output_path
    if not actual.exists():
        # 找目录下最近修改的 wav
        candidates = sorted(TEMP_DIR.glob("*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
        if candidates:
            actual = candidates[0]

    elapsed = time.time() - start
    mb = actual.stat().st_size / 1024 / 1024
    print(f"[视频]   下载完成: {mb:.1f} MB, 耗时 {elapsed:.0f} 秒")

    return actual


# ==============================
#   统一入口
# ==============================

def _capture_screenshots(room_id: str, session_dir: Path) -> tuple[Optional[Path], Optional[Path]]:
    """截取直播画面 + 封面图，返回 (frame_path, cover_path)"""
    import urllib.request, json

    frame_path, cover_path = None, None

    print(f"[截图]   正在截取直播画面...")
    try:
        frame_path = capture_screenshot(room_id, session_dir / "frame.jpg")
        print(f"[截图]   已保存: frame.jpg")
    except Exception as e:
        print(f"[截图]   失败: {e}")

    try:
        api_url = f"https://api.live.bilibili.com/room/v1/Room/get_info?room_id={room_id}"
        req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        cover_url = data.get("data", {}).get("user_cover", "") or data.get("data", {}).get("cover", "")
        if cover_url:
            cover_path = session_dir / "cover.jpg"
            urllib.request.urlretrieve(cover_url, str(cover_path))
            print(f"[封面]   已保存: cover.jpg")
    except Exception as e:
        print(f"[封面]   获取失败: {e}")

    return frame_path, cover_path


def _capture_audio(
    room_id: str,
    duration: Optional[int],
    session_dir: Path,
    progress_callback: Optional[Callable] = None,
) -> tuple[Path, str]:
    """录制音频，返回 (audio_path, mode)"""
    if duration is not None:
        print(f"[模式]   定时录制 {duration} 秒")
        audio_path = capture_fixed_duration(room_id, duration, session_dir / "audio.wav", progress_callback, session_dir=session_dir)
        return audio_path, "fixed"
    else:
        print(f"[模式]   跟播到结束")
        audio_path = capture_until_end(room_id, session_dir / "audio.wav", progress_callback, session_dir=session_dir)
        return audio_path, "until_end"


def capture_live(
    url: str,
    duration: Optional[int] = None,
    session_dir: Optional[Path] = None,
    progress_callback: Optional[Callable] = None,
    take_screenshot: bool = False,
) -> dict:
    """统一入口：截图 → 封面 → 音频录制"""
    if session_dir is None:
        session_dir = TEMP_DIR / "session"
    session_dir.mkdir(parents=True, exist_ok=True)

    room_id = extract_room_id(url)
    status = get_live_status(room_id)
    if not status["is_live"]:
        raise RuntimeError(f"直播间 {room_id} 当前未开播")

    print(f"[直播间] {status['title']}")
    print(f"[主播]   {status['uploader']}")
    print(f"[状态]   在线 · {status['viewer_count']} 人观看")

    # 截图和封面
    frame_path, cover_path = None, None
    if take_screenshot:
        frame_path, cover_path = _capture_screenshots(room_id, session_dir)

    # 音频
    audio_path, mode = _capture_audio(room_id, duration, session_dir, progress_callback)

    file_size_mb = audio_path.stat().st_size / 1024 / 1024
    print(f"[完成]   音频: audio.wav ({file_size_mb:.1f} MB)")

    return {
        "audio_path": audio_path,
        "screenshot_path": frame_path,
        "session_dir": session_dir,
        "room_id": room_id,
        "title": status["title"],
        "uploader": status["uploader"],
        "mode": mode,
    }
