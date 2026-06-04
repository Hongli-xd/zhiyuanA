#!/usr/bin/env python3
"""
独立测试脚本：验证 ROS2 音频 topic 是否有数据。
用法：python test_ros2_audio.py
不需要 pipecat，只需要 rclpy。
"""

import sys
import time

def main():
    try:
        import rclpy
        from rclpy.node import Node
        from rclpy.qos import QoSProfile, QoSHistoryPolicy, QoSReliabilityPolicy, QoSDurabilityPolicy
        from ros2_plugin_proto.msg import RosMsgWrapper
    except ImportError as e:
        print(f"缺少依赖: {e}")
        return

    rclpy.init()

    class TestNode(Node):
        def __init__(self):
            super().__init__("test_audio_listener")

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
            print("Audio listener test started")
            print("  1. Say wake word")
            print("  2. Speak anything after wakeup")
            print("  3. Watch for audio frame prints")
            print("  4. Ctrl+C to exit")
            print("=" * 60)

        def _on_wakeup(self, msg):
            self._wakeup_count += 1
            elapsed = time.time() - self._start_time
            print(f"[{elapsed:.1f}s] WAKEUP #{self._wakeup_count}! "
                  f"serialization_type={msg.serialization_type}, data_len={len(msg.data)}")

        def _on_audio(self, msg):
            self._audio_count += 1
            elapsed = time.time() - self._start_time

            if self._audio_count <= 10 or self._audio_count % 50 == 0:
                try:
                    raw_data = b"".join(msg.data)
                    from aimdk.protocol_pb2 import ProcessedAudioOutput
                    result = ProcessedAudioOutput()
                    result.ParseFromString(raw_data)
                    print(f"[{elapsed:.1f}s] AUDIO #{self._audio_count}: "
                          f"stream_id={result.stream_id}, vad={result.vad_state}, "
                          f"audio_len={len(result.audio_data)} bytes, "
                          f"raw={len(raw_data)} bytes")
                except Exception as e:
                    print(f"[{elapsed:.1f}s] AUDIO #{self._audio_count}: "
                          f"parse_error={e}, raw_len={len(b''.join(msg.data))}")

    node = TestNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        elapsed = time.time() - node._start_time
        print(f"\n{'=' * 60}")
        print(f"Test done, ran {elapsed:.1f}s")
        print(f"  Wakeup msgs: {node._wakeup_count}")
        print(f"  Audio frames: {node._audio_count}")
        if node._audio_count == 0:
            print("  NO audio frames received! Problem is ROS2 topic / QoS / publisher")
        else:
            print("  Audio frames OK! Problem is in your pipeline code")
        print(f"{'=' * 60}")
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == "__main__":
    main()
