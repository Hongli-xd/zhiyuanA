"""
ROS2 音频输入处理器（对齐 voice.py 逻辑）。

A2 麦克风音频通过 ROS2 Topic /agent/process_audio_output 输出 RosMsgWrapper
（内嵌序列化后的 ProcessedAudioOutput protobuf）。

VAD 状态：
  0 = 静默
  1 = 语音开始
  2 = 语音中
  3 = 语音结束（触发 ASR 识别）

音频累积逻辑（voice.py 方式）：
  vad=1 → 清空 buffer，开始累积
  vad=2 → 持续累积
  vad=3 → 累积最后一个 audio_data 片段，一起送 ASR
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
        self._running = False

        # 音频缓冲区：对齐 voice.py
        self._audio_buffer = bytearray()
        self._is_recording = False

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
                    reliability=QoSReliabilityPolicy.BEST_EFFORT,
                    durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
                )
                # 对齐 voice.py：完整 topic 名（含 protobuf 后缀）
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
        解析 A2 音频消息，对齐 voice.py 的 ProcessedAudioOutput protobuf 逻辑。

        VAD 状态：
          1 = 语音开始
          2 = 语音中
          3 = 语音结束（触发识别）
          0 = 静默
        """
        try:
            # 检查 serialization_type（必须是 "pb"）
            if hasattr(msg, "serialization_type") and msg.serialization_type != "pb":
                return

            # 解析 protobuf
            from ros2_plugin_proto.msg import RosMsgWrapper
            from aimdk.protocol_pb2 import ProcessedAudioOutput

            result = ProcessedAudioOutput()
            result.ParseFromString(b"".join(msg.data))

            stream_id = result.stream_id
            vad_state = result.vad_state
            audio_data = bytes(result.audio_data)

            # 只处理板载麦克风（stream_id=1）
            if stream_id != 1:
                return

        except Exception as e:
            log.error("解析音频消息失败: %s", e)
            return

        # ── VAD 状态机：对齐 voice.py ────────────────────────────────
        if vad_state == 1:  # 语音开始
            self._audio_buffer.clear()
            self._is_recording = True
            if audio_data:
                self._audio_buffer.extend(audio_data)
            self._submit(self._emit(UserStartedSpeakingFrame()))
            log.info("🎤 检测到语音开始")

        elif vad_state == 2:  # 语音中
            if self._is_recording and audio_data:
                self._audio_buffer.extend(audio_data)

        elif vad_state == 3:  # 语音结束
            if self._is_recording and audio_data:
                self._audio_buffer.extend(audio_data)
            self._is_recording = False

            total_size = len(self._audio_buffer)
            if total_size < 6400:  # < 0.2s，太短跳过
                log.info("⏩ 语音太短，跳过")
                self._audio_buffer.clear()
                return

            self._submit(self._emit(UserStoppedSpeakingFrame()))
            audio = bytes(self._audio_buffer)
            self._audio_buffer.clear()
            self._submit(self._transcribe_and_push(audio))

        elif vad_state == 0:  # 静默
            if self._is_recording:
                self._is_recording = False

    # ── 把协程安全地丢回 pipeline 的事件循环 ──────────────────────────────
    def _submit(self, coro):
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, self._loop)

    async def _emit(self, frame: Frame):
        await self.push_frame(frame, FrameDirection.DOWNSTREAM)

    async def _transcribe_and_push(self, pcm: bytes):
        if len(pcm) < 6400:  # < ~0.2s，忽略
            return
        log.info("🎤 [1/4] ROS2 Topic 收到音频 -> 送 ASR 转写")
        text, conf = await self._asr.transcribe(pcm)
        if not text:
            return
        log.info("📝 [2/4] ASR 识别结果 -> 「%s」", text)
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        await self.push_frame(
            TranscriptionFrame(text=text, user_id="user", timestamp=ts),
            FrameDirection.DOWNSTREAM,
        )