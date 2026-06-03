"""
任务引擎相关的原子工具（来自你上传的 voice_task.py 的三步序列）。

把原脚本里阻塞式的 requests 调用，改写成异步原子 Tool：
  - migrate_to_auto():   切系统到 Auto 模式  (SystemService/MigrateSystemStateSync)
  - set_current_task():  设置当前任务        (TaskEngineService/SetCurrentTask)
  - launch_task():       启动任务            (TaskEngineService/LaunchTask)

这三个是「原子操作」，单独看都是一个 RPC。把它们按业务顺序编排起来
（Auto → SetCurrent → Launch），就构成了 skills/launch_task.py 里的「技能」。
"""

import logging

from a2_agent.config import SYSTEM_SERVICE_BASE, TASK_ENGINE_BASE
from a2_agent.services.a2_client import a2_client

log = logging.getLogger("a2.tool.task")


async def migrate_to_auto() -> bool:
    """切系统到 Auto 模式。原脚本判断 body 里含 is_success:true。"""
    res = await a2_client.post_rpc(
        f"{SYSTEM_SERVICE_BASE}/MigrateSystemStateSync", {"state": "Auto"}
    )
    ok = res["ok"] and '"is_success": true' in res["text"]
    log.info("migrate_to_auto -> %s", ok)
    return ok


async def set_current_task(task_id: str) -> bool:
    res = await a2_client.post_rpc(
        f"{TASK_ENGINE_BASE}/SetCurrentTask", {"task_id": task_id}
    )
    ok = res["ok"] and '"is_success": true' in res["text"]
    log.info("set_current_task(%s) -> %s", task_id, ok)
    return ok


async def launch_task(task_id: str) -> bool:
    """启动任务。原脚本判断 body 里含 ReturnType_SUCCEED。"""
    res = await a2_client.post_rpc(
        f"{TASK_ENGINE_BASE}/LaunchTask", {"task_id": task_id}
    )
    ok = "ReturnType_SUCCEED" in res["text"]
    log.info("launch_task(%s) -> %s", task_id, ok)
    return ok
