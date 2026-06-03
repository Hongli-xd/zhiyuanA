"""
工具注册层。

把「工具」和「技能」统一注册给 Pipecat 的 LLM service。
采用技术选型文档推荐的 FunctionSchema 方式（精确控制：enum 约束、必填字段）。

注册的能力：
  1) set_status_light    —— 工具：灯带/等待状态指示（你的需求 3）
  2) launch_aimmaster_task —— 技能：启动 AimMaster 任务（重构自 voice_task.py）
  3) wait_for_person     —— 工具：进入等待状态（亮灯 + 提示），演示「等待控制」

每个 handler 把 result 通过 params.result_callback 回传，
Pipecat 会自动把结果作为 tool_result 注入对话上下文，触发 LLM 生成回复。
"""

import logging

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema

from a2_agent.config import KEYWORD_TASK_MAP, TASK_NAMES
from a2_agent.services.a2_client import confidence_gate
from a2_agent.skills.launch_task import run_launch_task_skill
from a2_agent.tools.light import set_status_light

log = logging.getLogger("a2.registry")


# ── Schema 定义 ─────────────────────────────────────────────────────────────
_task_id_enum = list(TASK_NAMES.keys())
_keyword_enum = list(KEYWORD_TASK_MAP.keys())

light_schema = FunctionSchema(
    name="set_status_light",
    description="设置A2机器人灯带颜色/效果，用于向现场人员指示当前状态（等待、工作、完成等）。",
    properties={
        "preset": {
            "type": "string",
            "enum": ["waiting", "working", "done", "off"],
            "description": "状态预设：waiting=等待(紫红), working=工作(蓝), done=完成(绿), off=关闭",
        },
        "red": {"type": "integer", "description": "可选，自定义红 0-255"},
        "green": {"type": "integer", "description": "可选，自定义绿 0-255"},
        "blue": {"type": "integer", "description": "可选，自定义蓝 0-255"},
    },
    required=[],
)

wait_schema = FunctionSchema(
    name="wait_for_person",
    description="让机器人进入等待来人的状态：亮起等待灯并保持。用于「等人」「在电梯口等」等场景。",
    properties={
        "reason": {
            "type": "string",
            "description": "等待原因的简短描述，如「在电梯口等人」",
        },
    },
    required=[],
)

launch_task_schema = FunctionSchema(
    name="launch_aimmaster_task",
    description=(
        "启动一个在 AimMaster 上预先创建好的任务（如讲解、电梯等人）。"
        "内部会自动完成：切换Auto模式→设置当前任务→启动任务三步。"
        "可以直接给 task_id，或给一个关键词由系统映射到任务。"
    ),
    properties={
        "task_id": {
            "type": "string",
            "enum": _task_id_enum,
            "description": f"任务ID。{TASK_NAMES}",
        },
        "keyword": {
            "type": "string",
            "enum": _keyword_enum,
            "description": "关键词，系统按映射表解析为任务ID（task_id 缺省时使用）",
        },
        "confidence": {
            "type": "number",
            "description": "本次指令识别置信度0-1，可选；低于阈值会被安全门拦截",
        },
    },
    required=[],
)


def get_tools_schema() -> ToolsSchema:
    return ToolsSchema(
        standard_tools=[light_schema, wait_schema, launch_task_schema]
    )


# ── Handlers ────────────────────────────────────────────────────────────────
async def _handle_set_light(params):
    a = params.arguments
    res = await set_status_light(
        preset=a.get("preset"),
        red=a.get("red"),
        green=a.get("green"),
        blue=a.get("blue"),
    )
    await params.result_callback(res)


async def _handle_wait(params):
    reason = params.arguments.get("reason", "等待中")
    await set_status_light(preset="waiting")
    await params.result_callback(
        {"status": "已进入等待状态，灯带已亮起", "reason": reason}
    )


async def _handle_launch_task(params):
    a = params.arguments
    # 安全兜底：任务启动会触发机器人物理动作（导航/运动），做置信度检查
    ok, msg = confidence_gate.check(a.get("confidence"), "launch_aimmaster_task")
    if not ok:
        await params.result_callback({"ok": False, "message": f"安全拦截：{msg}"})
        return
    res = await run_launch_task_skill(
        task_id=a.get("task_id"), keyword=a.get("keyword")
    )
    await params.result_callback(res)


def register_all(llm) -> None:
    """把所有 handler 注册到 Pipecat 的 LLM service。"""
    llm.register_function("set_status_light", _handle_set_light)
    llm.register_function("wait_for_person", _handle_wait)
    llm.register_function("launch_aimmaster_task", _handle_launch_task)
    log.info("已注册工具: set_status_light, wait_for_person, launch_aimmaster_task")
