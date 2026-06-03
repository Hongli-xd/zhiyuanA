import rclpy
import requests
import time
import wave
import io
import os
import threading
from rclpy.node import Node
from rclpy.qos import (
    QoSHistoryPolicy, QoSProfile,
    QoSReliabilityPolicy, QoSDurabilityPolicy,
)
from ros2_plugin_proto.msg import RosMsgWrapper
from aimdk.protocol_pb2 import ProcessedAudioOutput

import dashscope
from dashscope.audio.asr import Recognition
import numpy as np

# ── 关键词 → 任务ID ──────────────────────────
KEYWORD_TASK_MAP = {
    "电梯": "2",
    "接人": "2",
    "接待": "2",
    "介绍": "6",
    "讲解": "6",
    "英文介绍":"1",
    "英文讲解":"1"
}

# ── FunASR 配置 ──────────────────────────────
dashscope.api_key = "sk-e19c26823f0346b1acbc2071705bcb0f"

# ── 任务引擎接口 ─────────────────────────────
TASK_ENGINE    = "http://192.168.100.110:57881/rpc/aimdk.protocol.TaskEngineService"
SYSTEM_SERVICE = "http://192.168.100.110:51056/rpc/aimdk.protocol.SystemService"
HEADERS = {"content-type": "application/json"}

# ── 任务终态：这些状态表示任务已结束 ──────────
TASK_DONE_STATES = {
    "StateType_IDLE",
    "StateType_STOPPED",
    "StateType_FAILSTOP",
    "StateType_EXCEPTION",
}


def pcm_to_wav_bytes(audio_data: bytes, sample_rate=16000) -> bytes:
    """16kHz 16bit 单声道 PCM → WAV 字节"""
    int16_data = np.frombuffer(audio_data, dtype=np.int16)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(int16_data.tobytes())
    return buffer.getvalue()


def call_funasr(audio_data: bytes) -> str:
    """发 FunASR 识别，返回识别文本"""
    wav_data = pcm_to_wav_bytes(audio_data)
    tmp_path = "/tmp/funasr_input.wav"
    with open(tmp_path, "wb") as f:
        f.write(wav_data)

    try:
        result = Recognition(
            model="fun-asr-realtime-2026-02-28",
            format="wav",
            sample_rate=16000,
            language_hints=["zh", "en"],
            callback=None,
        ).call(tmp_path)
        os.unlink(tmp_path)

        if result.status_code == 200:
            sentences = result.get_sentence()
            if sentences and len(sentences) > 0:
                return sentences[0].get("text", "")
        return ""
    except Exception as e:
        print(f"[ASR] 识别异常: {e}")
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        return ""


def launch_task(task_id: str) -> bool:
    """三步启动任务"""
    print(f"[Task-{task_id}] 开始三步启动流程...")

    # Step 1: 切 Auto
    try:
        r = requests.post(
            f"{SYSTEM_SERVICE}/MigrateSystemStateSync",
            headers=HEADERS, json={"state": "Auto"}, timeout=5,
        )
        r.raise_for_status()
        print(f"[Task-{task_id} Step1] ✅ 切 Auto 成功")
    except Exception as e:
        print(f"[Task-{task_id} Step1] ❌ 失败: {e}")
        return False

    # Step 2: SetCurrentTask
    try:
        r1 = requests.post(
            f"{TASK_ENGINE}/SetCurrentTask",
            headers=HEADERS, json={"task_id": task_id}, timeout=5,
        )
        r1.raise_for_status()
        resp1 = r1.json()
        if not resp1.get("is_success", False):
            print(f"[Task-{task_id} Step2] ❌ SetCurrentTask 失败: {resp1}")
            return False
        print(f"[Task-{task_id} Step2] ✅ SetCurrentTask 成功")
    except Exception as e:
        print(f"[Task-{task_id} Step2] ❌ 异常: {e}")
        return False

    time.sleep(0.1)

    # Step 3: LaunchTask
    try:
        r2 = requests.post(
            f"{TASK_ENGINE}/LaunchTask",
            headers=HEADERS, json={"task_id": task_id}, timeout=5,
        )
        r2.raise_for_status()
        resp2 = r2.json()
        res = resp2.get("res", "ReturnType_UNDEFINED")
        if res == "ReturnType_SUCCEED":
            print(f"[Task-{task_id} Step3] ✅ 启动成功")
            return True
        else:
            print(f"[Task-{task_id} Step3] ❌ 启动失败: {res}")
            return False
    except Exception as e:
        print(f"[Task-{task_id} Step3] ❌ 异常: {e}")
        return False


def get_task_state(task_id: str) -> str:
    """调用 GetTask 查询任务状态，返回 state 字符串"""
    try:
        r = requests.post(
            f"{TASK_ENGINE}/GetTask",
            headers=HEADERS,
            json={"task_id": task_id},
            timeout=5,
        )
        )
        r.raise_for_status()
        resp = r.json()
        data = resp.get("data", [])
        if isinstance(data, list) and len(data) > 0:
            return data[0].get("state", "")
        elif isinstance(data, dict):
            return data.get("state", "")
    except Exception as e:
        print(f"[GetTask] 异常: {e}")
    return ""


class VoiceTaskNode(Node):
    def __init__(self):
        super().__init__("voice_task")

        qos = QoSProfile(
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=10,
            reliability=QoSReliabilityPolicy.BEST_EFFORT,
            durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
        )

        self.create_subscription(
            RosMsgWrapper,
            "/agent/process_audio_output/pb_3Aaimdk_2Eprotocol_2EProcessedAudioOutput",
            self.on_audio,
            qos,
        )

        self.last_trigger = {}
        self.cooldown = 3.0

        # 音频缓冲区：vad=1 开始攒，vad=3 一起发 ASR
        self.audio_buffer = bytearray()
        self.is_recording = False

        # 任务运行锁
        self.task_running = False
        self.current_task_id = None

        # 静音期：任务启动后一段时间忽略语音（防 TTS 误触发）
                                                                 

        self.get_logger().info("🎤 语音任务监听已启动")

    def on_audio(self, msg):
        """收到一帧音频，按 VAD 状态攒音频或触发识别"""
        try:
            if msg.serialization_type != "pb":
                return

            result = ProcessedAudioOutput()
            result.ParseFromString(b"".join(msg.data))

            stream_id = result.stream_id
            vad_state = result.vad_state
            audio_data = bytes(result.audio_data)

            # 只处理板载麦克风
            if stream_id != 1:
                return

            now = time.time()
            in_mute = now < self.mute_until

            # ── VAD 状态机 ──

            if vad_state == 1:  # 语音开始
                self.audio_buffer.clear()
                self.is_recording = True
                if audio_data:
                    self.audio_buffer.extend(audio_data)
                if in_mute:
                    self.get_logger().info("🎤 语音开始（静音期内，忽略）")
                else:
                    self.get_logger().info("🎤 检测到语音开始")

            elif vad_state == 2:  # 语音中
                if self.is_recording and audio_data:
                    self.audio_buffer.extend(audio_data)

            elif vad_state == 3:  # 语音结束
                if self.is_recording and audio_data:
                    self.audio_buffer.extend(audio_data)
                self.is_recording = False

                total_size = len(self.audio_buffer)
                duration = total_size / 32000

                # 静音期内：不做识别
                if in_mute:
                    self.get_logger().info(f"🔇 静音期内收到语音 {duration:.1f}s，忽略")
                    self.audio_buffer.clear()
                    return

                # 太短跳过
                if total_size < 6400:
                    self.get_logger().info("⏩ 语音太短，跳过")
                    self.audio_buffer.clear()
                    return

                # 任务运行中：识别但不触发
                if self.task_running:
                    text = call_funasr(bytes(self.audio_buffer))
                    self.audio_buffer.clear()
                    self.get_logger().info(
                        f"📝 识别结果: '{text}'（任务 {self.current_task_id} 运行中，不触发）"
                    )
                    return

                # 正常：识别 → 匹配 → 启动
                text = call_funasr(bytes(self.audio_buffer))
                self.audio_buffer.clear()
                self.get_logger().info(f"📝 识别结果: '{text}'")

                if not text:
                    self.get_logger().info("🔇 未识别到文字")
                    return

                for keyword, task_id in KEYWORD_TASK_MAP.items():
                    if keyword in text:
                        if now - self.last_trigger.get(task_id, 0) < self.cooldown:
                            self.get_logger().info("⏸️  冷却中，跳过")
                            return

                        self.get_logger().info(f"🎯 命中「{keyword}」→ 任务 {task_id}")

                        if launch_task(task_id):
                            self.last_trigger[task_id] = now
                            self.task_running = True
                            self.current_task_id = task_id
                            self.mute_until = now + 5.0  # 静音 5 秒

                            # ✅ 启动后台线程轮询任务状态
                            t = threading.Thread(
                                target=self._wait_task_done,
                                args=(task_id,),
                                daemon=True,
                            )
                            t.start()
                        return

                self.get_logger().info(f"❌ 未命中关键词: '{text}'")

            elif vad_state == 0:  # 静默
                if self.is_recording:
                    self.is_recording = False
    def _wait_task_done(self, task_id: str):
        """后台线程：轮询 GetTask，等任务进入终态后解锁"""
        self.get_logger().info(f"⏳ 开始轮询任务 {task_id} 状态...")
        last_state = None

        while rclpy.ok():
            time.sleep(3.0)

            state = get_task_state(task_id)

            if state != last_state:
                self.get_logger().info(f"🔄 任务 {task_id} 状态: {state}")
                last_state = state

            # ✅ 终态：任务结束，解锁
            if state in TASK_DONE_STATES:
                self.get_logger().info(
                    f"✅ 任务 {task_id} 已结束 (state={state})，恢复语音监听"
                )
                self.task_running = False
                self.current_task_id = None
                return

        # rclpy 退出
        self.task_running = False
        self.current_task_id = None


def main():
    rclpy.init()
    node = VoiceTaskNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
