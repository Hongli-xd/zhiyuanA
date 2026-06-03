"""
模拟 main.py 生产逻辑：ASR 识别结果 → LLM → 回复

完全复用 pipeline/build.py 的 SYSTEM_PROMPT 和 tools schema，
模拟收到 ASR "今天天气怎么样" 后的完整 LLM 对话流程。
"""
import asyncio
import aiohttp
import config
from pipeline.build import SYSTEM_PROMPT
from tools.registry import get_tools_schema


def _schema_to_api_tools(schema) -> list:
    tools = []
    for t in schema.standard_tools:
        tools.append({
            "name": t.name,
            "description": t.description,
            "input_schema": {"type": "object", "properties": t.properties, "required": t.required}
        })
    return tools


async def test_llm_full(user_input: str):
    url = f"{config.LLM_BASE_URL}/messages"
    headers = {
        "Authorization": f"Bearer {config.LLM_API_KEY}",
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    tools = _schema_to_api_tools(get_tools_schema())

    print(f"\n{'='*60}")
    print(f"模拟 ASR 输入: 「{user_input}」")
    print(f"{'='*60}")

    body = {
        "model": config.LLM_MODEL,
        "max_tokens": 300,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_input},
        ],
        "tools": tools,
    }

    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=body, headers=headers, timeout=30) as r:
            print(f"HTTP Status: {r.status}")
            data = await r.json(content_type=None)

            content = data.get("content", [])
            if not content:
                print(f"响应内容为空，raw: {data}")
                return

            for block in content:
                if block.get("type") == "text":
                    print(f"\n🤖 LLM 回复: 「{block['text']}」")
                elif block.get("type") == "tool_use":
                    name = block['name']
                    inp = block['input']
                    print(f"\n🔧 工具调用: {name}({inp})")

            # 检查 stop_reason
            print(f"\nstop_reason: {data.get('stop_reason')}")


async def main():
    # 测试用例
    test_cases = [
        "今天天气怎么样？",
        "去门口接人",
        "帮我把灯关掉",
        "开始讲解",
    ]

    for text in test_cases:
        await test_llm_full(text)
        await asyncio.sleep(0.5)


if __name__ == "__main__":
    asyncio.run(main())
