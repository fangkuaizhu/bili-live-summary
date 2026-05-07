"""
Job 队列管理器：跨进程 GPU 排队调度

让多个 Agent 安全并发提交任务，GPU 模型常驻、逐个排队处理。

架构:
  代理人 ── enqueue ──►  queue/pending/  ──►  Daemon (GPU)  ──►  queue/done/

用法:
  # 代理人：提交任务
  queue_enqueue("url", "https://live.bilibili.com/xxx")

  # 代理人：提交并等待
  queue_enqueue_wait("video", "https://...")

  # 守护进程：常驻处理
  QueueDaemon().run()
"""

import json
import os
import shutil
import time
import uuid
import traceback
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

# ── 队列目录 ──
_QUEUE_BASE = Path(__file__).resolve().parent / "queue"
PENDING_DIR  = _QUEUE_BASE / "pending"
RUNNING_DIR  = _QUEUE_BASE / "running"
DONE_DIR     = _QUEUE_BASE / "done"
FAILED_DIR   = _QUEUE_BASE / "failed"
ALL_DIRS     = [PENDING_DIR, RUNNING_DIR, DONE_DIR, FAILED_DIR]

JOB_PREFIX  = "job_"
LOCK_FILE   = _QUEUE_BASE / ".daemon.lock"


def _ensure_dirs():
    for d in ALL_DIRS:
        d.mkdir(parents=True, exist_ok=True)


# ── 队列状态 ──────────────────────────────────

def queue_status() -> dict:
    """返回各状态队列的统计信息"""
    _ensure_dirs()
    result = {
        "pending": len(list(PENDING_DIR.glob(f"{JOB_PREFIX}*.json"))),
        "running": len(list(RUNNING_DIR.glob(f"{JOB_PREFIX}*.json"))),
        "done":    len(list(DONE_DIR.glob(f"{JOB_PREFIX}*.json"))),
        "failed":  len(list(FAILED_DIR.glob(f"{JOB_PREFIX}*.json"))),
        "daemon_alive": _is_daemon_alive(),
    }
    return result


def _is_daemon_alive() -> bool:
    if not LOCK_FILE.exists():
        return False
    try:
        pid = int(LOCK_FILE.read_text().strip())
        os.kill(pid, 0)  # 仅检查存在，不发送信号
        return True
    except (ValueError, OSError):
        return False


# ── 入队（代理人调用） ──────────────────────

def queue_enqueue(
    job_type: str,       # "url" | "video" | "audio"
    source: str,
    scene: str = "general",
    **kwargs,
) -> str:
    """提交 job，返回 job_id（不等待）"""
    _ensure_dirs()
    job_id = uuid.uuid4().hex[:12]
    job = {
        "id":            job_id,
        "type":          job_type,
        "source":        source,
        "scene":         scene,
        "status":        "pending",
        "created_at":    datetime.now(timezone.utc).isoformat(),
        "result_dir":    None,
        "error":         None,
    }
    job.update(kwargs)
    (PENDING_DIR / f"{JOB_PREFIX}{job_id}.json").write_text(
        json.dumps(job, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return job_id


def queue_enqueue_wait(
    job_type: str,
    source: str,
    scene: str = "general",
    poll_interval: float = 2.0,
    timeout: int = 600,
    **kwargs,
) -> dict:
    """提交 job 并等待完成，返回结果 dict"""
    job_id = queue_enqueue(job_type, source, scene, **kwargs)
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = _read_job(job_id)
        if result:
            return result
        time.sleep(poll_interval)
    return {"id": job_id, "status": "timeout", "error": f"超过 {timeout}s 未完成"}


def _read_job(job_id: str) -> Optional[dict]:
    for d in (DONE_DIR, FAILED_DIR):
        f = d / f"{JOB_PREFIX}{job_id}.json"
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
    return None


# ── 守护进程 ──────────────────────────────────

class QueueDaemon:
    """模型常驻的队列处理器，逐个处理 pending job"""

    def __init__(self, poll_interval: float = 2.0):
        self.poll_interval = poll_interval
        self._running = False
        self._model_loaded = False

    def run(self):
        """启动 daemon"""
        _ensure_dirs()

        # 抢锁
        if not self._acquire_lock():
            return

        self._running = True
        print(f"[Daemon] PID={os.getpid()} 已启动（扫描间隔 {self.poll_interval}s）")
        print(f"[Daemon] Pending: {PENDING_DIR}")
        print(f"[Daemon] Done:    {DONE_DIR}")

        try:
            while self._running:
                jobs = sorted(PENDING_DIR.glob(f"{JOB_PREFIX}*.json"))
                for job_file in jobs:
                    if not self._running:
                        break
                    if not job_file.exists():
                        continue  # 可能已被其他进程处理
                    self._process_job(job_file)
                time.sleep(self.poll_interval)
        finally:
            self._release_lock()
            print("[Daemon] 已停止")

    def stop(self):
        self._running = False

    def _acquire_lock(self) -> bool:
        """尝试获取守护进程锁，防止重复启动"""
        if _is_daemon_alive():
            pid = LOCK_FILE.read_text().strip() if LOCK_FILE.exists() else "?"
            print(f"[错误] Daemon 已在运行 (PID={pid})，不能重复启动")
            return False
        LOCK_FILE.write_text(str(os.getpid()))
        return True

    def _release_lock(self):
        try:
            LOCK_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    def _process_job(self, job_file: Path):
        job = json.loads(job_file.read_text(encoding="utf-8"))
        job_id = job["id"]

        # 移入 running
        job["status"] = "running"
        running_file = RUNNING_DIR / job_file.name
        shutil.move(str(job_file), str(running_file))
        running_file.write_text(json.dumps(job, indent=2, ensure_ascii=False), encoding="utf-8")

        print(f"\n[Daemon] [{job_id}] {job['type']}: {job['source'][:60]}")

        # 预加载模型（仅首次）
        if not self._model_loaded:
            print(f"[Daemon] 加载 Whisper 模型...")
            try:
                from transcriber import get_model
                _ = get_model()
                self._model_loaded = True
                print(f"[Daemon] 模型就绪")
            except Exception as e:
                print(f"[Daemon] 模型加载失败 ({e})，回退到 CPU")
                import config as cfg
                cfg.WHISPER_DEVICE = "cpu"
                cfg.WHISPER_COMPUTE_TYPE = "int8"
                self._model_loaded = True  # 不重复尝试

        # 每个 job 独立临时目录，避免文件冲突
        import config as cfg
        instance_temp = cfg.TEMP_DIR.parent / f"temp_{job_id}"
        instance_temp.mkdir(parents=True, exist_ok=True)
        old_temp = cfg.TEMP_DIR
        cfg.TEMP_DIR = instance_temp

        try:
            from pipeline import process_live, process_video, process_audio

            job_type = job["type"]
            source   = job["source"]
            scene    = job.get("scene", "general")
            # 仅传递 pipeline 需要的参数
            kw = {}
            for k in ("no_summarize", "screenshot", "duration"):
                if k in job and job[k] is not None:
                    kw[k] = job[k]

            if job_type == "url":
                result_dir = process_live(source, scene=scene, **kw)
            elif job_type == "video":
                result_dir = process_video(source, scene=scene, **kw)
            elif job_type == "audio":
                result_dir = process_audio(Path(source), scene=scene, **kw)
            else:
                raise ValueError(f"未知 job type: {job_type}")

            job["status"]       = "done"
            job["result_dir"]   = str(result_dir)
            job["completed_at"] = datetime.now(timezone.utc).isoformat()
            (DONE_DIR / f"{JOB_PREFIX}{job_id}.json").write_text(
                json.dumps(job, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            print(f"[Daemon] [{job_id}] [OK] {result_dir}")

        except Exception as e:
            job["status"]    = "failed"
            job["error"]     = f"{type(e).__name__}: {e}"
            job["traceback"] = traceback.format_exc()
            (FAILED_DIR / f"{JOB_PREFIX}{job_id}.json").write_text(
                json.dumps(job, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            print(f"[Daemon] [{job_id}] [FAIL] {e}")

        finally:
            # 清理临时目录（独立 try，防止 rmtree 失败影响 TEMP_DIR 重置）
            try:
                shutil.rmtree(instance_temp, ignore_errors=True)
            except Exception:
                pass
            cfg.TEMP_DIR = old_temp
            if running_file.exists():
                running_file.unlink()
