"""
离线自测：不连真机器人，用 Mock 替换 A2Client.post_rpc，
验证 LangGraph 技能（launch_task）和灯带工具的逻辑是否正确。

运行: python -m a2_agent.test_offline
"""

import asyncio
import logging

from a2_agent.services import a2_client as client_mod

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


# ── Mock：拦截所有 RPC，返回成功响应 ──────────────────────────────────────
class MockClient:
    def __init__(self):
        self.calls = []

    async def start(self): ...
    async def stop(self): ...

    async def post_rpc(self, url: str, payload: dict) -> dict:
        self.calls.append((url, payload))
        # 按 url 末段返回符合各工具判定逻辑的假响应
        if url.endswith("MigrateSystemStateSync"):
            text = '{"is_success": true}'
        elif url.endswith("SetCurrentTask"):
            text = '{"is_success": true}'
        elif url.endswith("LaunchTask"):
            text = '{"result": "ReturnType_SUCCEED"}'
        elif url.endswith("SetRgbLightCommand"):
            text = '{"state":"ok"}'
        else:
            text = "{}"
        return {"ok": True, "status": 200, "text": text, "json": None}


async def main():
    # 打补丁
    mock = MockClient()
    client_mod.a2_client = mock
    # 让已 import 该单例的模块也用上 mock
    import a2_agent.tools.task_engine as te
    import a2_agent.tools.light as lt
    import a2_agent.services.tts as tts
    te.a2_client = mock
    lt.a2_client = mock
    tts.a2_client = mock

    from a2_agent.skills.launch_task import run_launch_task_skill
    from a2_agent.tools.light import set_status_light

    print("\n=== 测试1: 灯带等待预设 ===")
    r = await set_status_light(preset="waiting")
    print(r)
    assert r["applied"]["red"] == 180 and r["applied"]["blue"] == 100

    print("\n=== 测试2: 技能 - 用关键词「开始讲解」启动任务(应映射到 task_id=1) ===")
    r = await run_launch_task_skill(keyword="开始讲解")
    print(r)
    assert r["ok"] and r["task_id"] == "1"

    print("\n=== 测试3: 技能 - 用关键词「电梯门」启动任务(应映射到 task_id=2) ===")
    r = await run_launch_task_skill(keyword="电梯门")
    print(r)
    assert r["ok"] and r["task_id"] == "2"

    print("\n=== 测试4: 技能 - 直接给 task_id ===")
    r = await run_launch_task_skill(task_id="1")
    print(r)
    assert r["ok"]

    print("\n=== 测试5: 技能 - 未匹配关键词应失败 ===")
    r = await run_launch_task_skill(keyword="不存在的词")
    print(r)
    assert not r["ok"]

    print("\n=== RPC 调用序列(验证 Auto→SetCurrent→Launch 顺序) ===")
    seq = [u.split("/")[-1] for u, _ in mock.calls]
    print(seq)

    print("\n✅ 全部断言通过")


if __name__ == "__main__":
    asyncio.run(main())
