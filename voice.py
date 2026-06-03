#!/usr/bin/env python3

import rclpy
import requests
import time
import wave
import io
import os
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
from scipy.io import wavfile
from scipy import signal

# ── 关键词 → 任务ID ──────────────────────────
KEYWORD_TASK_MAP = {
    "电梯": "1",
    "接人": "1",
    "接待": "1",
    "介绍": "2",
    
}

# ── FunASR 配置 ──────────────────────────────
dashscope.api_key = "sk-e19c26823f0346b1acbc2071705bcb0f"

# ── 任务引擎接口 ─────────────────────────────
TASK_ENGINE = "http://192.168.100.110:57881/rpc/aimdk.protocol.TaskEngineService"
SYSTEM_SERVICE = "http://192.168.100.110:51056/rpc/aimdk.protocol.SystemService"
HEADERS = {"content-type": "application/json"}


def resample_to_wav(audio_data: bytes, orig_sr: int = 8000, target_sr: int = 16000) -> bytes:
    """音频重采样到 16kHz 并返回 WAV 字节（scipy 实现）"""
    int16_data = np.frombuffer(audio_data, dtype=np.int16)
    num_samples = int(len(int16_data) * target_sr / orig_sr)
    resampled = signal.resample(int16_data, num_samples).astype(np.int16)

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(target_sr)
        wf.writeframes(resampled.tobytes())
    return buffer.getvalue()


def call_funasr(audio_data: bytes) -> str:
    """发 FunASR 识别，返回识别文本"""
    wav_data = resample_to_wav(audio_data)

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
    """按文档 7.9.4 三步走启动任务"""
    print(f"[Task-{task_id}] 开始三步启动流程...")
    print(f"[Task-{task_id} Step1] 调用 MigrateSystemStateSync，切换系统状态为 Auto...")
    try:
        r = requests.post(
            f"{SYSTEM_SERVICE}/MigrateSystemStateSync",
            headers=HEADERS,
            json={"state": "Auto"},
            timeout=5,
        )
        r.raise_for_status()
        print(f"[Task-{task_id} Step1] ✅ MigrateSystemStateSync 成功")
    except Exception as e:
        print(f"[Task-{task_id} Step1] ❌ 切换 Auto 失败: {e}")
        return False

    print(f"[Task-{task_id} Step2] 调用 SetCurrentTask，设置 task_id={task_id}...")
    try:
        r1 = requests.post(
            f"{TASK_ENGINE}/SetCurrentTask",
            headers=HEADERS,
            json={"task_id": task_id},
            timeout=5,
        )
        r1.raise_for_status()
        resp1 = r1.json()
        print(f"[Task-{task_id} Step2] SetCurrentTask 返回: {resp1}")
        if not resp1.get("is_success", False):
            print(f"[Task-{task_id} Step2] ❌ SetCurrentTask 失败")
            return False
        print(f"[Task-{task_id} Step2] ✅ SetCurrentTask 成功")
    except Exception as e:
        print(f"[Task-{task_id} Step2] ❌ SetCurrentTask 异常: {e}")
        return False

    time.sleep(0.1)
    print(f"[Task-{task_id} Step3] 调用 LaunchTask，启动任务...")
    try:
        r2 = requests.post(
            f"{TASK_ENGINE}/LaunchTask",
            headers=HEADERS,
            json={"task_id": task_id},
            timeout=5,
        )
        r2.raise_for_status()
        resp2 = r2.json()
        print(f"[Task-{task_id} Step3] LaunchTask 返回: {resp2}")
        res = resp2.get("res", "ReturnType_UNDEFINED")
        if res == "ReturnType_SUCCEED":
            print(f"[Task-{task_id} Step3] ✅ 任务 {task_id} 启动成功")
            return True
        else:
            print(f"[Task-{task_id} Step3] ❌ 任务 {task_id} 启动失败: {res}")
            return False
    except Exception as e:
        print(f"[Task-{task_id} Step3] ❌ LaunchTask 异常: {e}")
        return False


# ── ROS2 节点 ────────────────────────────────
class VoiceTaskNode(Node):
    def __init__(self):
        super().__init__("voice_task")

        qos = QoSProfile(
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=10,
            reliability=QoSReliabilityPolicy.RELIABLE,
            durability=QoSDurabilityPolicy.VOLATILE,
        )

        self.create_subscription(
            RosMsgWrapper,
            "/agent/process_audio_output",
            self.on_audio,
            qos,
        )

        self.last_trigger = {}
        self.cooldown = 3.0

        self.get_logger().info("🎤 语音任务监听已启动（自研 ASR）")

    def on_audio(self, msg):
        print("[Step 0] 接收到 ROS 消息，开始解析...")
        result = ProcessedAudioOutput()
        result.ParseFromString(b"".join(msg.data))

        stream_id = result.stream_id
        vad_state = result.audio_vad_state
        audio_data = bytes(result.audio_data)

        print(f"[Step 0] stream_id={stream_id}, vad_state={vad_state}, audio_size={len(audio_data)} bytes")

        # 只处理板载麦克风，且语音结束才识别
        if stream_id != 1 or vad_state != 3 or not audio_data:
            print(f"[Step 0] 条件不满足，跳过识别 (stream_id=1 required, vad_state=3 required, audio非空)")
            return

        print("[Step 1] VAD 语音结束，开始识别...")
        print(f"[Step 1] 原始 PCM 大小: {len(audio_data)} bytes")

        print("[Step 2] PCM 转为 WAV 格式...")
        text = call_funasr(audio_data)
        print(f"[Step 3] FunASR 返回文字: '{text}'")

        if not text:
            self.get_logger().info("🔇 未识别到文字")
            return

        self.get_logger().info(f"📝 识别结果: {text}")

        print(f"[Step 4] 开始关键词匹配，文本: '{text}'")
        for keyword, task_id in KEYWORD_TASK_MAP.items():
            print(f"[Step 4] 检查关键词: '{keyword}'")
            if keyword in text:
                print(f"[Step 4] ✅ 命中关键词: '{keyword}'")
                now = time.time()
                if now - self.last_trigger.get(task_id, 0) < self.cooldown:
                    print(f"[Step 4] ⏸️ 任务 {task_id} 冷却中 ({self.cooldown}s)，跳过")
                    return

                print(f"[Step 5] 🎯 准备启动任务 {task_id}")
                if launch_task(task_id):
                    self.last_trigger[task_id] = now
                    print(f"[Step 6] ✅ 任务 {task_id} 启动完成")
                else:
                    print(f"[Step 6] ❌ 任务 {task_id} 启动失败")
                return


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