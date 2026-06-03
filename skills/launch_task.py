"""
技能：启动 AimMaster 任务（launch_task_skill）

这是把你上传的 voice_task.py 重构成的「技能」。

原脚本的本质流程（关键词命中后）：
    切 Auto 模式  →  设置当前任务  →  启动任务
任何一步失败就中止并报错。

按技术选型文档定义：
    Tool  = 一次原子操作（这里是 migrate_to_auto / set_current_task / launch_task）
    Skill = 多个 Tool + 业务逻辑 + 状态的组合
所以这里用 LangGraph 的 StateGraph 把三步串成一个带分支（失败即中止）的状态机，
作为一个「技能」对外暴露。

LLM 侧只看到一个工具 launch_aimmaster_task(task_id 或 keyword)，
内部由本技能负责多步执行，执行结果回注对话上下文（由 pipeline 完成）。
"""

import logging
from typing import Optional, TypedDict

from langgraph.graph import END, StateGraph

from config import TASK_NAMES
from tools.task_engine import launch_task, migrate_to_auto, set_current_task
from tools.light import set_status_light

log = logging.getLogger("a2.skill.launch_task")


# ── 技能的运行状态 ──────────────────────────────────────────────────────────
class LaunchState(TypedDict, total=False):
    task_id: str          # 目标任务
    step: str             # 当前步骤名（调试用）
    ok: bool              # 是否成功
    message: str          # 给 LLM/用户的人类可读说明


# ── 各节点：每个节点调用一个原子 Tool，并据结果决定走向 ─────────────────────
async def node_auto(state: LaunchState) -> LaunchState:
    state["step"] = "migrate_to_auto"
    if await migrate_to_auto():
        return {**state, "ok": True}
    return {**state, "ok": False, "message": "切换 Auto 模式失败，已放弃启动任务。"}


async def node_set_current(state: LaunchState) -> LaunchState:
    state["step"] = "set_current_task"
    if await set_current_task(state["task_id"]):
        return {**state, "ok": True}
    return {**state, "ok": False, "message": "设置当前任务失败，已放弃启动任务。"}


async def node_launch(state: LaunchState) -> LaunchState:
    state["step"] = "launch_task"
    if await launch_task(state["task_id"]):
        name = TASK_NAMES.get(state["task_id"], state["task_id"])
        # 启动成功时顺手把灯带切到「工作中」，给现场人员一个可视反馈
        await set_status_light(preset="working")
        return {**state, "ok": True, "message": f"{name} 已成功启动。"}
    return {
        **state,
        "ok": False,
        "message": "任务启动失败，请确认系统已切换到 Auto 模式。",
    }


# 失败即中止的分支判断
def _gate(state: LaunchState) -> str:
    return "continue" if state.get("ok") else "abort"


# ── 编译 StateGraph（模块加载时编译一次，复用）─────────────────────────────
def _build_graph():
    g = StateGraph(LaunchState)
    g.add_node("auto", node_auto)
    g.add_node("set_current", node_set_current)
    g.add_node("launch", node_launch)

    g.set_entry_point("auto")
    g.add_conditional_edges("auto", _gate, {"continue": "set_current", "abort": END})
    g.add_conditional_edges("set_current", _gate, {"continue": "launch", "abort": END})
    g.add_edge("launch", END)
    return g.compile()


_GRAPH = _build_graph()


def resolve_task_id(task_id: Optional[str]) -> Optional[str]:
    """直接返回 task_id（全权由 LLM 判断）。"""
    return task_id


async def run_launch_task_skill(task_id: Optional[str] = None) -> dict:
    """
    技能入口。只接受 task_id（全权由 LLM 判断任务）。

    返回 {"ok": bool, "message": str, "task_id": str|None}，
    message 会被 pipeline 注入对话上下文，供 LLM 生成回复并 TTS 播出。
    """
    tid = resolve_task_id(task_id)
    if not tid:
        return {
            "ok": False,
            "task_id": None,
            "message": "没有传入 task_id，无法启动任务。",
        }

    log.info("▶ 执行 launch_task_skill, task_id=%s", tid)
    final: LaunchState = await _GRAPH.ainvoke({"task_id": tid, "ok": False})
    return {
        "ok": bool(final.get("ok")),
        "task_id": tid,
        "message": final.get("message", ""),
    }
