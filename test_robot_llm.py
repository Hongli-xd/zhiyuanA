"""Robot 快速测试：LLM + TTS + 工具调用（不依赖 ROS2 audio）"""
import os
os.environ['PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION'] = 'python'
os.environ['NLTK_DATA'] = '/tmp/nltk_data'

import asyncio
import aiohttp
import config


async def test_llm():
    url = f"{config.LLM_BASE_URL}/messages"
    headers = {
        "Authorization": f"Bearer {config.LLM_API_KEY}",
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    body = {
        "model": config.LLM_MODEL,
        "max_tokens": 200,
        "messages": [{"role": "user", "content": "今天天气怎么样"}]
    }
    print("测试 LLM API...")
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=body, headers=headers, timeout=30) as r:
            print(f"HTTP Status: {r.status}")
            data = await r.json(content_type=None)
            print("Response:", data)


async def test_tts():
    from services.tts import play_tts
    print("\n测试 TTS...")
    result = await play_tts("你好，我是小航。天气我查不了，但我可以帮你做别的事。")
    print("TTS Result:", result)


async def test_tools():
    from tools.registry import get_tools_schema
    print("\n测试工具注册...")
    schema = get_tools_schema()
    print(f"已注册工具: {[t.name for t in schema.standard_tools]}")
    return schema


async def main():
    print("=" * 50)
    print("Robot LLM + TTS + 工具 快速测试")
    print("=" * 50)

    await test_llm()
    await test_tts()
    schema = await test_tools()

    print("\n✅ 测试完成")


if __name__ == "__main__":
    asyncio.run(main())
