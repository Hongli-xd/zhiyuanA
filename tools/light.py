"""
工具：灯带控制（等待状态指示）。

对应你给的 curl：
  POST .../HalRgbLightService/SetRgbLightCommand
  {"cmd": {"red":180,"green":0,"blue":100,"effect":2,"control":1}}

按技术选型文档的「Tool = 一次原子操作 = 一个 HTTP RPC」原则，
这是一个独立 Tool。LLM 可以用它来表达「等待中 / 工作中 / 完成」等状态，
也可以直接指定 RGB。

这同时满足你要求 3：把「等待控制」做成一个工具。
"""

import logging
from typing import Optional

from a2_agent.config import LIGHT_BASE, LIGHT_PRESETS
from a2_agent.services.a2_client import a2_client

log = logging.getLogger("a2.tool.light")


async def set_status_light(
    preset: Optional[str] = None,
    red: Optional[int] = None,
    green: Optional[int] = None,
    blue: Optional[int] = None,
    effect: int = 2,
    control: int = 1,
) -> dict:
    """
    设置 A2 灯带颜色 / 效果，用来给人指示当前状态（如等待来人）。

    优先用 preset；若同时给了 RGB 则 RGB 覆盖 preset 的颜色。

    Args:
        preset: 预设名，可选 "waiting"(等待/紫红) / "working"(工作/蓝) /
                "done"(完成/绿) / "off"(关闭)。
        red/green/blue: 0-255，手动指定颜色时使用。
        effect: 灯效编号（沿用 A2 取值，2=呼吸常亮类）。
        control: 控制位（1=开启，0=关闭）。
    """
    # 取基准
    if preset and preset in LIGHT_PRESETS:
        cmd = dict(LIGHT_PRESETS[preset])
    else:
        cmd = {"red": 0, "green": 0, "blue": 0, "effect": effect, "control": control}

    # RGB 显式覆盖
    if red is not None:
        cmd["red"] = max(0, min(255, red))
    if green is not None:
        cmd["green"] = max(0, min(255, green))
    if blue is not None:
        cmd["blue"] = max(0, min(255, blue))
    if preset is None:
        cmd["effect"] = effect
        cmd["control"] = control

    payload = {"cmd": cmd}
    res = await a2_client.post_rpc(f"{LIGHT_BASE}/SetRgbLightCommand", payload)
    status = "灯带已设置" if res["ok"] else f"灯带设置失败({res['status']})"
    log.info("set_status_light %s -> %s", cmd, res["ok"])
    return {"status": status, "applied": cmd, "ok": res["ok"]}
