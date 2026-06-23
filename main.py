"""
A2 语音 Agent 主入口（在机器人上运行）。

启动顺序：
  1. 初始化日志 / A2Client
  2. 构建 ROS2 音频输入 → pipeline → A2 TTS 输出（唤醒检测在 pipeline 内完成）
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
import os
import sys

from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask

from pipeline.build import build_pipeline
from pipeline.ros_audio_input import ROS2AudioInputProcessor
from pipeline.terminal_input import TerminalTextInput
from services.a2_client import a2_client

# 是否启用终端文本输入（默认启用，可通过环境变量关闭）
ENABLE_TERMINAL_INPUT = os.getenv("ENABLE_TERMINAL_INPUT", "1") == "1"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("a2.main")


async def run():
    await a2_client.start()

    terminal_input = TerminalTextInput() if ENABLE_TERMINAL_INPUT else None
    audio_input = ROS2AudioInputProcessor()
    pipeline, _, _ = build_pipeline(audio_input, terminal_input=terminal_input)

    # 在主事件循环中启动文件监控
    if terminal_input is not None:
        terminal_input.start_watch(asyncio.get_running_loop())

    task = PipelineTask(
        pipeline,
        cancel_on_idle_timeout=False,
        enable_turn_tracking=False,
    )
    runner = PipelineRunner()

    log.info("A2 语音 Agent 启动，等待唤醒… (Ctrl+C 退出)")
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
