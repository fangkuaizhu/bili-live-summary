"""
弹幕采集模块：通过 B 站直播 WebSocket 协议实时获取弹幕

与音频录制并行运行，采集的弹幕带时间戳，最终与转写文本合并。
"""

import json
import struct
import threading
import time
from pathlib import Path
from typing import Optional

from config import TEMP_DIR

try:
    import websocket
    HAS_WS = True
except ImportError:
    HAS_WS = False

# B 站直播协议常量
HEADER_LEN = 16
OP_HEARTBEAT = 2
OP_HEARTBEAT_REPLY = 3
OP_MESSAGE = 5
OP_AUTH = 7
OP_AUTH_REPLY = 8


def _pack(op: int, body: bytes = b"") -> bytes:
    total_len = HEADER_LEN + len(body)
    return struct.pack(">IhHII", total_len, HEADER_LEN, 1, op, 1) + body


def _unpack_header(data: bytes) -> tuple:
    return struct.unpack(">IhHII", data[:16])


# ============================================
#   弹幕采集器
# ============================================

class DanmakuCollector:
    """B 站直播间弹幕采集器"""

    def __init__(self, room_id: str):
        self.room_id = room_id
        self._danmaku: list[dict] = []
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def start(self):
        if not HAS_WS:
            print("[弹幕] websocket-client 未安装，跳过")
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        print("[弹幕] 采集已启动")

    def stop(self):
        self._running = False
        print(f"[弹幕] 结束，共 {len(self._danmaku)} 条")

    def get_danmaku(self) -> list[dict]:
        return self._danmaku

    def save(self, path: Path):
        lines = [f"# 弹幕记录 ({self.room_id})"]
        for d in self._danmaku:
            ts = time.strftime("%H:%M:%S", time.localtime(d["t"]))
            lines.append(f"[{ts}] {d['u']}: {d['c']}")
        path.write_text("\n".join(lines), encoding="utf-8")

    def _run(self):
        ws = None
        try:
            ws = websocket.create_connection(
                "wss://broadcastlv.chat.bilibili.com/sub",
                timeout=10,
            )
            # 发送认证包
            auth = json.dumps({
                "uid": 0, "roomid": int(self.room_id),
                "protover": 1, "platform": "web", "type": 2,
            }).encode("utf-8")
            ws.send(_pack(OP_AUTH, auth), websocket.ABNF.OPCODE_BINARY)

            last_heartbeat = time.time()

            while self._running:
                ws.settimeout(5)
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    # 心跳
                    if time.time() - last_heartbeat > 30:
                        ws.send(_pack(OP_HEARTBEAT), websocket.ABNF.OPCODE_BINARY)
                        last_heartbeat = time.time()
                    continue
                except websocket.WebSocketConnectionClosedException:
                    break

                if not raw:
                    continue

                # 解析包
                pkt_len, hdr_len, ver, op, seq = _unpack_header(raw[:16])
                body = raw[16:pkt_len]

                if op == OP_AUTH_REPLY:
                    continue
                elif op == OP_HEARTBEAT_REPLY:
                    continue
                elif op == OP_MESSAGE:
                    # 可能被压缩（zlib）
                    if ver == 2:
                        import zlib
                        body = zlib.decompress(body)
                        # 内部可能包含多个子包
                        while body:
                            slen = struct.unpack(">I", body[:4])[0]
                            if slen < 16:
                                break
                            self._parse_msg(body[16:slen])
                            body = body[slen:]
                    else:
                        self._parse_msg(body)

        except Exception as e:
            if self._running:
                print(f"[弹幕] 连接异常: {e}")
        finally:
            if ws:
                try:
                    ws.close()
                except Exception:
                    pass

    def _parse_msg(self, data: bytes):
        try:
            msg = json.loads(data.decode("utf-8", errors="replace"))
            cmd = msg.get("cmd", "")
            if cmd == "DANMU_MSG":
                info = msg.get("info", [])
                if len(info) >= 3:
                    text = info[1]
                    uid_info = info[2] if len(info) > 2 else ["", ""]
                    uname = uid_info[1] if isinstance(uid_info, list) and len(uid_info) > 1 else ""
                    self._danmaku.append({
                        "t": time.time(),
                        "u": uname,
                        "c": text,
                    })
        except (json.JSONDecodeError, IndexError):
            pass
