"""
ROS2 音频输入处理器（修复版：唤醒后才订阅音频 topic）。

修复要点：
  1. 启动时只订阅唤醒 topic
  2. 唤醒后才创建音频 topic 订阅（对齐能工作的内联脚本模式）
  3. 加了 _handle_ros_audio 诊断日志
  4. IDLE_TIMEOUT 改成 600 秒
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
from services.tts import play_tts

log = logging.getLogger("a2.ros_audio")

IDLE_TIMEOUT = 600  # 秒，空闲多久后重新等待唤醒（调大）


class ROS2AudioInputProcessor(FrameProcessor):
    """订阅 A2 麦克风 Topic，唤醒后输出 TranscriptionFrame。"""

    def __init__(self):
        super().__init__()
        self._asr = build_asr()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._ros_thread: Optional[threading.Thread] = None
        self._running = False

        # 音频缓冲区
        self._audio_buffer = bytearray()
        self._is_recording = False

        # 唤醒状态
        self._wakeup_event = threading.Event()
        self._audio_active = False
        self._last_activity = time.time()

        # ROS2 节点引用（延迟创建）
        self._ros_node = None

        # 诊断计数
        self._diag_count = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, StartFrame):
            self._loop = asyncio.get_running_loop()
            self._start_ros()
            log.info("等待唤醒...")
            await self._loop.run_in_executor(None, self._wakeup_event.wait)
            self._wakeup_event.clear()
        elif isinstance(frame, EndFrame):
            self._running = False
        await self.push_frame(frame, direction)

    def _start_ros(self):
        self._running = True
        self._ros_thread = threading.Thread(target=self._ros_spin, daemon=True)
        self._ros_thread.start()

    def _ros_spin(self):
        try:
            import rclpy
            from rclpy.node import Node
            from rclpy.qos import (
                QoSProfile,
                QoSHistoryPolicy,
                QoSReliabilityPolicy,
                QoSDurabilityPolicy,
            )
        except ImportError:
            log.error("未安装 rclpy，无法订阅音频/唤醒。请在 A2 机器人环境运行。")
            return

        if not rclpy.ok():
            rclpy.init()

        parent = self

        class _DualNode(Node):
            def __init__(self):
                super().__init__("a2_agent_dual_node")

                # ====== 关键修复：启动时只订阅唤醒 topic ======
                qos_wakeup = QoSProfile(
                    reliability=QoSReliabilityPolicy.BEST_EFFORT,
                    durability=QoSDurabilityPolicy.VOLATILE,
                    depth=10,
                )
                from ros2_plugin_proto.msg import RosMsgWrapper

                self._audio_sub = None  # 音频订阅：唤醒后才创建

                self.create_subscription(
                    RosMsgWrapper,
                    "/agent/wakeup/pb_3Aaimdk_2Eprotocol_2EWakeUpResult",
                    self._on_wakeup,
                    qos_wakeup,
                )
                log.info("ROS2 节点启动，已订阅唤醒 topic，等待唤醒...")

            def _on_wakeup(self, msg):
                log.info("🔔 _on_wakeup 被调用！")
                parent._record_activity()

                # ====== 关键修复：唤醒后才订阅音频 topic ======
                if self._audio_sub is None:
                    log.info("🔔 首次唤醒，创建音频 topic 订阅...")
                    qos_audio = QoSProfile(
                        history=QoSHistoryPolicy.KEEP_LAST,
                        depth=10,
                        reliability=QoSReliabilityPolicy.BEST_EFFORT,
                        durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
                    )
                    from ros2_plugin_proto.msg import RosMsgWrapper
                    self._audio_sub = self.create_subscription(
                        RosMsgWrapper,
                        config.AUDIO_TOPIC,
                        self._on_audio,
                        qos_audio,
                    )
                    log.info("🔔 音频 topic 订阅已创建: %s", config.AUDIO_TOPIC)
                else:
                    log.info("🔔 再次唤醒，音频订阅已存在，重新激活")

                # 调度 TTS 回复
                if parent._loop and parent._loop.is_running():
                    log.info("🔔 用 run_coroutine_threadsafe 调度 TTS")
                    asyncio.run_coroutine_threadsafe(
                        parent._do_wakeup_reply(), parent._loop
                    )

            def _on_audio(self, msg):
                parent._handle_ros_audio(msg)

        node = _DualNode()
        parent._ros_node = node
        try:
            rclpy.spin(node)
        except Exception as e:
            log.error("ROS2 spin 异常: %s", e)
        finally:
            node.destroy_node()

    async def _do_wakeup_reply(self):
        """在主事件循环中执行唤醒回复"""
        log.info("✅✅✅ _do_wakeup_reply 开始执行！")
        self._audio_active = True
        try:
            log.info("✅ 唤醒成功，TTS 回复『我在呢』")
            await play_tts("我在呢", interrupt=True)
        except Exception as e:
            log.error("TTS 异常: %s", e)
        finally:
            log.info("✅✅✅ TTS 完成，set wakeup_event")
            self._wakeup_event.set()
            log.info("🎤 进入语音交互模式")

    def _handle_ros_audio(self, msg):
        """
        解析 A2 音频消息。
        唤醒前丢弃音频帧。空闲超时后自动回等待唤醒状态。
        """
        # 诊断日志
        self._diag_count += 1
        if self._diag_count <= 10 or self._diag_count % 100 == 0:
            log.info(
                "📡 _handle_ros_audio #%d, _audio_active=%s",
                self._diag_count,
                self._audio_active,
            )

        self._idle_check()
        if not self._audio_active:
            if self._diag_count <= 5:
                log.info("🔇 音频帧被丢弃（_audio_active=False）")
            return

        try:
            serialization_type = getattr(msg, "serialization_type", "unknown")
            if hasattr(msg, "serialization_type") and msg.serialization_type != "pb":
                log.debug("跳过非 pb 消息: %s", serialization_type)
                return

            from aimdk.protocol_pb2 import ProcessedAudioOutput

            raw_data = b"".join(msg.data)
            result = ProcessedAudioOutput()
            result.ParseFromString(raw_data)

            stream_id = result.stream_id
            vad_state = result.vad_state
            audio_data = bytes(result.audio_data)

            if self._diag_count <= 5:
                log.info(
                    "🔍 stream_id=%d, vad=%d, audio_len=%d",
                    stream_id,
                    vad_state,
                    len(audio_data),
                )

            if stream_id != 1:
                return

        except Exception as e:
            log.error("解析音频消息失败: %s", e)
            import traceback
            log.error("堆栈: %s", traceback.format_exc())
            return

        # VAD 状态机
        self._record_activity()
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
            log.info(
                "🎤 语音结束，buffer 大小=%d bytes (%.2fs)",
                total_size,
                total_size / 32000,
            )

            if total_size < 6400:
                log.info("⏩ 语音太短（%d bytes），跳过", total_size)
                self._audio_buffer.clear()
                return

            self._submit(self._emit(UserStoppedSpeakingFrame()))
            audio = bytes(self._audio_buffer)
            self._audio_buffer.clear()
            log.info("🎤 送 ASR 转写，音频大小=%d bytes", len(audio))
            self._submit(self._transcribe_and_push(audio))

        elif vad_state == 0:  # 静默
            if self._is_recording:
                self._is_recording = False

    def _submit(self, coro):
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, self._loop)

    def _idle_check(self):
        idle = time.time() - self._last_activity
        if idle > IDLE_TIMEOUT:
            if self._audio_active:
                log.info("😴 空闲超时，重新进入等待唤醒状态 (idle=%.1fs)", idle)
                self._audio_active = False
                self._wakeup_event.clear()

    def _record_activity(self):
        self._last_activity = time.time()

    async def _emit(self, frame: Frame):
        await self.push_frame(frame, FrameDirection.DOWNSTREAM)

    async def _transcribe_and_push(self, pcm: bytes):
        if len(pcm) < 6400:
            return
        log.info("🎤 [1/4] ROS2 Topic 收到音频 -> 送 ASR 转写")
        text, conf = await self._asr.transcribe(pcm)
        if not text:
            log.info("⏭️ ASR 未识别到文字")
            return
        log.info("📝 [2/4] ASR 识别结果 -> 「%s」", text)
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        await self.push_frame(
            TranscriptionFrame(text=text, user_id="user", timestamp=ts),
            FrameDirection.DOWNSTREAM,
        )
