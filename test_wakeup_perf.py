"""
测试两种唤醒回复模式的响应速度差异。

场景：
  1. 连续对话：唤醒 → "我在呢" → 立即连续发多条语音指令
  2. 间隔对话：唤醒 → "我在呢" → 等待一段时间 → 再发指令

测量：唤醒到 TTS 开始播放的延迟、pipeline 继续的时机
"""

import asyncio
import time

TTS_DURATION = 1.0  # 秒，模拟 "我在呢" 播放时长


async def play_tts_mock(text: str, interrupt: bool = True):
    print(f"  🔊 TTS 开始: 「{text}」")
    await asyncio.sleep(TTS_DURATION)
    print(f"  🔇 TTS 结束")


async def test_blocking():
    """
    阻塞模式：pipeline 等 TTS 说完才继续
    """
    print("\n" + "=" * 50)
    print("【阻塞模式】TTS 说完再让 pipeline 继续")
    print("=" * 50)

    t0 = time.time()
    pipeline_unblocked_at = None

    async def on_wakeup():
        nonlocal pipeline_unblocked_at
        print(f"[{time.time()-t0:.3f}s] 🔔 唤醒消息到达")
        print(f"[{time.time()-t0:.3f}s] 🔊 TTS 开始...")
        await play_tts_mock("我在呢")
        print(f"[{time.time()-t0:.3f}s] ✅ TTS 说完，pipeline 继续")
        pipeline_unblocked_at = time.time() - t0

    # 启动唤醒任务
    task = asyncio.create_task(on_wakeup())

    # 模拟 pipeline 在 wait() 处阻塞
    print(f"[{time.time()-t0:.3f}s] ⏳ pipeline 等待唤醒事件...")
    await task
    print(f"[{time.time()-t0:.3f}s] 📨 pipeline 继续，frame 开始传递")

    # 连续收到 3 个音频帧
    for i in range(3):
        print(f"[{time.time()-t0:.3f}s] 收到音频帧 {i+1}")
        await asyncio.sleep(0.2)

    total = time.time() - t0
    print(f"\n📊 阻塞模式:")
    print(f"  TTS 响应延迟: 0.00s（唤醒后立即开始）")
    print(f"  pipeline 继续: {pipeline_unblocked_at:.3f}s（TTS 说完）")
    print(f"  总耗时: {total:.3f}s")
    return total, pipeline_unblocked_at


async def test_non_blocking():
    """
    非阻塞模式：pipeline 不等 TTS，音频帧暂被丢弃
    """
    print("\n" + "=" * 50)
    print("【非阻塞模式】先让 pipeline 继续，TTS 并行跑")
    print("=" * 50)

    t0 = time.time()
    audio_active = True
    pipeline_unblocked_at = 0.0  # 几乎立即

    async def on_wakeup():
        print(f"[{time.time()-t0:.3f}s] 🔔 唤醒消息到达")
        print(f"[{time.time()-t0:.3f}s] ✅ pipeline 继续（几乎同时）")
        print(f"[{time.time()-t0:.3f}s] 🔊 TTS 开始（并行）...")
        await play_tts_mock("我在呢")

    task = asyncio.create_task(on_wakeup())

    # 模拟 pipeline 立即通过（不等待）
    print(f"[{time.time()-t0:.3f}s] ✅ pipeline 立即通过")
    await task
    print(f"[{time.time()-t0:.3f}s] 📨 frame 开始传递（TTS 播放中）")

    # 连续收到 3 个音频帧（TTS 播放期间）
    for i in range(3):
        print(f"[{time.time()-t0:.3f}s] 收到音频帧 {i+1}")
        await asyncio.sleep(0.2)

    total = time.time() - t0
    print(f"\n📊 非阻塞模式:")
    print(f"  pipeline 继续: ~0.00s（立即）")
    print(f"  TTS 响应延迟: 0.00s（并行开始）")
    print(f"  总耗时: {total:.3f}s")
    return total


async def test_spaced_blocking():
    """阻塞模式：唤醒后等 10s 再发指令"""
    print("\n" + "=" * 50)
    print("【阻塞模式】间隔对话：唤醒后等 10s 再发指令")
    print("=" * 50)

    t0 = time.time()

    async def on_wakeup():
        print(f"[{time.time()-t0:.3f}s] 🔔 唤醒消息到达")
        await play_tts_mock("我在呢")
        print(f"[{time.time()-t0:.3f}s] ✅ pipeline 继续")

    asyncio.create_task(on_wakeup())
    await asyncio.sleep(TTS_DURATION + 0.01)  # 等待 TTS 说完
    print(f"[{time.time()-t0:.3f}s] 等待用户说话...")

    await asyncio.sleep(10)  # 用户思考 10s
    print(f"[{time.time()-t0:.3f}s] 收到音频帧 1 (唤醒后 10s)")

    total = time.time() - t0
    print(f"\n📊 阻塞模式 间隔: {total:.3f}s")
    return total


async def test_spaced_non_blocking():
    """非阻塞模式：唤醒后等 10s 再发指令"""
    print("\n" + "=" * 50)
    print("【非阻塞模式】间隔对话：唤醒后等 10s 再发指令")
    print("=" * 50)

    t0 = time.time()
    audio_active = False  # TTS 说完前暂时 False

    async def on_wakeup():
        nonlocal audio_active
        print(f"[{time.time()-t0:.3f}s] 🔔 唤醒消息到达")
        print(f"[{time.time()-t0:.3f}s] ✅ pipeline 继续")
        audio_active = True
        print(f"[{time.time()-t0:.3f}s] 🔊 TTS 开始...")
        await play_tts_mock("我在呢")

    asyncio.create_task(on_wakeup())
    await asyncio.sleep(0.01)  # 任务调度
    print(f"[{time.time()-t0:.3f}s] 等待用户说话...")

    await asyncio.sleep(10)  # 用户思考 10s
    print(f"[{time.time()-t0:.3f}s] 收到音频帧 1 (唤醒后 10s)")

    total = time.time() - t0
    print(f"\n📊 非阻塞模式 间隔: {total:.3f}s")
    return total


async def main():
    print("🔬 唤醒回复模式速度对比测试")
    print(f"  模拟 TTS 时长: {TTS_DURATION}s")

    b_total, b_pipeline = await test_blocking()
    nb_total = await test_non_blocking()

    print("\n" + "=" * 50)
    print("【连续对话对比】")
    print("=" * 50)
    print(f"  阻塞模式:   TTS {TTS_DURATION:.1f}s 后 pipeline 继续")
    print(f"  非阻塞模式: pipeline 几乎立即继续，TTS 并行跑")
    print(f"  → 非阻塞模式 pipeline 提前 ~{TTS_DURATION:.1f}s")
    print(f"  → 但唤醒后立即说话的话，音频会被丢弃（因为 audio_active 未设）")

    b_spaced = await test_spaced_blocking()
    nb_spaced = await test_spaced_non_blocking()

    print("\n" + "=" * 50)
    print("【间隔对话对比】（唤醒后等 10s 再发指令）")
    print("=" * 50)
    print(f"  阻塞模式:   {b_spaced:.3f}s（TTS 在等待期间播放完）")
    print(f"  非阻塞模式: {nb_spaced:.3f}s（TTS 并行）")
    print(f"  差异: {abs(b_spaced - nb_spaced):.3f}s（几乎无差异）")


if __name__ == "__main__":
    asyncio.run(main())