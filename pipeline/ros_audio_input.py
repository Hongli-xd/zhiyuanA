"""
ROS2 音频输入处理器（对齐 voice.py 逻辑）。

A2 麦克风音频通过 ROS2 Topic /agent/process_audio_output 输出 RosMsgWrapper
（内嵌序列化后的 ProcessedAudioOutput protobuf）。

启动后先等待唤醒词，唤醒成功 TTS 说"我在呢"后再进入正常音频处理。
唤醒前：音频帧被丢弃，只监听 /agent/wakeup topic。
唤醒后：正常 VAD → ASR → TranscriptionFrame 流程。

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
from services.tts import play_tts

log = logging.getLogger("a2.ros_audio")

IDLE_TIMEOUT = 60  # 秒，空闲多久后重新等待唤醒

# 唤醒回复模式：
#   "blocking":  TTS 说完再让 pipeline 继续（默认）
#   "non-blocking": 先让 pipeline 继续，TTS 和 pipeline 并行跑
WAKEUP_REPLY_MODE = "blocking"


class ROS2AudioInputProcessor(FrameProcessor):
    """订阅 A2 麦克风 Topic，唤醒后输出 TranscriptionFrame。"""

    def __init__(self):
        super().__init__()
        self._asr = build_asr()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._ros_thread: Optional[threading.Thread] = None
        self._running = False

        # 音频缓冲区：对齐 voice.py
        self._audio_buffer = bytearray()
        self._is_recording = False

        # 唤醒状态：ROS线程设此事件 → 主循环等待 → 调TTS → 音频处理激活
        self._wakeup_event = threading.Event()
        self._audio_active = False  # 唤醒+ TTS完成后才为 True
        self._last_activity = time.time()  # 上次有效交互时间（唤醒 or 音频识别）

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, StartFrame):
            self._loop = asyncio.get_running_loop()
            self._start_ros()
            # 启动时等待唤醒（一次性，唤醒后由 _do_wakeup_reply 处理）
            log.info("等待唤醒...")
            await self._loop.run_in_executor(None, self._wakeup_event.wait)
            self._wakeup_event.clear()  # 重置，供后续复用
        elif isinstance(frame, EndFrame):
            self._running = False
        await self.push_frame(frame, direction)

    # ── ROS 线程：同时监听唤醒 + 音频，统一入口 ─────────────────────
    def _start_ros(self):
        self._running = True
        self._ros_thread = threading.Thread(target=self._ros_spin, daemon=True)
        self._ros_thread.start()

    def _ros_spin(self):
        try:
            import rclpy
            from rclpy.node import Node
            from rclpy.qos import QoSProfile, QoSHistoryPolicy, QoSReliabilityPolicy, QoSDurabilityPolicy
        except ImportError:
            log.error("未安装 rclpy，无法订阅音频/唤醒。请在 A2 机器人环境运行。")
            return

        if not rclpy.ok():
            rclpy.init()

        parent = self

        class _DualNode(Node):
            def __init__(self):
                super().__init__("a2_agent_dual_node")
                qos_audio = QoSProfile(
                    history=QoSHistoryPolicy.KEEP_LAST, depth=10,
                    reliability=QoSReliabilityPolicy.BEST_EFFORT,
                    durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
                )
                qos_wakeup = QoSProfile(
                    reliability=QoSReliabilityPolicy.BEST_EFFORT,
                    durability=QoSDurabilityPolicy.VOLATILE,
                    depth=10,
                )
                from ros2_plugin_proto.msg import RosMsgWrapper
                # 音频 topic（唤醒前也会收到，但 _handle_ros_audio 会丢弃）
                self.create_subscription(
                    RosMsgWrapper, config.AUDIO_TOPIC, self._on_audio, qos_audio
                )
                # 唤醒 topic
                self.create_subscription(
                    RosMsgWrapper,
                    "/agent/wakeup/pb_3Aaimdk_2Eprotocol_2EWakeUpResult",
                    self._on_wakeup,
                    qos_wakeup,
                )

            def _on_audio(self, msg):
                print("🔊🔊🔊 _on_audio 被调用！ data_len=%d" % (len(msg.data) if hasattr(msg, "data") else -1))
                parent._handle_ros_audio(msg)

            def _on_wakeup(self, msg):
                log.info("🔔 _on_wakeup 被调用！msg=%s", str(msg)[:300])
                parent._record_activity()
                log.info("🔔 _loop=%s, is_running=%s",
                         parent._loop, parent._loop.is_running() if parent._loop else "None")
                if WAKEUP_REPLY_MODE == "non-blocking":
                    parent._audio_active = True
                    parent._wakeup_event.set()
                    if parent._loop and parent._loop.is_running():
                        log.info("🔔 用 run_coroutine_threadsafe 调度 TTS")
                        asyncio.run_coroutine_threadsafe(parent._do_wakeup_reply(), parent._loop)
                    else:
                        log.info("🔔 非运行状态，用 run_coroutine_threadsafe")
                        asyncio.run_coroutine_threadsafe(parent._do_wakeup_reply(), parent._loop)
                else:
                    log.info("🔔 阻塞模式，调度 TTS")
                    if parent._loop and parent._loop.is_running():
                        log.info("🔔 用 run_coroutine_threadsafe 调度 TTS")
                        asyncio.run_coroutine_threadsafe(parent._do_wakeup_reply(), parent._loop)
                    else:
                        log.info("🔔 非运行状态，用 run_coroutine_threadsafe")
                        asyncio.run_coroutine_threadsafe(parent._do_wakeup_reply(), parent._loop)

        # ✅ 创建节点实例 + spin，回调才能触发
        node = _DualNode()
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
        log.info("🎤 进入语音交互模式，_audio_active=True，已订阅音频 topic")
        try:
            log.info("✅ 唤醒成功，TTS 回复『我在呢』")
            await play_tts("我在呢", interrupt=True)
        except Exception as e:
            log.error("TTS 异常: %s", e)
        finally:
            log.info("✅✅✅ TTS 完成（或异常），set wakeup_event")
            self._wakeup_event.set()
            log.info("🎤 进入语音交互模式，pipeline 继续运行")

    def _handle_ros_audio(self, msg):
        """
        解析 A2 音频消息，对齐 voice.py 的 ProcessedAudioOutput protobuf 逻辑。
        唤醒前丢弃音频帧。空闲超时后自动回等待唤醒状态。

        VAD 状态：
          1 = 语音开始
          2 = 语音中
          3 = 语音结束（触发识别）
          0 = 静默
        """
        log.info("🎤 _handle_ros_audio 被调用！_audio_active=%s", self._audio_active)
        self._idle_check()
        if not self._audio_active:
            log.info("🎤 _audio_active=False，丢弃音频帧")
            return
        try:
            # 检查 serialization_type（必须是 "pb"）
            serialization_type = getattr(msg, "serialization_type", "unknown")
            log.debug("📡 收到 ROS 消息, serialization_type=%s, data_len=%d",
                     serialization_type, len(msg.data) if hasattr(msg, "data") else -1)

            if hasattr(msg, "serialization_type") and msg.serialization_type != "pb":
                log.debug("跳过非 pb 消息: %s", serialization_type)
                return

            # 解析 protobuf
            from ros2_plugin_proto.msg import RosMsgWrapper
            from aimdk.protocol_pb2 import ProcessedAudioOutput

            raw_data = b"".join(msg.data)
            log.debug("📦 原始数据长度: %d bytes", len(raw_data))

            result = ProcessedAudioOutput()
            result.ParseFromString(raw_data)

            stream_id = result.stream_id
            vad_state = result.vad_state
            audio_data = bytes(result.audio_data)

            stream_id = result.stream_id
            vad_state = result.vad_state
            audio_data = bytes(result.audio_data)

            log.info("🎤 音频帧解析: stream_id=%d, vad_state=%d, audio_data_len=%d",
                     stream_id, vad_state, len(audio_data))

            # 只处理板载麦克风（stream_id=1）
            if stream_id != 1:
                log.debug("跳过非板载麦克风 stream_id=%d", stream_id)
                return

        except Exception as e:
            log.error("解析音频消息失败: %s", e)
            import traceback
            log.error("堆栈: %s", traceback.format_exc())
            return

        # ── VAD 状态机：对齐 voice.py ────────────────────────────────
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
            log.info("🎤 语音结束，buffer 大小=%d bytes (%.2fs)", total_size, total_size / 32000)

            if total_size < 6400:  # < 0.2s，太短跳过
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
                log.debug("🔇 静默，重置录音状态")

    # ── 把协程安全地丢回 pipeline 的事件循环 ──────────────────────────────
    def _submit(self, coro):
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, self._loop)

    def _idle_check(self):
        """检查空闲超时，超时后重新等待唤醒"""
        idle = time.time() - self._last_activity
        if idle > IDLE_TIMEOUT:
            if self._audio_active:
                log.info("😴 空闲超时，重新进入等待唤醒状态 (idle=%.1fs)", idle)
                self._audio_active = False
                self._wakeup_event.clear()
        else:
            log.debug("⏱️ idle_check: idle=%.1fs, _audio_active=%s", idle, self._audio_active)

    def _record_activity(self):
        self._last_activity = time.time()

    async def _emit(self, frame: Frame):
        await self.push_frame(frame, FrameDirection.DOWNSTREAM)

    async def _transcribe_and_push(self, pcm: bytes):
        if len(pcm) < 6400:  # < ~0.2s，忽略
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
