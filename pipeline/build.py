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

from a2_agent import config
from a2_agent.pipeline.a2_tts_output import A2TTSProcessor
from a2_agent.tools.registry import get_tools_schema, register_all

log = logging.getLogger("a2.pipeline")


SYSTEM_PROMPT = """你是智元 A2 机器人的语音交互助手。你以完全接管模式运行，全权负责理解用户语音并执行机器人动作。

规则：
- 用简短、口语化的中文回复，因为回复会被 TTS 念出来，不要用 markdown、列表或表情符号。
- 当用户的意图对应一个可执行能力时，调用相应工具，不要只是口头答应。
- 「等人」「在电梯口等」等场景：调用 wait_for_person。
- 「开始讲解」「等人/电梯门」等已配置任务：调用 launch_aimmaster_task（用 keyword 或 task_id）。
- 需要给现场人员状态指示时可调用 set_status_light。
- 涉及机器人物理移动的指令，确认理解无误后再执行。
- 工具执行完成后，根据返回结果用一句话告知用户。"""


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
