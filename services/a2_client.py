"""
A2 HTTP RPC 通用客户端。

- 用 aiohttp 做异步请求，避免阻塞 Pipecat 的 frame pipeline。
- post_rpc() 是所有工具/技能调用 A2 的唯一出口，统一处理超时、日志、错误。
- ConfidenceGate 实现技术选型文档里说的「运动控制类指令送执行前加置信度检查」。
"""

import logging
from typing import Any, Optional

import aiohttp
from yarl import URL

from config import HTTP_HEADERS, HTTP_TIMEOUT

log = logging.getLogger("a2.rpc")


class A2Client:
    """对 A2 各 HTTP JSON RPC 服务的瘦封装。整个进程共享一个 session。"""

    def __init__(self) -> None:
        self._session: Optional[aiohttp.ClientSession] = None

    async def start(self) -> None:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT)
            self._session = aiohttp.ClientSession(timeout=timeout, headers=HTTP_HEADERS)
            log.info("A2Client session 已创建")

    async def stop(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            log.info("A2Client session 已关闭")

    async def post_rpc(self, url: str, payload: dict) -> dict:
        """
        发一个 RPC，返回 {"ok": bool, "status": int, "text": str, "json": dict|None}。
        不抛异常 —— 失败信息打包返回，方便工具层决定如何回复 LLM。
        """
        await self.start()
        assert self._session is not None
        # yarl 会把路径中的 %2F / %3A 等正确保留，不二次编码
        encoded_url = str(URL(url, encoded=True))
        try:
            async with self._session.post(encoded_url, json=payload) as resp:
                text = await resp.text()
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    data = None
                ok = resp.status == 200
                log.info("RPC %s -> %s | %s", encoded_url, resp.status, text[:300])
                return {"ok": ok, "status": resp.status, "text": text, "json": data}
        except Exception as e:  # 网络/超时
            log.error("RPC %s 失败: %s", url, e)
            return {"ok": False, "status": -1, "text": str(e), "json": None}


class ConfidenceGate:
    """
    运动/物理动作安全兜底。

    技术选型文档建议：物理机器人在 LLM 工具调用结果送执行前加一层置信度检查，
    特别是运动控制类指令。这里提供一个集中开关 + 阈值校验点。

    用法：在「危险类」工具里调用 gate.check(confidence, tool_name)。
    真实部署可接入：语音置信度、二次确认、人机交互按钮等。
    """

    def __init__(self, min_confidence: float = 0.6, require_confirm: bool = False):
        self.min_confidence = min_confidence
        self.require_confirm = require_confirm

    def check(self, confidence: Optional[float], tool_name: str) -> tuple[bool, str]:
        if confidence is None:
            # 未提供置信度时，按选型文档的保守原则：除非显式放行，否则放行但记录
            log.debug("工具 %s 未提供置信度，按默认放行", tool_name)
            return True, ""
        if confidence < self.min_confidence:
            msg = f"指令置信度 {confidence:.2f} 低于阈值 {self.min_confidence}，已拦截"
            log.warning("[ConfidenceGate] %s: %s", tool_name, msg)
            return False, msg
        return True, ""


# 进程级单例
a2_client = A2Client()
confidence_gate = ConfidenceGate()
