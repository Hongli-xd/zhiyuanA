"""
终端文本输入处理器。

绕过 ASR，通过监控 /a2_input.txt 接收文字输入，
等价位 ASR 识别结果并入 pipeline。

用法：
    echo "往前走两步" > /a2_input.txt
    每行一个命令，写入即发送。
"""

import asyncio
import logging
import os
import time

from pipecat.frames.frames import TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

log = logging.getLogger("a2.terminal_input")

INPUT_FILE = "../a2_input.txt"


class TerminalTextInput(FrameProcessor):
    """
    监控 INPUT_FILE，每行文字生成 TranscriptionFrame 注入 pipeline。
    不读写主 terminal，不干扰任何日志输出。
    """

    def __init__(self):
        super().__init__()
        self._running = True
        self._loop = None
        self._last_mtime = 0.0
        # 如果文件已存在，记录初始修改时间
        if os.path.exists(INPUT_FILE):
            self._last_mtime = os.path.getmtime(INPUT_FILE)
        log.info("TerminalTextInput 已启动，输入文件: %s（另一个终端: echo '指令' > %s）",
                 INPUT_FILE, INPUT_FILE)

    async def _file_watch_loop(self):
        """监控文件变化，检测到新行立即推帧。"""
        while self._running:
            try:
                if os.path.exists(INPUT_FILE):
                    mtime = os.path.getmtime(INPUT_FILE)
                    if mtime != self._last_mtime:
                        self._last_mtime = mtime
                        with open(INPUT_FILE, "r") as f:
                            lines = f.readlines()
                        for line in lines:
                            text = line.strip()
                            if not text:
                                continue
                            ts = time.strftime("%Y-%m-%dT%H:%M:%S")
                            frame = TranscriptionFrame(text=text, user_id="user", timestamp=ts)
                            await self.push_frame(frame, FrameDirection.DOWNSTREAM)
                            log.info("[终端输入] -> 「%s」", text)
                        # 清空文件，避免重复发送
                        with open(INPUT_FILE, "w") as f:
                            f.write("")
            except Exception as e:
                log.error("TerminalTextInput 文件监控异常: %s", e)
            await asyncio.sleep(0.1)

    def start_watch(self, loop: asyncio.AbstractEventLoop):
        """在指定事件循环中启动文件监控协程。"""
        self._loop = loop
        asyncio.run_coroutine_threadsafe(self._file_watch_loop(), loop)

    def shutdown(self):
        self._running = False


