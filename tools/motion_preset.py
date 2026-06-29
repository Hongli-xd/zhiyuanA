"""
工具：预设动作播放。

预设动作通过 MotionCommandService HTTP RPC 调用，
motion_id 对应一个 .mcap 文件路径（机器人事先录制好的动作）。

用法：
    play_motion(name="点点头")
    play_motion(name="挥手")
"""

import json
import logging
import subprocess
from typing import Optional

from config import A2_LIGHT_HOST

log = logging.getLogger("a2.tool.motion_preset")

MOTION_BASE = f"http://{A2_LIGHT_HOST}:56444/rpc/aimdk.protocol.MotionCommandService/SendMotionCommand"
MOTION_PREFIX = "/agibot/data/resources/default/motion/"

_running_motions: set[str] = set()

MOTION_MAP: dict[str, str] = {
    "敬礼": "敬礼/敬礼.mcap",
    "打太极拳": "打太极拳/打太极拳.mcap",
    "点点头": "点点头/点点头.mcap",
    "挥手": "挥手/挥手.mcap",
    "跳舞": "跳舞/跳舞.mcap",
    "左手握手": "左手握手/左手握手.mcap",
    "右手握手": "右手握手/右手握手.mcap",
    "左手比耶": "左手比耶/左手比耶.mcap",
    "右手比耶": "右手比耶/右手比耶.mcap",
    "双手比耶": "双手比耶/双手比耶.mcap",
    "左手点赞": "左手点赞/左手点赞.mcap",
    "右手点赞": "右手点赞/右手点赞.mcap",
    "双手点赞": "双手点赞/双手点赞.mcap",
    "左手加油": "左手加油/左手加油.mcap",
    "右手加油": "右手加油/右手加油.mcap",
    "双手加油": "双手加油/双手加油.mcap",
    "左手挥手": "左手挥手/左手挥手.mcap",
    "右手挥手": "右手挥手/右手挥手.mcap",
    "双手挥手": "双手挥手/双手挥手.mcap",
    "左转头": "左转头/左转头.mcap",
    "右转头": "右转头/右转头.mcap",
    "向左欢迎": "向左欢迎/向左欢迎.mcap",
    "向右欢迎": "向右欢迎/向右欢迎.mcap",
    "开篇打招呼_12s": "开篇打招呼_12s/开篇打招呼_12s.mcap",
    "个人介绍_7s": "个人介绍_7s/个人介绍_7s.mcap",
    "结尾致谢_7s": "结尾致谢_7s/结尾致谢_7s.mcap",
    "比心": "比心/比心.mcap",
    "右手比心": "右手比心/右手比心.mcap",
    "双手向下比心": "双手向下比心/双手向下比心.mcap",
    "太极": "打太极拳/打太极拳.mcap",
}


async def play_motion(name: str, duration_ms: int = 10000) -> dict:
    if name in _running_motions:
        return {"ok": True, "message": f"动作「{name}」执行中，跳过"}

    _running_motions.add(name)
    try:
        if name not in MOTION_MAP:
            _running_motions.discard(name)
            # fuzzy match
            top_matches = _fuzzy_match(name, list(MOTION_MAP.keys()))
            if top_matches and top_matches[0][1] > 0.2:
                best, _ = top_matches[0]
                return {"ok": False, "suggest": best, "message": f"没有「{name}」，最接近的是「{best}」"}
            return {"ok": False, "message": f"未知动作「{name}」"}

        motion_id = MOTION_PREFIX + MOTION_MAP[name]
        payload = {
            "motion_id": motion_id,
            "duration_ms": duration_ms,
            "cmd_end": True,
            "cmd_pause": False,
            "cmd_reset": False,
        }
        body = json.dumps(payload, ensure_ascii=False)
        cmd = [
            "curl", "-s", "-X", "POST", MOTION_BASE,
            "-H", "content-type:application/json",
            "-d", body,
        ]
        log.info("[motion] ▶ curl %s body=%s", MOTION_BASE, body[:80])
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        resp_text = result.stdout.strip()
        if result.returncode == 0 and resp_text:
            log.info("[motion] ✅ ok resp=%s", resp_text[:100])
            return {"ok": True, "message": f"动作「{name}」已播放"}
        log.warning("[motion] ❌ rc=%d resp=%s", result.returncode, resp_text[:100])
        return {"ok": False, "message": f"动作「{name}」执行失败"}
    finally:
        _running_motions.discard(name)


def _fuzzy_match(name: str, candidates: list[str], top_n: int = 3) -> list[tuple[str, float]]:
    import re
    name_clean = re.sub(r"[\s\-_]", "", name)
    scores = []
    for c in candidates:
        c_clean = re.sub(r"[\s\-_]", "", c)
        overlap = len(set(name_clean) & set(c_clean))
        score = overlap / max(len(name_clean), len(c_clean), 1)
        if name_clean in c_clean or c_clean in name_clean:
            score += 0.3
        scores.append((c, min(score, 1.0)))
    scores.sort(key=lambda x: x[1], reverse=True)
    return scores[:top_n]
