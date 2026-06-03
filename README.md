# A2 机器人语音 Agent（Pipecat + LangGraph）

按你的技术选型文档实现的完整语音 Agent 框架。链路：

```
A2 mic (ROS2 VAD音频16k/16bit)
   │
   ▼
ROS2AudioInputProcessor ──ASR──► TranscriptionFrame
   │
   ▼
user context aggregator
   │
   ▼
AnthropicLLMService ──工具调用──► 原子Tool / LangGraph技能
   │                                   │
   │   (HTTP RPC / StateGraph)         │ 执行结果经 result_callback
   ▼                                   ▼ 回注上下文 → LLM 生成回复
A2TTSProcessor ──PlayTTS──► A2 speaker
```

## 目录结构

```
a2_agent/
├── config.py                  # ★ 集中配置：IP/端口、API key、关键词映射、灯带预设
├── main.py                    # 机器人上的入口
├── test_offline.py            # 离线自测（不连真机，Mock RPC）
├── services/
│   ├── a2_client.py           # 异步 HTTP RPC 客户端 + ConfidenceGate 安全门
│   ├── asr.py                 # 可插拔 ASR（OpenAI兼容 / faster-whisper）
│   └── tts.py                 # A2 PlayTTS / StopTTS 封装
├── tools/                     # 「工具」= 一次原子操作 = 一个 HTTP RPC
│   ├── light.py               # set_status_light（你的 curl #3，等待控制）
│   ├── task_engine.py         # migrate_to_auto / set_current_task / launch_task
│   └── registry.py            # 用 FunctionSchema 注册给 LLM
├── skills/                    # 「技能」= 多个工具 + 业务逻辑 + 状态
│   └── launch_task.py         # ★ 由你的 voice_task.py 重构的 LangGraph 技能
└── pipeline/
    ├── ros_audio_input.py     # ROS2 音频订阅 → ASR → 转写帧
    ├── a2_tts_output.py       # 文本帧 → A2 PlayTTS，处理打断
    └── build.py               # 组装 pipeline
```

## Tool 与 Skill 的落地（对应你的需求）

**需求 2 —— 把上传的 `voice_task.py` 变成一个技能：**
原脚本「关键词命中 → 切Auto → 设当前任务 → 启动任务」被拆解为：
- 3 个原子 **Tool**（`tools/task_engine.py`）：每个对应一个 HTTP RPC。
- 1 个 **Skill**（`skills/launch_task.py`）：用 LangGraph `StateGraph`
  把三步串成带「失败即中止」分支的状态机，对 LLM 暴露为单个工具
  `launch_aimmaster_task(task_id | keyword)`。关键词映射沿用原脚本的
  `KEYWORD_TASK_MAP`。

  原脚本是「ROS唤醒词 → 直接触发」；这里改成「ASR转写 → LLM理解 →
  调用技能」，更灵活（用户不必念固定关键词，LLM 会判断意图）。
  你也可以保留原来的唤醒词直触发——把 `KEYWORD_TASK_MAP` 留着即可。

**需求 3 —— 等待控制作为一个工具：**
`tools/light.py` 的 `set_status_light` 直接封装你给的灯带 curl
（`HalRgbLightService/SetRgbLightCommand`），`waiting` 预设就是你示例里的
紫红色 `{red:180,green:0,blue:100,effect:2,control:1}`。另外提供
`wait_for_person` 工具表达「进入等待状态」。

**需求 4 —— ASR / TTS：**
- TTS 用 A2 自带 `TTSService/PlayTTS`（文档 7.5），打断用 `StopTTS`。
- ASR 可插拔：`services/asr.py`。⚠️ 你提到的「pasted 文件里的 api-key」
  **没有出现在上传目录**（uploads 为空），所以我做成了可配置：在
  `config.py` 填 `ASR_API_KEY/ASR_BASE_URL`（OpenAI 兼容），或设
  `ASR_PROVIDER=whisper` 走本地 faster-whisper。把那份文件发我，
  我替换成对应厂商的原生调用。

## 配置

编辑 `config.py` 或用环境变量：

```bash
export LLM_API_KEY=sk-...            # Anthropic key
export ASR_API_KEY=...               # 你的 ASR key
export ASR_BASE_URL=...              # OpenAI 兼容网关地址
export A2_HOST=192.168.100.110       # 主控（TTS/任务/系统）
export A2_LIGHT_HOST=192.168.100.100 # 灯带服务
```

## 运行

离线验证逻辑（不需要机器人 / ROS）：
```bash
pip install -r requirements.txt
python -m test_offline
```

机器人上运行：
```bash
pip install prebuilt/a2_aimdk-2.0.1-py3-none-any.whl
source prebuilt/ros2_plugin_proto_aarch64/share/ros2_plugin_proto/local_setup.bash
python -m main
```

## 扩展：再加一个 A2 能力

1. 在 `tools/` 写一个原子 Tool（一个 HTTP RPC）。
2. 在 `tools/registry.py` 加 `FunctionSchema` + handler + `register_function`。
3. 多步业务逻辑 → 在 `skills/` 写一个 LangGraph `StateGraph` 技能，
   handler 里调它。

## 安全兜底

`ConfidenceGate`（`services/a2_client.py`）对会触发物理移动的工具
（如 `launch_aimmaster_task`）做置信度检查，低于阈值拦截。阈值/开关在
`ConfidenceGate(min_confidence=...)`。长时任务（导航）建议注册时设
`cancel_on_interruption=False`，让执行期间仍可对话。
