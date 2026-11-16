import asyncio
import logging

from . import desktop_clipboard, x11_clipboard
from .sync import SyncState

logger = logging.getLogger("framesync")


class FrameClipboard:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.observed: dict[str, str] = {}

    async def read_text(self) -> str | None:
        async with self.lock:
            try:
                text = await desktop_clipboard.read_text()
                if text:
                    return text
            except (OSError, RuntimeError, ValueError) as error:
                logger.warning("Could not read desktop clipboard: %s", error)
            return await x11_clipboard.read_text()

    async def _write(self, source: str, text: str) -> None:
        try:
            if source == "desktop":
                if not await desktop_clipboard.set_text(text):
                    self.observed.pop(source, None)
                    return
            else:
                await x11_clipboard.set_text(text)
            self.observed[source] = text
        except (OSError, RuntimeError, ValueError) as error:
            logger.warning("Could not write %s clipboard: %s", source, error)

    async def set_text(self, text: str) -> None:
        async with self.lock:
            await self._write("x11", text)
            await self._write("desktop", text)

    async def watch(self, state: SyncState) -> None:
        sources = (("desktop", desktop_clipboard.read_text), ("x11", x11_clipboard.read_text))
        while True:
            async with self.lock:
                for source, read in sources:
                    try:
                        text = await read()
                    except (OSError, RuntimeError, UnicodeDecodeError, ValueError) as error:
                        logger.warning("Could not read %s clipboard: %s", source, error)
                        continue
                    if text is None:
                        self.observed.pop(source, None)
                        continue
                    if self.observed.get(source) == text:
                        continue
                    first_observation = source not in self.observed
                    self.observed[source] = text
                    if first_observation and not text and state.current is not None:
                        await self._write(source, state.current.text)
                        continue
                    await state.local_change(text)
                    await self._write("x11" if source == "desktop" else "desktop", text)
            await asyncio.sleep(0.5)