"""
工具：预设动作播放。

预设动作通过 MotionCommandService HTTP RPC 调用，
motion_id 对应一个 .mcap 文件路径（机器人事先录制好的动作）。

用法：
    play_motion(name="点点头")
    play_motion(name="挥手")
"""

import asyncio
import json
import logging
from typing import Literal, Optional

from config import A2_LIGHT_HOST

log = logging.getLogger("a2.tool.motion_preset")

MOTION_BASE = f"http://{A2_LIGHT_HOST}:56444/rpc/aimdk.protocol.MotionCommandService/SendMotionCommand"

# 动作名 -> motion_id（省略 /agibot/data/resources/default/motion/ 前缀）
# 完整路径由代码拼接
MOTION_PREFIX = "/agibot/data/resources/default/motion/"

# 预设动作映射表（动作名 -> .mcap 文件名，共 133 种）
MOTION_MAP: dict[str, str] = {
    "右手比心": "右手比心/右手比心.mcap",
    "语音对话专属动作_09": "语音对话专属动作_09/语音对话专属动作_09.mcap",
    "语音对话专属动作_13": "语音对话专属动作_13/语音对话专属动作_13.mcap",
    "左手握手_长": "左手握手_长/左手握手_长.mcap",
    "通用讲解动作_29s": "通用讲解动作_29s/通用讲解动作_29s.mcap",
    "语音对话专属动作_04": "语音对话专属动作_04/语音对话专属动作_04.mcap",
    "右手碰拳_长": "右手碰拳_长/右手碰拳_长.mcap",
    "右手加油": "右手加油/右手加油.mcap",
    "语音对话专属动作_05": "语音对话专属动作_05/语音对话专属动作_05.mcap",
    "语音对话专属动作_06": "语音对话专属动作_06/语音对话专属动作_06.mcap",
    "左转头": "左转头/左转头.mcap",
    "语音对话专属动作_16": "语音对话专属动作_16/语音对话专属动作_16.mcap",
    "左手握手": "左手握手/左手握手.mcap",
    "语音对话专属动作_07": "语音对话专属动作_07/语音对话专属动作_07.mcap",
    "右手点赞": "右手点赞/右手点赞.mcap",
    "语音对话专属动作_08": "语音对话专属动作_08/语音对话专属动作_08.mcap",
    "右手比耶": "右手比耶/右手比耶.mcap",
    "点点头": "点点头/点点头.mcap",
    "摇摇头": "摇摇头/摇摇头.mcap",
    "通用讲解动作_11s": "通用讲解动作_11s/通用讲解动作_11s.mcap",
    "语音对话专属动作_02": "语音对话专属动作_02/语音对话专属动作_02.mcap",
    "通用讲解动作_8s": "通用讲解动作_8s/通用讲解动作_8s.mcap",
    "语音对话专属动作_11": "语音对话专属动作_11/语音对话专属动作_11.mcap",
    "打太极拳": "打太极拳/打太极拳.mcap",
    "通用讲解动作_22s": "通用讲解动作_22s/通用讲解动作_22s.mcap",
    "通用讲解动作_25s": "通用讲解动作_25s/通用讲解动作_25s.mcap",
    "语音对话专属动作_12": "语音对话专属动作_12/语音对话专属动作_12.mcap",
    "语音对话专属动作_10": "语音对话专属动作_10/语音对话专属动作_10.mcap",
    "右手碰拳": "右手碰拳/右手碰拳.mcap",
    "语音对话专属动作_17": "语音对话专属动作_17/语音对话专属动作_17.mcap",
    "敬礼": "敬礼/敬礼.mcap",
    "语音对话专属动作_01": "语音对话专属动作_01/语音对话专属动作_01.mcap",
    "右手握手": "右手握手/右手握手.mcap",
    "语音对话专属动作_14": "语音对话专属动作_14/语音对话专属动作_14.mcap",
    "左手比耶": "左手比耶/左手比耶.mcap",
    "左手点赞": "左手点赞/左手点赞.mcap",
    "语音对话专属动作_03": "语音对话专属动作_03/语音对话专属动作_03.mcap",
    "左手挥手": "左手挥手/左手挥手.mcap",
    "语音对话专属动作_19": "语音对话专属动作_19/语音对话专属动作_19.mcap",
    "右手挥手": "右手挥手/右手挥手.mcap",
    "右转头": "右转头/右转头.mcap",
    "右手握手_长": "右手握手_长/右手握手_长.mcap",
    "播报随机动作_18": "语音对话专属动作_18/语音对话专属动作_18.mcap",
    "语音对话专属动作_15": "语音对话专属动作_15/语音对话专属动作_15.mcap",
    "左手碰拳_长": "左手碰拳_长/左手碰拳_长.mcap",
    "向右欢迎": "向右欢迎/向右欢迎.mcap",
    "向左欢迎": "向左欢迎/向左欢迎.mcap",
    "左手碰拳": "左手碰拳/左手碰拳.mcap",
    "双手点赞": "双手点赞/双手点赞.mcap",
    "右手点赞_长": "右手点赞_长/右手点赞_长.mcap",
    "左手点赞_长": "左手点赞_长/左手点赞_长.mcap",
    "双手挥手": "双手挥手/双手挥手.mcap",
    "左手加油": "左手加油/左手加油.mcap",
    "双手加油": "双手加油/双手加油.mcap",
    "双手比耶": "双手比耶/双手比耶.mcap",
    "双手向下比心": "双手向下比心/双手向下比心.mcap",
    "双手擦眼泪": "双手擦眼泪/双手擦眼泪.mcap",
    "击掌_长": "击掌_长/击掌_长.mcap",
    "右手NO": "右手NO/右手NO.mcap",
    "右手OK": "右手OK/右手OK.mcap",
    "跳舞": "跳舞/跳舞.mcap",
    "摆拍_右手胸前比耶": "摆拍_右手胸前比耶/摆拍_右手胸前比耶.mcap",
    "摆拍_左手胸前比耶": "摆拍_左手胸前比耶/摆拍_左手胸前比耶.mcap",
    "摆拍_右手胸前点赞": "摆拍_右手胸前点赞/摆拍_右手胸前点赞.mcap",
    "摆拍_抬起右手": "摆拍_抬起右手/摆拍_抬起右手.mcap",
    "摆拍_双手咏春": "摆拍_双手咏春/摆拍_双手咏春.mcap",
    "摆拍_我是大力士": "摆拍_我是大力士/摆拍_我是大力士.mcap",
    "数字手势1": "数字手势1/数字手势1.mcap",
    "数字手势2": "数字手势2/数字手势2.mcap",
    "数字手势3": "数字手势3/数字手势3.mcap",
    "数字手势4": "数字手势4/数字手势4.mcap",
    "数字手势5": "数字手势5/数字手势5.mcap",
    "数字手势6": "数字手势6/数字手势6.mcap",
    "数字手势7": "数字手势7/数字手势7.mcap",
    "数字手势8": "数字手势8/数字手势8.mcap",
    "数字手势9": "数字手势9/数字手势9.mcap",
    "数字手势10": "数字手势10/数字手势10.mcap",
    "方向_向右上方指引": "方向_向右上方指引/方向_向右上方指引.mcap",
    "方向_向右下方指引": "方向_向右下方指引/方向_向右下方指引.mcap",
    "方向_向左上方指引": "方向_向左上方指引/方向_向左上方指引.mcap",
    "方向_向左下方指引": "方向_向左下方指引/方向_向左下方指引.mcap",
    "方向_右手指前方": "方向_右手指前方/方向_右手指前方.mcap",
    "方向_左手指前方": "方向_左手指前方/方向_左手指前方.mcap",
    "方向_右手指后方": "方向_右手指后方/方向_右手指后方.mcap",
    "方向_左手指后方": "方向_左手指后方/方向_左手指后方.mcap",
    "方向_指向当前位置": "方向_指向当前位置/方向_指向当前位置.mcap",
    "方向_指向右边": "方向_指向右边/方向_指向右边.mcap",
    "方向_指向左边": "方向_指向左边/方向_指向左边.mcap",
    "方向_右边示意": "方向_右边示意/方向_右边示意.mcap",
    "方向_左边示意": "方向_左边示意/方向_左边示意.mcap",
    "开篇打招呼_12s": "开篇打招呼_12s/开篇打招呼_12s.mcap",
    "个人介绍_7s": "个人介绍_7s/个人介绍_7s.mcap",
    "强调2大要点_12s": "强调2大要点_12s/强调2大要点_12s.mcap",
    "强调3大要点_11s": "强调3大要点_11s/强调3大要点_11s.mcap",
    "强调4大要点_16s": "强调4大要点_16s/强调4大要点_16s.mcap",
    "快速强调5大要点_8s": "快速强调5大要点_8s/快速强调5大要点_8s.mcap",
    "对1个要点展开阐述_14s": "对1个要点展开阐述_14s/对1个要点展开阐述_14s.mcap",
    "对2个要点展开阐述_18s": "对2个要点展开阐述_18s/对2个要点展开阐述_18s.mcap",
    "对3个要点展开阐述_15s": "对3个要点展开阐述_15s/对3个要点展开阐述_15s.mcap",
    "简述6个要点_12s": "简述6个要点_12s/简述6个要点_12s.mcap",
    "做自上而下的示意_10s": "做自上而下的示意_10s/做自上而下的示意_10s.mcap",
    "用手指强调信息_09s": "用手指强调信息_09s/用手指强调信息_09s.mcap",
    "用握拳强调2组信息_12s": "用握拳强调2组信息_12s/用握拳强调2组信息_12s.mcap",
    "双手强调全局_08s": "双手强调全局_08s/双手强调全局_08s.mcap",
    "右手强调核心_10s": "右手强调核心_10s/右手强调核心_10s.mcap",
    "讲解结束总结_9s": "讲解结束总结_9s/讲解结束总结_9s.mcap",
    "结尾致谢_7s": "结尾致谢_7s/结尾致谢_7s.mcap",
    "右手握手收回": "右手握手收回/右手握手收回.mcap",
    "右手拍照pose1-短": "右手拍照pose1-短/右手拍照pose1-短.mcap",
    "右手拍照pose2-短": "右手拍照pose2-短/右手拍照pose2-短.mcap",
    "右手拍照pose3-短": "右手拍照pose3-短/右手拍照pose3-短.mcap",
    "右手拍照pose4-短": "右手拍照pose4-短/右手拍照pose4-短.mcap",
    "右手拍照pose5-短": "右手拍照pose5-短/右手拍照pose5-短.mcap",
    "灵动环顾1": "灵动环顾1/灵动环顾1.mcap",
    "灵动环顾2": "灵动环顾2/灵动环顾2.mcap",
    "灵动环顾3": "灵动环顾3/灵动环顾3.mcap",
    "灵动环顾4": "灵动环顾4/灵动环顾4.mcap",
    "比心": "比心/比心.mcap",
    "单手发红包": "单手发红包/单手发红包.mcap",
    "端盘子": "端盘子/端盘子.mcap",
    "捏住东西然后递出": "捏住东西然后递出/捏住东西然后递出.mcap",
    "下蹲感谢示意": "下蹲感谢示意/下蹲感谢示意.mcap",
    "拿话筒演讲": "拿话筒演讲/拿话筒演讲.mcap",
    "快速端东西": "快速端东西/快速端东西.mcap",
    "端东西": "端东西/端东西.mcap",
    "右手拿话筒左手讲解1": "右手拿话筒左手讲解1/右手拿话筒左手讲解1.mcap",
    "右手拿话筒左手讲解2": "右手拿话筒左手讲解2/右手拿话筒左手讲解2.mcap",
    "右手拿话筒左手讲解3": "右手拿话筒左手讲解3/右手拿话筒左手讲解3.mcap",
    "右手拿话筒双侧扭腰": "右手拿话筒双侧扭腰/右手拿话筒双侧扭腰.mcap",
    "右手拿话筒双侧扭腰2": "右手拿话筒双侧扭腰2/右手拿话筒双侧扭腰2.mcap",
    "右手拿话筒轻微扭腰": "右手拿话筒轻微扭腰/右手拿话筒轻微扭腰.mcap",
    "右手拿话筒大幅扭腰": "右手拿话筒大幅扭腰/右手拿话筒大幅扭腰.mcap",
    "握话筒": "握话筒/握话筒.mcap",
}


def _fuzzy_match(name: str, candidates: list[str], top_n: int = 3) -> list[tuple[str, float]]:
    """
    简单模糊匹配：按字符重叠率打分，返回最接近的 top_n 个。
    name: 用户输入
    candidates: 候选列表
    返回: [(候选名, score), ...] 按 score 降序
    """
    import re
    name_clean = re.sub(r'[\s\-_]', '', name)
    scores = []
    for c in candidates:
        c_clean = re.sub(r'[\s\-_]', '', c)
        # 重叠字符数 / max(len(name), len(c))
        overlap = len(set(name_clean) & set(c_clean))
        score = overlap / max(len(name_clean), len(c_clean), 1)
        # 包含关系加权
        if name_clean in c_clean or c_clean in name_clean:
            score += 0.3
        # 连续子串匹配
        for i in range(len(name_clean)):
            for j in range(i + 1, len(name_clean) + 1):
                sub = name_clean[i:j]
                if sub in c_clean:
                    score += 0.1 * len(sub) / max(len(name_clean), 1)
        scores.append((c, min(score, 1.0)))
    scores.sort(key=lambda x: x[1], reverse=True)
    return scores[:top_n]


async def play_motion(name: str, duration_ms: int = 10000) -> dict:
    """
    播放预设动作。

    Args:
        name: 动作名称（如"点点头"、"挥手"）。
        duration_ms: 动作持续时间（毫秒），默认 10000。

    Returns:
        {"ok": bool, "message": str}
    """
    if name not in MOTION_MAP:
        top_matches = _fuzzy_match(name, list(MOTION_MAP.keys()))
        if top_matches and top_matches[0][1] > 0.2:
            best, _ = top_matches[0]
            return {
                "ok": False,
                "suggest": best,
                "message": f"没有'{name}'这个动作，最接近的是'{best}'。"
            }
        available = ", ".join(sorted(MOTION_MAP.keys())[:10])
        return {
            "ok": False,
            "message": f"未知动作: {name}，可用示例: {available}..."
        }

    motion_id = MOTION_PREFIX + MOTION_MAP[name]
    payload = {
        "motion_id": motion_id,
        "duration_ms": duration_ms,
        "cmd_end": True,
        "cmd_pause": False,
        "cmd_reset": False,
    }

    body = json.dumps(payload, ensure_ascii=False)
    log.info("🤖 [motion] >>> curl POST %s body=%s", MOTION_BASE, body[:100])
    try:
        proc = await asyncio.create_subprocess_exec(
            "curl", "-s", "-X", "POST", MOTION_BASE,
            "-H", "content-type:application/json",
            "-d", body,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout=5)
        resp_text = stdout.decode().strip()
        ok = proc.returncode == 0 and bool(resp_text)
        log.info("play_motion(%s) -> ok=%s resp=%s", name, ok, resp_text[:200])
        if ok:
            return {"ok": True, "message": f"动作 {name} 已播放"}
        return {"ok": False, "message": f"动作播放失败: {resp_text[:100]}"}
    except asyncio.TimeoutError:
        return {"ok": False, "message": "动作播放超时"}
    except Exception as e:
        log.error("play_motion(%s) 失败: %s", name, e)
        return {"ok": False, "message": str(e)}
