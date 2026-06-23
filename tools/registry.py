"""
工具注册层。

把「工具」和「技能」统一注册给 Pipecat 的 LLM service。
采用技术选型文档推荐的 FunctionSchema 方式（精确控制：enum 约束、必填字段）。

注册的能力：
  1) set_status_light        —— 工具：灯带/等待状态指示
  2) launch_aimmaster_task   —— 技能：启动 AimMaster 任务（LangGraph StateGraph）

每个 handler 把 result 通过 params.result_callback 回传，
Pipecat 会自动把结果作为 tool_result 注入对话上下文，触发 LLM 生成回复。
"""

import logging

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema

from config import TASK_NAMES
from services.a2_client import confidence_gate
from skills.launch_task import run_launch_task_skill
from tools.light import set_status_light
from tools.motion import move
from tools.motion_preset import play_motion

log = logging.getLogger("a2.registry")


# ── Schema 定义 ─────────────────────────────────────────────────────────────
_task_id_enum = list(TASK_NAMES.keys())

light_schema = FunctionSchema(
    name="set_status_light",
    description="设置A2机器人灯带颜色/效果，用于向现场人员指示当前状态。预设：waiting=等待(紫红), working=工作(蓝), done=完成(绿), off=关闭。也支持自定义RGB(0-255)。",
    properties={
        "preset": {
            "type": "string",
            "enum": ["waiting", "working", "done", "off"],
        },
        "red": {"type": "integer", "minimum": 0, "maximum": 255},
        "green": {"type": "integer", "minimum": 0, "maximum": 255},
        "blue": {"type": "integer", "minimum": 0, "maximum": 255},
    },
    required=[],
)

motion_schema = FunctionSchema(
    name="move",
    description="控制机器人移动方向和步数/距离。直线运动走N步或指定距离后自动停止，转向类（原地左/右转）只发指令不计时长。速度默认0.5m/s，用户要求快时提高到0.9m/s。",
    properties={
        "direction": {
            "type": "string",
            "enum": ["forward", "backward", "left", "right", "left_forward", "right_forward", "stop"],
            "description": "移动方向：forward=前进, backward=后退, left=原地左转, right=原地右转, left_forward=左前方, right_forward=右前方, stop=停止",
        },
        "steps": {
            "type": "integer",
            "description": "走几步（默认1步，步长约0.3米）。distance>0时忽略。",
            "default": 1,
        },
        "distance": {
            "type": "number",
            "description": "走多少米（单位米），优先级高于steps，自动换算为步数（distance/0.3）。",
        },
        "speed": {
            "type": "number",
            "description": "运动速度 m/s（默认0.5，最大0.9。用户要求快时提高到0.9）。",
            "default": 0.5,
        },
    },
    required=["direction"],
)

launch_task_schema = FunctionSchema(
    name="launch_aimmaster_task",
    description=(
        "启动一个在 AimMaster 上预先创建好的任务。内部自动完成：切换Auto模式→设置当前任务→启动任务三步。"
    ),
    properties={
        "task_id": {
            "type": "string",
            "enum": _task_id_enum,
            "description": f"任务ID。{TASK_NAMES}",
        },
        "confidence": {
            "type": "number",
            "description": "本次指令识别置信度0-1，可选；低于阈值会被安全门拦截",
        },
    },
    required=["task_id"],
)

motion_preset_schema = FunctionSchema(
    name="play_motion",
    description="播放机器人预设动作（如挥手、点头、鞠躬等）。动作名精确匹配预设列表中的名称。",
    properties={
        "name": {
            "type": "string",
            "description": "动作名称（如：点点头、挥手、打招呼等），精确匹配动作库中的名称。",
        },
        "duration_ms": {
            "type": "integer",
            "description": "动作持续时间（毫秒），默认 10000。",
            "default": 10000,
        },
    },
    required=["name"],
)


def get_tools_schema() -> ToolsSchema:
    return ToolsSchema(
        standard_tools=[light_schema, launch_task_schema, motion_schema, motion_preset_schema]
    )


# ── Handlers ────────────────────────────────────────────────────────────────
async def _handle_set_light(params):
    a = params.arguments
    log.info("🔧 [3/4] 调用工具 set_status_light -> preset=%s, RGB=(%s,%s,%s)",
             a.get("preset"), a.get("red"), a.get("green"), a.get("blue"))
    res = await set_status_light(
        preset=a.get("preset"),
        red=a.get("red"),
        green=a.get("green"),
        blue=a.get("blue"),
    )
    log.info("✅ [5/5] set_status_light 执行结果 -> %s", res)
    await params.result_callback(res)


async def _handle_move(params):
    a = params.arguments
    direction = a.get("direction")
    steps = a.get("steps", 1)
    distance = a.get("distance", 0)
    speed = a.get("speed", 0.5)
    log.info("🔧 [3/4] 调用工具 move -> direction=%s, steps=%s, distance=%s, speed=%s",
             direction, steps, distance, speed)
    res = await move(direction=direction, steps=steps, distance=distance, speed=speed)
    log.info("✅ [5/5] move 执行结果 -> %s", res)
    await params.result_callback(res)


async def _handle_play_motion(params):
    a = params.arguments
    name = a.get("name")
    duration_ms = a.get("duration_ms", 10000)
    log.info("🔧 [3/4] 调用工具 play_motion -> name=%s, duration_ms=%s", name, duration_ms)
    res = await play_motion(name=name, duration_ms=duration_ms)
    log.info("✅ [5/5] play_motion 执行结果 -> %s", res)
    await params.result_callback(res)


async def _handle_launch_task(params):
    a = params.arguments
    # 安全兜底：任务启动会触发机器人物理动作（导航/运动），做置信度检查
    ok, msg = confidence_gate.check(a.get("confidence"), "launch_aimmaster_task")
    if not ok:
        log.warning("⛔ [3/4] launch_aimmaster_task 安全拦截 -> %s", msg)
        await params.result_callback({"ok": False, "message": f"安全拦截：{msg}"})
        return
    task_id = a.get("task_id")
    log.info("🔧 [3/4] 调用工具 launch_aimmaster_task -> task_id=%s", task_id)
    res = await run_launch_task_skill(task_id=task_id)
    log.info("✅ [5/5] launch_aimmaster_task 执行结果 -> ok=%s, task_id=%s, message=%s",
             res.get("ok"), res.get("task_id"), res.get("message"))
    await params.result_callback(res)


def register_all(llm) -> None:
    """把所有 handler 注册到 Pipecat 的 LLM service。"""
    llm.register_function("set_status_light", _handle_set_light)
    llm.register_function("move", _handle_move)
    llm.register_function("play_motion", _handle_play_motion)
    llm.register_function("launch_aimmaster_task", _handle_launch_task)
    log.info("已注册工具: set_status_light, move, play_motion, launch_aimmaster_task")