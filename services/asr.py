"""
ASR 服务 —— 可插拔。

两种实现：
  1) "openai": 走 OpenAI 兼容的 /audio/transcriptions 接口
              （阿里百炼、硅基流动、讯飞兼容网关、OpenAI 等都可用，填 base_url + key 即可）
  2) "whisper": 本地 faster-whisper，断网可用，ORIN 上 small/base 约 300–600ms
  3) "funasr":  阿里云 FunASR 实时识别（dashscope SDK），需 ASR_API_KEY

输入是 16kHz/16bit/单声道 PCM（A2 麦克风 VAD END 后的整段）。
输出 (text, confidence)。

⚠️ 关于你说的「pasted 文件里的 api-key」：上传目录 /mnt/user-data/uploads 为空，
   我没有拿到那份文件。请把 key 填到 config.ASR_API_KEY，或用环境变量 ASR_API_KEY 注入。
   如果你的 ASR 是某家特定厂商（讯飞/阿里/百度等），告诉我，我替换成对应的原生 SDK 调用。
"""

import io
import logging
import struct
import wave
from typing import Optional, Tuple

import config

log = logging.getLogger("a2.asr")


def _pcm_to_wav_bytes(pcm: bytes, sample_rate: int = 16000, channels: int = 1) -> bytes:
    """把裸 PCM 包成带 44 字节头的 WAV，喂给云 ASR。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)  # 16bit
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


class BaseASR:
    async def transcribe(self, pcm: bytes) -> Tuple[str, Optional[float]]:
        raise NotImplementedError


class OpenAICompatibleASR(BaseASR):
    """走 OpenAI 兼容的转写接口。用 aiohttp 异步上传 WAV。"""

    def __init__(self):
        self.base_url = config.ASR_BASE_URL.rstrip("/")
        self.api_key = config.ASR_API_KEY
        self.model = config.ASR_MODEL
        self.language = config.ASR_LANGUAGE

    async def transcribe(self, pcm: bytes) -> Tuple[str, Optional[float]]:
        import aiohttp

        wav = _pcm_to_wav_bytes(pcm, config.AUDIO_SAMPLE_RATE, config.AUDIO_CHANNELS)
        url = f"{self.base_url}/audio/transcriptions"
        headers = {"Authorization": f"Bearer {self.api_key}"}

        form = aiohttp.FormData()
        form.add_field("file", wav, filename="audio.wav", content_type="audio/wav")
        form.add_field("model", self.model)
        form.add_field("language", self.language)
        form.add_field("response_format", "json")

        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(url, data=form, headers=headers, timeout=15) as r:
                    data = await r.json(content_type=None)
                    text = (data or {}).get("text", "").strip()
                    log.info("ASR(cloud) -> 「%s」", text)
                    # 多数兼容接口不返回 confidence，置 None 让 ConfidenceGate 走默认逻辑
                    return text, None
        except Exception as e:
            log.error("云 ASR 失败: %s", e)
            return "", None


class FasterWhisperASR(BaseASR):
    """本地 faster-whisper。首次使用时懒加载模型。"""

    def __init__(self):
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel  # 延迟导入

            log.info("加载 faster-whisper: %s on %s", config.WHISPER_MODEL_SIZE, config.WHISPER_DEVICE)
            self._model = WhisperModel(
                config.WHISPER_MODEL_SIZE,
                device=config.WHISPER_DEVICE,
                compute_type=config.WHISPER_COMPUTE,
            )

    async def transcribe(self, pcm: bytes) -> Tuple[str, Optional[float]]:
        import asyncio
        import numpy as np

        self._ensure_model()
        # PCM16 -> float32 [-1,1]
        audio = np.frombuffer(pcm, dtype=np.int16).astype("float32") / 32768.0

        def _run():
            segments, _info = self._model.transcribe(
                audio, language=config.ASR_LANGUAGE, beam_size=5
            )
            segs = list(segments)
            text = "".join(s.text for s in segs).strip()
            # avg_logprob -> 粗略置信度
            if segs:
                import math
                avg = sum(s.avg_logprob for s in segs) / len(segs)
                conf = max(0.0, min(1.0, math.exp(avg)))
            else:
                conf = None
            return text, conf

        text, conf = await asyncio.get_event_loop().run_in_executor(None, _run)
        log.info("ASR(local) -> 「%s」 conf=%s", text, conf)
        return text, conf


class FunASR(BaseASR):
    """
    阿里云 FunASR 实时识别（dashscope SDK）。
    支持 funasr-realtime-2026-02-28 等实时模型。

    用法：ASR_PROVIDER=funasr，ASR_API_KEY 填 dashscope key。
    """

    def __init__(self):
        import dashscope
        dashscope.api_key = config.ASR_API_KEY
        self.model = config.FUNASR_MODEL
        self.lang_hints = config.FUNASR_LANGUAGE_HINTS.split(",")

    async def transcribe(self, pcm: bytes) -> Tuple[str, Optional[float]]:
        import asyncio, io, wave, os

        # PCM16 -> WAV 文件（dashscope 需要文件路径，不接受 bytes）
        wav_buf = io.BytesIO()
        with wave.open(wav_buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(config.AUDIO_SAMPLE_RATE)
            wf.writeframes(pcm)
        wav_buf.seek(0)

        tmp_path = "/tmp/funasr_input.wav"
        with open(tmp_path, "wb") as f:
            f.write(wav_buf.read())

        try:
            from dashscope.audio.asr import Recognition

            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: Recognition(
                    model=self.model,
                    format="wav",
                    sample_rate=16000,
                    language_hints=self.lang_hints,
                    callback=None,
                ).call(tmp_path),
            )
            os.unlink(tmp_path)

            if result.status_code == 200:
                sentences = result.get_sentence()
                if sentences and len(sentences) > 0:
                    text = sentences[0].get("text", "").strip()
                    log.info("ASR(funasr) -> 「%s」", text)
                    return text, None
            return "", None
        except Exception as e:
            log.error("FunASR 识别异常: %s", e)
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            return "", None


def build_asr() -> BaseASR:
    if config.ASR_PROVIDER == "whisper":
        return FasterWhisperASR()
    if config.ASR_PROVIDER == "funasr":
        return FunASR()
    return OpenAICompatibleASR()
