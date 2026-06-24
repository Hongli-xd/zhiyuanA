"""
动作触发器：根据回复文本自动选最合适的一个动作。

策略：穷举语义关键词 → 按优先级顺序匹配 → 返回最匹配的动作。
一个回复只选一个最重要的动作。
"""

import re
from typing import Optional


def pick_motion_for_response(text: str) -> Optional[str]:
    """
    根据回复内容选最合适的一个预设动作。
    """
    # ── 1. 手势类（最具体，不易误触发）──────────────
    if _match(r"握个手", text): return "右手握手"
    if _match(r"点个头", text) or _match(r"点下头", text) or _match(r"点一下头", text): return "点点头"
    if _match(r"给我点", text) and _match(r"头", text): return "点点头"
    if _match(r"点赞", text) or _match(r"太棒了", text) or _match(r"真棒", text): return "右手点赞"
    if _match(r"加油", text) or _match(r"加个油", text) or _match(r"给我加", text): return "双手加油"
    if _match(r"比心", text) or _match(r"爱心", text): return "双手向下比心"
    if _match(r"挥手", text) or _match(r"挥挥手", text): return "双手挥手"
    if _match(r"比耶", text) or _match(r"胜利", text): return "双手比耶"
    if _match(r"OK手势", text) or _match(r"比OK", text): return "右手OK"
    if _match(r"NO手势", text) or _match(r"比NO", text) or _match(r"摇头", text): return "右手NO"
    if _match(r"请坐", text) or _match(r"坐下吧", text): return "下蹲感谢示意"
    if _match(r"鼓掌", text): return "双手加油"
    if _match(r"敬礼", text) or _match(r"敬个礼", text) or _match(r"给我敬", text): return "敬礼"

    # ── 2. 欢迎/礼仪 ──────────────────────────────
    if _match(r"感谢", text) or _match(r"谢谢", text) or _match(r"致谢", text): return "结尾致谢_7s"
    if _match(r"恭喜发财", text): return "单手发红包"
    if _match(r"欢迎来到", text) or _match(r"欢迎各位", text): return "向右欢迎"
    if _match(r"欢迎大家", text): return "向左欢迎"
    if _match(r"自我介绍一下", text): return "开篇打招呼_12s"
    if _match(r"介绍一下", text) and not _match(r"号", text): return "开篇打招呼_12s"
    if _match(r"大家好", text) and not _match(r"号", text): return "开篇打招呼_12s"

    # ── 3. 数字（只在明确计数场景触发，避免"一号产品"误触发）────
    if _has_counting(text): return "数字手势1"

    # ── 4. 引导方向 / 移动 ───────────────────────
    if _match(r"看这边", text) or _match(r"请看", text) or _match(r"看向", text): return "右转头"
    if _match(r"看左边", text) or _match(r"左侧", text) or _match(r"左边", text): return "左转头"
    if _match(r"看右边", text) or _match(r"右侧", text) or _match(r"右边", text): return "右转头"
    if _match(r"后退", text): return "通用讲解动作_8s"
    if _match(r"前进", text): return "通用讲解动作_8s"

    # ── 5. 讲解结构 ──────────────────────────────
    if _match(r"总结一下", text) or _match(r"概括", text) or _match(r"总之", text): return "讲解结束总结_9s"
    if _match(r"自上而下", text): return "做自上而下的示意_10s"
    if _match(r"全局", text) or _match(r"整体来看", text): return "双手强调全局_08s"
    if _match(r"核心", text) or _match(r"最关键", text): return "右手强调核心_10s"
    if _match(r"详细展开", text) or _match(r"具体展开", text): return "对1个要点展开阐述_14s"
    n = _count_digit_words(text)
    if _match(r"要点", text) or _match(r"重点", text):
        if n >= 4: return "快速强调5大要点_8s"
        if n == 3: return "强调3大要点_11s"
        if n == 2: return "强调2大要点_12s"
        if n == 1: return "通用讲解动作_11s"

    # ── 6. 演讲/娱乐 ───────────────────────────────
    if _match(r"演讲", text) or _match(r"讲话", text): return "拿话筒演讲"
    if _match(r"拍照", text) or _match(r"茄子", text): return "右手拍照pose1-短"
    if _match(r"跳舞", text): return "跳舞"
    if _match(r"太极", text): return "打太极拳"
    if _match(r"灵动", text) or _match(r"四周看看", text): return "灵动环顾1"
    if _match(r"端盘子", text) or _match(r"端东西", text): return "端东西"

    return None


def _match(pattern: str, text: str) -> bool:
    """判断 text 中是否存在 pattern（支持中文正则）。"""
    return bool(re.search(pattern, text))


def _has_counting(text: str) -> bool:
    """text 中是否含明确的计数词（排除"一号产品"类）。"""
    return bool(re.search(
        r"一个|两个|三个|四个|五个|六个|七个|八个|九个|十个|"
        r"第一|第二|第三|第四|第五|第六|第七|第八|第九|第十|"
        r"一、二、三、四，五、六、七，八、九、十|"
        r"个要点|个重点",
        text
    ))


def _count_digit_words(text: str) -> int:
    """统计 text 中出现的明确计数词数量。"""
    return len(re.findall(
        r"一个|两个|三个|四个|五个|六个|七个|八个|九个|十个|"
        r"第一|第二|第三|第四|第五|第六|第七|第八|第九|第十",
        text
    ))


def get_motion_duration_ms(name: str) -> int:
    """根据动作名估算持续时间（毫秒），用于领先触发 TTS。"""
    DURATIONS = {
        "结尾致谢_7s": 7100,
        "开篇打招呼_12s": 12700,
        "数字手势1": 7000,
        "右转头": 9600,
        "左转头": 7600,
        "右手握手": 16700,
        "左手握手": 17200,
        "右手OK": 14100,
        "右手NO": 6300,
        "双手加油": 4900,
        "双手挥手": 8600,
        "双手比耶": 7100,
        "双手向下比心": 8300,
        "双手强调全局_08s": 8800,
        "右手强调核心_10s": 10400,
        "强调2大要点_12s": 12400,
        "强调3大要点_11s": 11500,
        "快速强调5大要点_8s": 7100,
        "通用讲解动作_11s": 11400,
        "通用讲解动作_8s": 8100,
        "对1个要点展开阐述_14s": 14800,
        "讲解结束总结_9s": 9800,
        "端东西": 10700,
        "拿话筒演讲": 8900,
        "右手拍照pose1-短": 14100,
        "跳舞": 71900,
        "打太极拳": 39200,
        "灵动环顾1": 20100,
        "单手发红包": 22100,
        "下蹲感谢示意": 8200,
        "做自上而下的示意_10s": 10800,
        "向右欢迎": 9300,
        "向左欢迎": 8000,
        "右手点赞": 6500,
        "敬礼": 5700,
        "鼓掌": 5000,
        "左右看": 7600,
    }
    for key, ms in DURATIONS.items():
        if key in name or name in key:
            return ms
    return 10000
