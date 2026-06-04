#!/usr/bin/env python3
"""
独立测试脚本：验证 ROS2 音频 topic 是否有数据。
用法：python test_ros2_audio.py
不需要 pipecat，只需要 rclpy。
"""

import sys
import time
import threading

def main():
    try:
        import rclpy
        from rclpy.node import Node
        from rclpy.qos import QoSProfile, QoSHistoryPolicy, QoSReliabilityPolicy, QoSDurabilityPolicy
        from ros2_plugin_proto.msg import RosMsgWrapper
    except ImportError as e:
        print(f"❌ 缺少依赖: {e}")
        return

    rclpy.init()

    class TestNode(Node):
        def __init__(self):
            super().__init__("test_audio_listener")

            # 音频 topic
            qos_audio = QoSProfile(
                history=QoSHistoryPolicy.KEEP_LAST, depth=10,
                reliability=QoSReliabilityPolicy.BEST_EFFORT,
                durability=QoSDurabilityPolicy.VOLATILE,
            )
            self.create_subscription(
                RosMsgWrapper,
                "/agent/process_audio_output/pb_3Aaimdk_2Eprotocol_2EProcessedAudioOutput",
                self._on_audio,
                qos_audio,
            )

            # 唤醒 topic（看看唤醒是否还在触发）
            qos_wakeup = QoSProfile(
                reliability=QoSReliabilityPolicy.BEST_EFFORT,
                durability=QoSDurabilityPolicy.VOLATILE,
                depth=10,
            )
            self.create_subscription(
                RosMsgWrapper,
                "/agent/wakeup/pb_3Aaimdk_2Eprotocol_2EWakeUpResult",
                self._on_wakeup,
                qos_wakeup,
            )

            self._audio_count = 0
            self._wakeup_count = 0
            self._start_time = time.time()

            print("=" * 60)
            print("📡 音频监听测试启动")
            print("   1. 先说唤醒词")
            print("   2. 唤醒后随便说话")
            print("   3. 观察是否有音频帧打印")
            print("   4. Ctrl+C 退出")
            print("=" * 60)

        def _on_wakeup(self, msg):
            self._wakeup_count += 1
            elapsed = time.time() - self._start_time
            print(f"[{elapsed:.1f}s] 🔔 唤醒消息 #{self._wakeup_count}! "
                  f"serialization_type={msg.serialization_type}, data_len={len(msg.data)}")

        def _on_audio(self, msg):
            self._audio_count += 1
            elapsed = time.time() - self._start_time

            # 只打印前 10 条 + 之后每 50 条打印一次
            if self._audio_count <= 10 or self._audio_count % 50 == 0:
                try:
                    raw_data = b"".join(msg.data)
                    from aimdk.protocol_pb2 import ProcessedAudioOutput
                    result = ProcessedAudioOutput()
                    result.ParseFromString(raw_data)
                    print(f"[{elapsed:.1f}s] 📡 音频帧 #{self._audio_count}: "
                          f"stream_id={result.stream_id}, vad={result.vad_state}, "
                          f"audio_len={len(result.audio_data)} bytes, "
                          f"total_raw={len(raw_data)} bytes")
                except Exception as e:
                    print(f"[{elapsed:.1f}s] 📡 音频帧 #{self._audio_count}: "
                          f"parse failed: {e}, raw_data_len={len(b''.join(msg.data))}")

    node = TestNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        elapsed = time.time() - node._start_time
        print(f"\n{'=' * 60}")
        print(f"⏹ 测试结束，运行 {elapsed:.1f}s")
        print(f"   唤醒消息: {node._wakeup_count} 条")
        print(f"   音频帧: {node._audio_count} 条")
        if node._audio_count == 0:
            print("   ❌ 没有收到任何音频帧！问题在 ROS2 topic / QoS / 发布端")
        else:
