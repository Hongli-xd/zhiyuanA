"""快速测试 LLM 对「去门口接人」的回复（不依赖 audio pipeline）。"""
import asyncio
import aiohttp
import config
from pipeline.build import SYSTEM_PROMPT
from tools.registry import get_tools_schema


def _schema_to_api_tools(schema) -> list:
    """把 Pipecat ToolsSchema 转成 Anthropic API 格式"""
    tools = []
    for t in schema.standard_tools:
        tools.append({
            "name": t.name,
            "description": t.description,
            "input_schema": {"type": "object", "properties": t.properties, "required": t.required}
        })
    return tools


async def test_llm(prompt_text: str, user_input: str):
    url = f"{config.LLM_BASE_URL}/messages"
    headers = {
        "Authorization": f"Bearer {config.LLM_API_KEY}",
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    tools = _schema_to_api_tools(get_tools_schema())
    body = {
        "model": config.LLM_MODEL,
        "max_tokens": 300,
        "messages": [
            {"role": "system", "content": prompt_text},
            {"role": "user", "content": user_input},
        ],
        "tools": tools,
    }
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=body, headers=headers, timeout=30) as r:
            data = await r.json(content_type=None)
            print(f"[status {r.status}]")
            print(f"用户: {user_input}")
            content = data.get("content", [])
            if not content:
                print(f"raw: {data}")
            for block in content:
                if block.get("type") == "text":
                    print(f"LLM: {block['text']}")
                elif block.get("type") == "tool_use":
                    print(f"tool_call: {block['name']}({block['input']})")
            print()


async def main():
    await test_llm(SYSTEM_PROMPT, "去门口接人")
    await test_llm(SYSTEM_PROMPT, "今天天气怎么样")
    await test_llm(SYSTEM_PROMPT, "帮我把灯关掉")


if __name__ == "__main__":
    asyncio.run(main())
