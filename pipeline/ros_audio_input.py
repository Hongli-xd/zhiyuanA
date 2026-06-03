"""
ROS2 音频输入处理器。

A2 麦克风（已做降噪+回声消除）通过 ROS2 Topic 输出 16kHz/16bit PCM，
并带 VAD 状态（BEGIN / PROCESSING / END）。本处理器：

  - 在后台线程跑 rclpy.spin 订阅音频 Topic；
  - VAD=BEGIN 时发 UserStartedSpeakingFrame（触发打断逻辑）；
  - PROCESSING 期间累积 PCM；
  - VAD=END 时发 UserStoppedSpeakingFrame，并把整段 PCM 交给 ASR，
    转写出文本后注入一个 TranscriptionFrame 推进 pipeline。

这样把 A2 的 ROS2 世界桥接进 Pipecat 的 frame pipeline。

注意：本文件依赖 rclpy 与 A2 的消息类型，只能在机器人上运行。
为便于在无 ROS 环境下测试，提供 MockAudioInput（见 pipeline/build.py）。
"""

import asyncio
import logging
import threading
import time
from typing import Optional

from pipecat.frames.frames import (
    Frame,
    StartFrame,
    EndFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

import config
from services.asr import build_asr

log = logging.getLogger("a2.ros_audio")


class ROS2AudioInputProcessor(FrameProcessor):
    """订阅 A2 麦克风 Topic，输出 TranscriptionFrame 的源处理器。"""

    def __init__(self):
        super().__init__()
        self._asr = build_asr()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._ros_thread: Optional[threading.Thread] = None
        self._pcm_buffer = bytearray()
        self._running = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, StartFrame):
            self._loop = asyncio.get_running_loop()
            self._start_ros()
        elif isinstance(frame, EndFrame):
            self._running = False
        await self.push_frame(frame, direction)

    # ── ROS 线程 ────────────────────────────────────────────────────────────
    def _start_ros(self):
        self._running = True
        self._ros_thread = threading.Thread(target=self._ros_spin, daemon=True)
        self._ros_thread.start()
        log.info("ROS2 音频订阅线程已启动, topic=%s", config.AUDIO_TOPIC)

    def _ros_spin(self):
        try:
            import rclpy
            from rclpy.node import Node
            from rclpy.qos import (
                QoSProfile, QoSHistoryPolicy,
                QoSReliabilityPolicy, QoSDurabilityPolicy,
            )
        except ImportError:
            log.error("未安装 rclpy，无法订阅音频。请在 A2 机器人环境运行。")
            return

        if not rclpy.ok():
            rclpy.init()

        parent = self

        class _AudioNode(Node):
            def __init__(self):
                super().__init__("a2_agent_audio_node")
                qos = QoSProfile(
                    history=QoSHistoryPolicy.KEEP_LAST, depth=10,
                    reliability=QoSReliabilityPolicy.RELIABLE,
                    durability=QoSDurabilityPolicy.VOLATILE,
                )
                # 音频消息类型按 A2 约定（RosMsgWrapper 携带 PCM + VAD 状态）
                from ros2_plugin_proto.msg import RosMsgWrapper
                self.create_subscription(
                    RosMsgWrapper, config.AUDIO_TOPIC, self._on_audio, qos
                )

            def _on_audio(self, msg):
                parent._handle_ros_audio(msg)

        node = _AudioNode()
        try:
            while self._running and rclpy.ok():
                rclpy.spin_once(node, timeout_sec=0.1)
        finally:
            node.destroy_node()

    def _handle_ros_audio(self, msg):
        """
        解析 A2 音频消息。VAD 状态字段名以实际消息为准，这里按
        BEGIN/PROCESSING/END 三态处理（technical doc 描述）。
        """
        vad = getattr(msg, "vad_status", None) or self._extract_vad(msg)
        pcm = b"".join(msg.data) if hasattr(msg, "data") else b""

        if vad == "BEGIN":
            self._pcm_buffer = bytearray()
            self._submit(self._emit(UserStartedSpeakingFrame()))
        elif vad == "PROCESSING":
            self._pcm_buffer.extend(pcm)
        elif vad == "END":
            self._pcm_buffer.extend(pcm)
            self._submit(self._emit(UserStoppedSpeakingFrame()))
            audio = bytes(self._pcm_buffer)
            self._pcm_buffer = bytearray()
            self._submit(self._transcribe_and_push(audio))

    @staticmethod
    def _extract_vad(msg) -> str:
        # 兜底：从 context 字段里找 VAD 状态字符串
        ctx = getattr(msg, "context", []) or []
        for c in ctx:
            if c in ("BEGIN", "PROCESSING", "END"):
                return c
        return "PROCESSING"

    # ── 把协程安全地丢回 pipeline 的事件循环 ──────────────────────────────
    def _submit(self, coro):
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, self._loop)

    async def _emit(self, frame: Frame):
        await self.push_frame(frame, FrameDirection.DOWNSTREAM)

    async def _transcribe_and_push(self, pcm: bytes):
        if len(pcm) < config.AUDIO_SAMPLE_RATE:  # < ~0.5s，忽略
            return
        text, conf = await self._asr.transcribe(pcm)
        if not text:
            return
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        await self.push_frame(
            TranscriptionFrame(text=text, user_id="user", timestamp=ts),
            FrameDirection.DOWNSTREAM,
        )
