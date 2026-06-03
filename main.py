"""
A2 语音 Agent 主入口（在机器人上运行）。

启动顺序：
  1. 初始化日志 / A2Client
  2. 构建 ROS2 音频输入 → pipeline → A2 TTS 输出
  3. 跑 PipelineRunner

运行前确认：
  - 已 source A2 的 ros2_plugin_proto 环境
  - 已安装 a2_aimdk whl（音频/唤醒消息 protobuf 解析需要）
  - config.py 里填好 LLM_API_KEY、ASR 凭据、各 IP/端口
  - 交互运行模式设为 normal / voice_face（only_voice 模式不发 TTS 状态）

用法:
  python -m main
"""

import asyncio
import logging
import sys

from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask

from pipeline.build import build_pipeline
from pipeline.ros_audio_input import ROS2AudioInputProcessor
from services.a2_client import a2_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("a2.main")


async def run():
    await a2_client.start()
    print("\n" + "=" * 50)
    print("🤖 A2 语音 Agent 已启动")
    print("   等待唤醒...（请对我说话）")
    print("=" * 50 + "\n")

    audio_input = ROS2AudioInputProcessor()
    pipeline, _llm = build_pipeline(audio_input)

    task = PipelineTask(pipeline)
    runner = PipelineRunner()

    log.info("A2 语音 Agent 启动，等待语音输入… (Ctrl+C 退出)")
    try:
        await runner.run(task)
    finally:
        await a2_client.stop()


def main():
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log.info("收到退出信号，已停止")


if __name__ == "__main__":
    main()
