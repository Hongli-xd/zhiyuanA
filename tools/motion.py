"""
工具：运动控制（移动指令）。

支持方向：前进、后退、左转、右转、左前方、右前方、停止。
基于 humanoid 运动控制协议，速度单位为物理量（m/s, rad/s），非角度或百分比。

安全限制（来自官方 spec）：
  - forward_velocity: 推荐 -0.8~0.8，极限 -1.5~1.5
  - lateral_velocity: 推荐 -0.3~0.3，极限 -0.5~0.5
  - angular_velocity: 推荐 -1.0~1.0，极限 -1.5~1.5
"""

import asyncio
import logging
from typing import Literal

from config import MOTION_BASE
from services.a2_client import a2_client

log = logging.getLogger("a2.tool.motion")

# 步长估算值（米），用于将"走N步"换算为运动时长
STEP_LENGTH = 0.3  # m/step


async def move(
    direction: Literal["forward", "backward", "left", "right", "left_forward", "right_forward", "stop"] = "stop",
    steps: int = 1,
    distance: float = 0,
    speed: float = 0.5,
) -> dict:
    """
    控制机器人移动方向。

    Args:
        direction: 移动方向。
            forward     - 前进
            backward    - 后退
            left        - 原地左转
            right       - 原地右转
            left_forward  - 前进+左转
            right_forward - 前进+右转
            stop        - 停止
        steps: 走几步（步长0.3m/步）。走完自动停止。distance时忽略。
        distance: 走多少米（单位米），优先级高于steps。换算为 steps = round(distance/0.3)。
        speed: 运动速度 m/s（默认0.5，最大0.9）。

    Returns:
        {"ok": bool, "message": str}
    """
    speed = min(speed, 0.9)  # 安全上限
    # distance 优先，换算为步数
    if distance > 0:
        steps = max(1, round(distance / STEP_LENGTH))

    # 速度配置：angular 正=逆时针左转，负=顺时针右转
    TABLE = {
        "stop":         {"forward": 0.0, "lateral": 0.0, "angular": 0.0},
        "forward":      {"forward": speed, "lateral": 0.0, "angular": 0.0},
        "backward":     {"forward": -speed, "lateral": 0.0, "angular": 0.0},
        "left":         {"forward": 0.0, "lateral": 0.0, "angular": speed * 0.6},
        "right":        {"forward": 0.0, "lateral": 0.0, "angular": -speed * 0.6},
        "left_forward": {"forward": speed, "lateral": 0.0, "angular": speed * 0.6},
        "right_forward":{"forward": speed, "lateral": 0.0, "angular": -speed * 0.6},
    }

    if direction not in TABLE:
        return {"ok": False, "message": f"未知方向: {direction}"}

    v = TABLE[direction]

    # 构造 payload
    payload = {
        "header": {
            "timestamp": {"seconds": 0, "nanos": 0, "ms_since_epoch": 0},
            "control_source": "ControlSource_API",
        },
        "data": {
            "mode": 0,
            "forward_velocity": v["forward"],
            "lateral_velocity": v["lateral"],
            "angular_velocity": v["angular"],
        },
    }

    # 发速度指令
    res = await a2_client.post_rpc(MOTION_BASE, payload)
    if not res["ok"]:
        return {"ok": False, "message": f"移动指令发送失败: {res.get('text', '')}"}

    # 若方向是 stop 或 steps<=0，无需等待直接返回
    if direction == "stop" or steps <= 0:
        return {"ok": True, "message": f"{direction} 指令已发送", "direction": direction}

    # 计算运动时长（秒），然后自动停止
    # 直线运动：时长 = steps × step_length / speed
    # 转向运动： angular 速度 ≈ speed*0.6 rad/s，转一圈 ≈ 2π/speed rad → 不计时长，直接返回
    if v["angular"] != 0:
        # 转向类：暂时不发停止，用户说停再说
        return {"ok": True, "message": f"{direction} 已转向", "direction": direction}

    duration = steps * STEP_LENGTH / speed
    log.info("move: 方向=%s, 速度=%.2f m/s, 步数=%d(%.1fm), 预计时长=%.1f秒",
             direction, speed, steps, distance if distance > 0 else steps * STEP_LENGTH, duration)
    await asyncio.sleep(duration)

    # 发停止指令
    stop_payload = {**payload}
    stop_payload["data"].update(forward=0.0, lateral=0.0, angular=0.0)
    await a2_client.post_rpc(MOTION_BASE, stop_payload)

    dist_msg = f"{distance}m" if distance > 0 else f"{steps}步"
    return {"ok": True, "message": f"{direction} 走了 {dist_msg}，已停止", "direction": direction}
