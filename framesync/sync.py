import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from .protocol import MAX_TEXT_BYTES, ClipboardUpdate

logger = logging.getLogger("framesync")


class SyncState:
    def __init__(
        self,
        read_clipboard: Callable[[], Awaitable[str | None]],
        write_clipboard: Callable[[str], Awaitable[None]],
    ) -> None:
        self.origin = str(uuid.uuid4())
        self.clock = 0
        self.current: ClipboardUpdate | None = None
        self.read_clipboard = read_clipboard
        self.write_clipboard = write_clipboard
        self.peer: "Peer | None" = None

    def _next_update(self, text: str) -> ClipboardUpdate:
        encoded = text.encode("utf-8")
        if len(encoded) > MAX_TEXT_BYTES:
            raise ValueError(f"clipboard text exceeds {MAX_TEXT_BYTES} bytes")
        self.clock = max(time.time_ns(), self.clock + 1)
        update = ClipboardUpdate(self.clock, self.origin, text)
        self.current = update
        return update

    async def local_change(self, text: str) -> None:
        if self.current is not None and text == self.current.text:
            return
        try:
            update = self._next_update(text)
        except (UnicodeEncodeError, ValueError) as error:
            logger.warning("Ignoring unsupported clipboard text: %s", error)
            return
        logger.info("Local clipboard changed (%d UTF-8 bytes)", len(text.encode("utf-8")))
        if self.peer is not None:
            await self.peer.send(update)

    async def publish_snapshot(self) -> None:
        text = await self.read_clipboard()
        if text is None:
            logger.info("Clipboard has no text to synchronize")
            return
        try:
            update = self._next_update(text)
        except (UnicodeEncodeError, ValueError) as error:
            logger.warning("Ignoring unsupported clipboard snapshot: %s", error)
            return
        if self.peer is not None:
            await self.peer.send(update)

    async def receive(self, update: ClipboardUpdate) -> None:
        if update.origin == self.origin:
            return
        current_order = (
            (self.current.timestamp_ns, self.current.origin)
            if self.current is not None
            else (-1, "")
        )
        if (update.timestamp_ns, update.origin) <= current_order:
            return
        previous_text = self.current.text if self.current is not None else None
        self.current = update
        self.clock = max(self.clock, update.timestamp_ns) + 1
        if update.text != previous_text:
            await self.write_clipboard(update.text)
            logger.info(
                "Applied remote clipboard update (%d UTF-8 bytes)",
                len(update.text.encode("utf-8")),
            )


class Peer:
    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        state: SyncState,
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.state = state
        self._write_lock = asyncio.Lock()

    async def send(self, update: ClipboardUpdate) -> None:
        from .protocol import send_message

        async with self._write_lock:
            await send_message(self.writer, update.to_message())

    async def run(self) -> None:
        from .protocol import read_message

        await self.state.publish_snapshot()
        while True:
            message = await read_message(self.reader)
            await self.state.receive(ClipboardUpdate.from_message(message))

    async def close(self) -> None:
        self.writer.close()
        try:
            await self.writer.wait_closed()
        except OSError:
            logger.debug("Peer socket was already closed")
