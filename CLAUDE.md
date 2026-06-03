# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A2 voice agent framework (Pipecat + LangGraph) for the A2 robot. The robot receives voice input via ROS2 → ASR → LLM reasoning → tool/skill execution → TTS response.

```
A2 mic (ROS2 VAD audio 16k/16bit)
   ▼
ROS2AudioInputProcessor ──ASR──► TranscriptionFrame
   ▼
AnthropicLLMService ──tool call──► atomic Tool / LangGraph Skill
   │  (HTTP RPC / StateGraph)        │ result via result_callback
   ▼                                ▼ 回注上下文 → LLM 生成回复
A2TTSProcessor ──PlayTTS──► A2 speaker
```

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Offline self-test (mock RPC, no robot needed)
python -m test_offline

# Run on robot (requires A2 ROS2 env + a2_aimdk whl)
source prebuilt/ros2_plugin_proto_aarch64/share/ros2_plugin_proto/local_setup.bash
python -m main
```

> The package `prebuilt/a2_aimdk-2.0.1-py3-none-any.whl` (protobuf message types for audio/wakeword) and the ROS2 plugin are required only on the robot. The `test_offline` path needs neither.

## Architecture

### Tool vs Skill distinction
- **Tool** = single atomic HTTP RPC (e.g., `tools/light.py`, `tools/task_engine.py`)
- **Skill** = multi-step business logic via LangGraph `StateGraph` (e.g., `skills/launch_task.py`)

A Skill composes multiple Tools into a state machine. Skills are exposed to the LLM as a single tool (e.g., `launch_aimmaster_task`), while atomic Tools are registered individually.

### Key modules
| File | Role |
|---|---|
| `config.py` | Central config: IPs/ports, API keys, keyword→task map, light presets. All env-var overridable. |
| `main.py` | Entry point; builds Pipecat pipeline and runs `PipelineRunner` |
| `pipeline/build.py` | Assembles pipeline: audio → user aggregator → LLM → TTS → assistant aggregator |
| `services/a2_client.py` | Async HTTP RPC client + `ConfidenceGate` safety check for physical actions |
| `tools/registry.py` | Registers all tools/skills with Pipecat LLM service via `FunctionSchema` |
| `skills/launch_task.py` | LangGraph `StateGraph`: `auto → set_current → launch` with fail-fast branching |

### ConfidenceGate
`ConfidenceGate` in `services/a2_client.py` intercepts physical-action tools (e.g., `launch_aimmaster_task`). Calls without sufficient confidence (configurable threshold, default 0.6) are blocked. Integrate with ASR confidence or external confirm mechanism for production.

### Task routing
`KEYWORD_TASK_MAP` in `config.py` maps spoken keywords to AimMaster task IDs. The `launch_aimmaster_task` skill accepts either a direct `task_id` or a `keyword` that gets resolved through this map. `TASK_NAMES` provides human-readable names used in LLM responses.

### Tool execution flow
When LLM calls a tool → handler in `tools/registry.py` → calls HTTP RPC via `A2Client.post_rpc()` → result passed to `result_callback()` → injected into LLM context → LLM generates response → `A2TTSProcessor` speaks it.

## Extending the Agent

1. **Add an atomic Tool**: create `tools/<name>.py` with an async function, add `FunctionSchema` in `tools/registry.py`, register handler in `register_all()`.
2. **Add a multi-step Skill**: create `skills/<name>.py` using LangGraph `StateGraph`, expose as single tool in `registry.py`.
3. **Add a light preset**: add to `LIGHT_PRESETS` in `config.py`.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `LLM_API_KEY` | required | Anthropic API key |
| `ASR_API_KEY` | required | ASR provider key |
| `ASR_BASE_URL` | OpenAI compat endpoint | ASR gateway |
| `ASR_PROVIDER` | `openai` | `openai` or `whisper` (local) |
| `A2_HOST` | `192.168.100.110` | Main robot (TTS/task/system) |
| `A2_LIGHT_HOST` | `192.168.100.100` | Light strip service |
| `A2_HTTP_TIMEOUT` | `5` | RPC timeout in seconds |
| `A2_AUDIO_TOPIC` | `/agent/process_audio_output` | ROS2 audio topic |
| `WHISPER_MODEL_SIZE` | `small` | Local ASR model (when `ASR_PROVIDER=whisper`) |
| `WHISPER_DEVICE` | `cuda` | Compute device for local whisper |