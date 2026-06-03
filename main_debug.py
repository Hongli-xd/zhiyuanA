"""
带详细调试日志的 main.py——在 robot 上运行，
可以看到发给 LLM 的完整请求和响应，方便定位一直重试的原因。
"""
import os
os.environ['PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION'] = 'python'
os.environ['NLTK_DATA'] = '/tmp/nltk_data'

import asyncio
import logging
import sys

from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask

from pipeline.build import build_pipeline
from pipeline.ros_audio_input import ROS2AudioInputProcessor
from services.a2_client import a2_client

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)],
)

# 拦截 HTTP 请求，打印发给 LLM 的内容
import aiohttp
_orig_post = aiohttp.ClientSession.post

async def _logged_post(self, url, **kwargs):
    if '/messages' in str(url):
        import json
        body = kwargs.get('json', {})
        print("\n" + "=" * 60)
        print(f"[DEBUG] >>> LLM API 请求")
        print(f"URL: {url}")
        print(f"Body 消息数: {len(body.get('messages', []))}")
        for i, msg in enumerate(body.get('messages', [])):
            role = msg.get('role', '?')
            content = msg.get('content', '')
            if isinstance(content, list):
                for c in content:
                    if isinstance(c, dict):
                        print(f"  消息[{i}] role={role} type={c.get('type')} text={c.get('text', '')[:100] if c.get('type')=='text' else str(c)[:100]}")
            else:
                print(f"  消息[{i}] role={role} content={str(content)[:200]}")
        print(f"Tools: {[t.get('name') for t in body.get('tools', [])]}")
        print("=" * 60 + "\n")
    return await _orig_post(self, url, **kwargs)

aiohttp.ClientSession.post = _logged_post


async def run():
    print("🤖 A2 语音 Agent 启动（调试模式）")
    await a2_client.start()

    audio_input = ROS2AudioInputProcessor()
    pipeline, llm = build_pipeline(audio_input)

    task = PipelineTask(pipeline)
    runner = PipelineRunner()

    print("=" * 50)
    print("  等待语音输入...（唤醒后说话）")
    print("=" * 50 + "\n")

    try:
        await runner.run(task)
    finally:
        await a2_client.stop()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n收到退出信号，已停止")
