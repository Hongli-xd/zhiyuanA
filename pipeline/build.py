"""
组装 Pipecat pipeline。

数据流（与技术选型文档的架构图一致）：

    A2 mic (ROS2 VAD音频)
        │
        ▼
    ROS2AudioInputProcessor  ── ASR ──► TranscriptionFrame
        │
        ▼
    user context aggregator        ┐
        │                          │  LLMContext: 对话历史 + 工具 schema
    AnthropicLLMService ───────────┤  LLM 推理 + 工具调用
        │  (工具 handler 内部调用      │      └─► LangGraph skill / 原子 Tool
        │   HTTP RPC / LangGraph)    │           执行结果经 result_callback
        ▼                          │           回注上下文 → LLM 生成回复
    assistant context aggregator   ┘
        │
        ▼
    A2TTSProcessor ── PlayTTS ──► A2 speaker
"""

import logging

from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
)
from pipecat.services.anthropic.llm import AnthropicLLMService

import config
from pipeline.a2_tts_output import A2TTSProcessor
from tools.registry import get_tools_schema, register_all

log = logging.getLogger("a2.pipeline")


SYSTEM_PROMPT = """你是智元 A2 机器人的语音交互助手。你以完全接管模式运行，全权负责理解用户语音并执行机器人动作。

## 对话风格
- 根据用户意图自行判断：执行任务 还是 日常闲聊。
- 日常闲聊时，你是博学的百科助手，说话有节奏、有语气助词（嗯、嘛、哦、呀），可以适当幽默，但不过度。
- 执行任务时，用简短口语化的指令确认，不要 markdown、列表或表情符号。
- 所有回复都会被 TTS 念出来，每句控制在 20 字以内，语气自然。

## 工具调用规则
每当决定调用工具时，回复必须同时满足：
  1. 先有一句对用户的口头回应（如"好的，我去接人"、"嗯，我帮你打开灯"）
  2. 紧随其后才是 tool_call 格式
  不要只发 tool_call 而不说话。
- 「等人」「在电梯口等」等场景 → wait_for_person。
- 讲解/电梯等人等任务 → launch_aimmaster_task（直接给 task_id）。
- 需要状态指示时 → set_status_light。
- 物理移动指令：确认意图后再执行。
- 工具执行完成后，根据返回结果一句话告知用户；ok=False 时必须说明失败原因。

## 拒接/不会的情况
如果用户的要求你确实做不到，先口头说一句"抱歉，这个我还不会"，再拒绝，不要空缺。"""


def build_llm():
    llm = AnthropicLLMService(
        api_key=config.LLM_API_KEY,
        settings=AnthropicLLMService.Settings(model=config.LLM_MODEL),
    )
    register_all(llm)  # 注册所有工具/技能 handler
    return llm


def build_context() -> LLMContext:
    ctx = LLMContext()
    ctx.set_messages([{"role": "system", "content": SYSTEM_PROMPT}])
    ctx.set_tools(get_tools_schema())
    return ctx


def build_pipeline(audio_input):
    """
    audio_input: 上游音频源处理器。
      - 机器人上：ROS2AudioInputProcessor
      - 本地测试：MockTextInput（直接喂 TranscriptionFrame）
    """
    llm = build_llm()
    context = build_context()
    aggregators = LLMContextAggregatorPair(context)
    tts_out = A2TTSProcessor()

    pipeline = Pipeline([
        audio_input,            # 音频/转写源
        aggregators.user(),     # 把用户转写并入上下文
        llm,                    # LLM 推理 + 工具调用
        tts_out,                # A2 PlayTTS
        aggregators.assistant(),  # 把助手回复 + 工具结果并入上下文
    ])
    return pipeline, llm
