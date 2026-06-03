"""
A2 TTS 播放服务封装（文档 7.5）。

- play_tts(): 调 TTSService/PlayTTS 播报文本。
- stop_all(): 调 TTSService/StopTTS 清空播报队列 —— 用户打断时调用。

设计为 Pipecat 的 TTSService 适配：当 Pipecat 输出文本帧时调 play_tts；
当框架广播 InterruptionFrame（用户插话）时调 stop_all。
"""

import logging
import uuid

from config import TTS_BASE, TTS_PRIORITY, TTS_DOMAIN
from services.a2_client import a2_client

log = logging.getLogger("a2.tts")


async def play_tts(text: str, interrupt: bool = True) -> dict:
    """
    在 A2 上播报一段文本。

    interrupt=True 表示打断同优先级正在播的内容（默认，文档建议默认 true）。
    返回 trace_id，便于后续查询/打断。
    """
    text = (text or "").strip()
    if not text:
        return {"ok": False, "reason": "空文本"}

    # 文档：单次最高约 1024 字节 / 约 200 个中文，超长要分片
    trace_id = f"agent_{uuid.uuid4().hex[:12]}"
    payload = {
        "text": text[:1024],
        "priority_level": TTS_PRIORITY,
        "domain": TTS_DOMAIN,
        "trace_id": trace_id,
        "is_interrupted": interrupt,
    }
    res = await a2_client.post_rpc(f"{TTS_BASE}/PlayTTS", payload)
    log.info("🔊 [4/4] TTS 播放 -> 「%s」 trace_id=%s", text, trace_id)
    return {"ok": res["ok"], "trace_id": trace_id, "raw": res}


async def stop_all_tts() -> dict:
    """终止当前及队列中所有 TTS（用户打断 / 需要立刻安静时调用）。"""
    res = await a2_client.post_rpc(f"{TTS_BASE}/StopTTS", {})
    log.info("🔇 TTS 停止")
    return res
