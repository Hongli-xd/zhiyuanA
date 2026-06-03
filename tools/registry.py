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


def get_tools_schema() -> ToolsSchema:
    return ToolsSchema(
        standard_tools=[light_schema, launch_task_schema]
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
    llm.register_function("launch_aimmaster_task", _handle_launch_task)
    log.info("已注册工具: set_status_light, launch_aimmaster_task")