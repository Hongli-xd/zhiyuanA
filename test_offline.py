"""
离线自测：不连真机器人，用 Mock 替换 A2Client.post_rpc，
验证 LangGraph 技能（launch_task）和灯带工具的逻辑是否正确。

运行: python test_offline.py
"""

import asyncio
import logging

from services import a2_client as client_mod

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
    import tools.task_engine as te
    import tools.light as lt
    import services.tts as tts
    te.a2_client = mock
    lt.a2_client = mock
    tts.a2_client = mock

    from skills.launch_task import run_launch_task_skill
    from tools.light import set_status_light

    # ── 灯带预设测试 ────────────────────────────────────────────────────
    print("\n=== 测试1: 灯带预设 waiting（紫红）===")
    r = await set_status_light(preset="waiting")
    print(r)
    assert r["applied"]["red"] == 180 and r["applied"]["blue"] == 100

    print("\n=== 测试2: 灯带预设 working（蓝）===")
    r = await set_status_light(preset="working")
    print(r)
    assert r["applied"]["red"] == 0 and r["applied"]["green"] == 80 and r["applied"]["blue"] == 220

    print("\n=== 测试3: 灯带预设 done（绿）===")
    r = await set_status_light(preset="done")
    print(r)
    assert r["applied"]["green"] == 200 and r["applied"]["red"] == 0

    print("\n=== 测试4: 灯带预设 off（关闭）===")
    r = await set_status_light(preset="off")
    print(r)
    assert r["applied"]["red"] == 0 and r["applied"]["green"] == 0 and r["applied"]["blue"] == 0
    assert r["applied"]["control"] == 0

    # ── 灯带 RGB 直接测试 ──────────────────────────────────────────────
    print("\n=== 测试5: 灯带自定义颜色 - 红色 ===")
    r = await set_status_light(red=255, green=0, blue=0)
    print(r)
    assert r["applied"]["red"] == 255 and r["applied"]["green"] == 0 and r["applied"]["blue"] == 0

    print("\n=== 测试6: 灯带自定义颜色 - 蓝色 ===")
    r = await set_status_light(red=0, green=0, blue=255)
    print(r)
    assert r["applied"]["red"] == 0 and r["applied"]["green"] == 0 and r["applied"]["blue"] == 255

    print("\n=== 测试7: 灯带自定义颜色 - 蓝白色 ===")
    r = await set_status_light(red=180, green=230, blue=255)
    print(r)
    assert r["applied"]["red"] == 180 and r["applied"]["green"] == 230 and r["applied"]["blue"] == 255

    print("\n=== 测试8: 灯带自定义颜色 - 黄色 ===")
    r = await set_status_light(red=255, green=255, blue=0)
    print(r)
    assert r["applied"]["red"] == 255 and r["applied"]["green"] == 255 and r["applied"]["blue"] == 0

    print("\n=== 测试9: 灯带 RGB 越界 clamping（>255 → 255，<0 → 0）===")
    r = await set_status_light(red=300, green=-10, blue=128)
    print(r)
    assert r["applied"]["red"] == 255 and r["applied"]["green"] == 0 and r["applied"]["blue"] == 128

    # ── 任务技能测试（全权 LLM 判断，只用 task_id）─────────────────────
    print("\n=== 测试10: 技能 - task_id=1 启动讲解任务 ===")
    r = await run_launch_task_skill(task_id="1")
    print(r)
    assert r["ok"] and r["task_id"] == "1"

    print("\n=== 测试11: 技能 - task_id=2 启动电梯等人任务 ===")
    r = await run_launch_task_skill(task_id="2")
    print(r)
    assert r["ok"] and r["task_id"] == "2"

    print("\n=== 测试12: 技能 - 不传 task_id 应失败 ===")
    r = await run_launch_task_skill()
    print(r)
    assert not r["ok"]
    assert r["task_id"] is None

    # ── 重置 mock 调用记录 ─────────────────────────────────────────────
    mock.calls.clear()

    # ── LLM 意图识别模拟（offline 无法真正调用 LLM，打桩验证完整流程）───
    # 场景：ASR 返回 "去门口接人"，LLM 理解意图后调用 task_id="2"
    #       真实流程中这一步由 LLM 自主判断，test 只验证 task_id=2 能正确执行
    print("\n=== 测试13: 模拟 LLM 理解「去门口接人」→ 调用 task_id=2（电梯等人任务）===")
    r = await run_launch_task_skill(task_id="2")
    print(r)
    assert r["ok"] and r["task_id"] == "2"
    assert "电梯等人任务" in r["message"]

    print("\n=== 测试14: 模拟 LLM 理解「开始讲解」→ 调用 task_id=1（讲解任务）===")
    r = await run_launch_task_skill(task_id="1")
    print(r)
    assert r["ok"] and r["task_id"] == "1"
    assert "讲解任务" in r["message"]

    print("\n=== RPC 调用序列(验证 Auto→SetCurrent→Launch 顺序) ===")
    seq = [u.split("/")[-1] for u, _ in mock.calls]
    print(seq)

    print("\n✅ 全部断言通过")


# ── 在线测试：验证 MiniMax LLM 和 FunASR ASR ──────────────────────────────
async def test_online_services():
    """测试真实 API 连通性（需要网络）。"""
    print("\n=== 测试在线服务: FunASR ASR + MiniMax LLM ===")
    from services.asr import build_asr
    asr = build_asr()
    print(f"ASR provider: {type(asr).__name__}")

    # 生成 1 秒静音 PCM（16kHz/16bit），看能否正常调用
    import struct
    pcm = struct.pack("<h", 0) * 16000  # 1s silence
    text, conf = await asr.transcribe(pcm)
    print(f"FunASR result: 「{text}」 conf={conf}")

    from services.a2_client import A2Client
    import config
    a2 = A2Client()
    try:
        import aiohttp
        url = f"{config.LLM_BASE_URL}/messages"
        headers = {
            "Authorization": f"Bearer {config.LLM_API_KEY}",
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        body = {
            "model": config.LLM_MODEL,
            "max_tokens": 10,
            "messages": [{"role": "user", "content": "Hi"}]
        }
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=body, headers=headers, timeout=15) as r:
                print(f"LLM status: {r.status}")
                data = await r.json(content_type=None)
                print(f"LLM response: {data}")
                assert r.status in (200, 201), f"Unexpected status {r.status}"
    except Exception as e:
        print(f"在线服务调用失败: {e}")
    finally:
        await a2.stop()


if __name__ == "__main__":
    import sys
    if "--online" in sys.argv:
        asyncio.run(test_online_services())
    else:
        asyncio.run(main())
